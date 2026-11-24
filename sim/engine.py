import random

from sim.models import (
    CriticalSection,
    Job,
    MissCause,
    Mutex,
    OverloadPolicy,
    SimulationResult,
    Task,
    TickCategory,
    TickRecord,
)
from sim.schedulers import (
    assign_priorities_and_levels,
    select_next_job,
)


def job_identifier(job: Job) -> str:
    """Generate a unique identifier for a job."""
    return f"{job.task_id}_{job.job_index}"


class DefaultProtocolHandler:
    """
    Basic mutex implementation used when the real protocol
    implementation is unavailable.

    This handler provides exclusive resource ownership,
    FIFO waiting and ownership transfer.
    """

    def system_ceiling(self) -> int:
        return 0

    def try_acquire(
        self,
        job: Job,
        resource_id: str,
        mutexes: dict[str, Mutex],
    ) -> bool:

        mutex = mutexes.get(resource_id)

        if mutex is None:
            raise ValueError(f"Unknown resource: {resource_id}")

        if mutex.owner is None:
            mutex.owner = job

            if resource_id not in job.held_resources:
                job.held_resources.append(resource_id)

            job.blocked_on = None
            return True

        if mutex.owner is job:
            return True

        if job not in mutex.wait_queue:
            mutex.wait_queue.append(job)

        job.blocked_on = resource_id
        return False

    def release(
        self,
        job: Job,
        resource_id: str,
        mutexes: dict[str, Mutex],
    ) -> None:

        mutex = mutexes.get(resource_id)

        if mutex is None:
            return

        if mutex.owner is not job:
            return

        mutex.owner = None

        if resource_id in job.held_resources:
            job.held_resources.remove(resource_id)

        # Transfer ownership to the first waiting job.
        while mutex.wait_queue:
            next_job = mutex.wait_queue.pop(0)

            if next_job.remaining_exec <= 0:
                next_job.blocked_on = None
                continue

            mutex.owner = next_job

            if resource_id not in next_job.held_resources:
                next_job.held_resources.append(resource_id)

            next_job.blocked_on = None
            break

    def remove_from_wait_queues(
        self,
        job: Job,
        mutexes: dict[str, Mutex],
    ) -> None:

        for mutex in mutexes.values():
            if job in mutex.wait_queue:
                mutex.wait_queue.remove(job)

        job.blocked_on = None


class DefaultAttributionHandler:
    """
    Lightweight attribution fallback.

    The real attribution module can replace this handler
    through dependency injection.
    """

    def record_tick(
        self,
        tick: int,
        active_jobs: list[Job],
        running_job: Job | None,
        is_overhead: bool,
        system_ceiling: int,
    ) -> TickRecord:

        job_states = {}

        for job in active_jobs:

            if job is running_job:

                if is_overhead:
                    category = TickCategory.OVERHEAD
                else:
                    category = TickCategory.RUNNING

            else:
                category = TickCategory.INTERFERENCE

            job.tick_counts[category] += 1

            job_states[job_identifier(job)] = category

        return TickRecord(
            tick=tick,
            running_job_id=(
                job_identifier(running_job)
                if running_job is not None and not is_overhead
                else None
            ),
            running_task_id=(
                running_job.task_id
                if running_job is not None and not is_overhead
                else None
            ),
            is_overhead=is_overhead,
            job_states=job_states,
            system_ceiling=system_ceiling,
        )


def _snapshot_priorities(jobs: list[Job]) -> dict[int, tuple[int, int]]:
    """Capture effective priorities and preemption levels."""
    return {
        id(job): (job.effective_priority, job.preemption_level)
        for job in jobs
    }


def _count_priority_changes(
    jobs: list[Job],
    before: dict[int, tuple[int, int]],
) -> int:
    """Count jobs whose effective priority or level changed."""
    return sum(
        1
        for job in jobs
        if id(job) in before
        and before[id(job)]
        != (job.effective_priority, job.preemption_level)
    )

def _load_optional_handler(module_name, factory_name, config):
    """Load a handler if the module exposes its agreed factory."""
    from importlib import import_module

    try:
        module = import_module(module_name)
    except ImportError:
        return None

    factory = getattr(module, factory_name, None)

    if factory is None:
        return None

    return factory(config)

class Simulator:
    def __init__(
        self,
        config,
        protocol_handler=None,
        attribution_handler=None,
        exec_time_fn=None,
    ):
        self.config = config
        self.overhead_ticks = 0

        # Initialize protocol handler:
        # 1. Use explicitly injected handler if provided.
        # 2. Otherwise, try loading the real protocol implementation.
        # 3. Fall back to the default implementation.
        self.protocol_handler = (
            protocol_handler
            or _load_optional_handler(
                "sim.protocols",
                "create_protocol_handler",
                config,
            )
            or DefaultProtocolHandler()
        )

        # Initialize attribution handler using the same approach.
        self.attribution_handler = (
            attribution_handler
            or _load_optional_handler(
                "sim.attribution",
                "create_attribution_handler",
                config,
            )
            or DefaultAttributionHandler()
        )

        # Execution-time function.
        # Defaults to each task's WCET (C).
        self.exec_time_fn = exec_time_fn or (
            lambda task, job_index, rng: task.C
        )

        # Random generator for deterministic execution-time variation.
        self.rng = random.Random(config.seed)

        # Assign base priorities and preemption levels.
        self.priority_table = assign_priorities_and_levels(
            config.tasks,
            config.scheduler,
        )

        # Initialize task lookup.
        self.tasks_by_id = {
            task.id: task
            for task in config.tasks
        }

        # Create mutexes from critical sections.
        self.mutexes = {}

        for task in config.tasks:
            for section in task.sections:
                if section.resource_id not in self.mutexes:
                    self.mutexes[section.resource_id] = Mutex(
                    id=section.resource_id
            )

        # Active jobs currently in the simulation.
        self.active_jobs = []

        # All jobs released during the simulation.
        self.all_jobs = []

        # Track the next job index for each task.
        self.job_indices = {
            task.task_id: 0
            for task in config.tasks
        }

        # Previous running job, used for context-switch detection.
        self.previous_running_job = None

        # Simulation trace and statistics.
        self.trace = []
        self.lock_ops = 0
        self.priority_changes = 0
        self.context_switches = 0

    def _create_mutexes(self) -> dict[str, Mutex]:
        """Create mutexes referenced by task critical sections."""

        resource_ids = {
            section.resource_id
            for task in self.config.tasks
            for section in task.sections
        }

        mutexes = {
            resource_id: Mutex(id=resource_id)
            for resource_id in sorted(resource_ids)
        }

        # Assign resource ceilings for protocols using them.
        for resource_id, mutex in mutexes.items():

            users = [
                self.priorities[task.id][0]
                for task in self.config.tasks
                if any(
                    section.resource_id == resource_id
                    for section in task.sections
                )
            ]

            if users:
                mutex.ceiling = max(users)

        return mutexes

    def _release_new_jobs(self, tick: int) -> None:
        """Release periodic jobs at their exact release times."""

        for task in self.config.tasks:

            if tick < task.phase:
                continue

            if (tick - task.phase) % task.T != 0:
                continue

            job_index = self.job_indices[task.id]
            self.job_indices[task.id] += 1

            actual_exec = self.exec_time_fn(
                task,
                job_index,
                self.rng,
            )

            if (
                isinstance(actual_exec, bool)
                or not isinstance(actual_exec, int)
                or actual_exec <= 0
            ):
                raise ValueError(
                    f"Invalid execution time for task {task.id}"
                )

            base_priority, preemption_level = (
                self.priority_table[task.id]
            )

            job = Job(
                task_id=task.id,
                job_index=job_index,
                release_time=tick,
                abs_deadline=tick + task.D,
                wcet=task.C,
                actual_exec=actual_exec,
                remaining_exec=actual_exec,
                executed=0,
                base_priority=base_priority,
                effective_priority=base_priority,
                preemption_level=preemption_level,
                held_resources=[],
                blocked_on=None,
            )

            self.active_jobs.append(job)
            self.all_jobs.append(job)

    def _ready_jobs(self) -> list[Job]:
        """Return jobs that are ready to execute."""

        return [
            job
            for job in self.active_jobs
            if job.blocked_on is None
            and job.remaining_exec > 0
        ]

    def _find_critical_section(
        self,
        job: Job,
    ) -> CriticalSection | None:
        """Find a critical section beginning at current execution offset."""

        for section in self._task_for_job(job).sections:

            if section.offset != job.executed:
                continue

            if section.resource_id in job.held_resources:
                continue

            return section

        return None

    def _task_for_job(self, job: Job) -> Task:
        """Find the task definition associated with a job."""

        for task in self.config.tasks:
            if task.id == job.task_id:
                return task

        raise ValueError(f"Unknown task: {job.task_id}")

    def _schedule_and_acquire(self, previous_running_job: Job | None):
        """
        Repeatedly select a job and attempt lock acquisition.

        A blocked job is removed from the ready set, and
        scheduling is repeated within the same tick.
        """

        while True:

            ready_jobs = self._ready_jobs()

            current_running = (
                previous_running_job
                if previous_running_job in ready_jobs
                else None
            )

            selected_job = select_next_job(
                ready_jobs=ready_jobs,
                running_job=current_running,
                scheduler=self.config.scheduler,
                protocol=self.config.protocol,
                system_ceiling=self.protocol_handler.system_ceiling(),
            )

            if selected_job is None:
                return None

            section = self._find_critical_section(selected_job)

            if section is None:
                return selected_job

            before = _snapshot_priorities(self.active_jobs)

            acquired = self.protocol_handler.try_acquire(
                selected_job,
                section.resource_id,
                self.mutexes,
            )

            self.priority_changes += _count_priority_changes(
                self.active_jobs,
                before,
            )

            if acquired:
                self.lock_ops += 1
                continue

            # The job is blocked. Re-select within this tick.
            selected_job.blocked_on = section.resource_id

    def _release_resource(self, job: Job, resource_id: str) -> None:
        """Release a mutex and account for protocol priority changes."""

        before = _snapshot_priorities(self.active_jobs)

        self.protocol_handler.release(
            job,
            resource_id,
            self.mutexes,
        )

        self.lock_ops += 1

        self.priority_changes += _count_priority_changes(
            self.active_jobs,
            before,
        )

    def _release_all_resources(self, job: Job) -> None:
        """Defensively release all resources held by a job."""

        for resource_id in list(job.held_resources):
            self._release_resource(job, resource_id)

        # A terminated or aborted job must not remain in a wait queue.
        if hasattr(self.protocol_handler, "remove_from_wait_queues"):
            self.protocol_handler.remove_from_wait_queues(
                job,
                self.mutexes,
            )
        else:
            for mutex in self.mutexes.values():
                if job in mutex.wait_queue:
                    mutex.wait_queue.remove(job)

        job.blocked_on = None

    def _handle_execution_completion(
        self,
        job: Job,
        tick: int,
    ) -> None:
        """Release locks at critical-section boundaries and finish jobs."""

        task = self._task_for_job(job)

        for section in task.sections:

            if (
                section.offset + section.length == job.executed
                and section.resource_id in job.held_resources
            ):
                self._release_resource(job, section.resource_id)

        if job.remaining_exec == 0:

            # Defensive cleanup for short jittered execution.
            self._release_all_resources(job)

            job.finish_time = tick + 1

            if job in self.active_jobs:
                self.active_jobs.remove(job)

    def _check_deadlines(self, tick: int) -> None:
        """Mark deadline misses and apply the overload policy."""

        for job in list(self.active_jobs):

            if tick + 1 < job.abs_deadline:
                continue

            if not job.missed:
                job.missed = True

                # The engine marks the miss once.
                # Detailed causal classification belongs to
                # the attribution module.
                if job.miss_cause is None:
                    job.miss_cause = (
                        MissCause.OVERRUN_OR_OVERHEAD_MISS
                    )

            if self.config.overload_policy == OverloadPolicy.ABORT:

                self._release_all_resources(job)

                if job in self.active_jobs:
                    self.active_jobs.remove(job)

    def run(self) -> SimulationResult:
        """Execute the complete deterministic simulation."""

        previous_running_job = None
        overhead_remaining = 0

        for tick in range(self.config.duration):

            # 1. RELEASE

            self._release_new_jobs(tick)

            # 2. SCHEDULE AND LOCK ACQUISITION

            selected_job = self._schedule_and_acquire(
                previous_running_job
            )

            executed_job = None
            is_overhead = False

            # 3. CONTEXT SWITCH / OVERHEAD

            if selected_job is None:

                previous_running_job = None
                overhead_remaining = 0

            else:
                if (
                    selected_job is not previous_running_job
                    and previous_running_job is not None
                ):
                    self.context_switches += 1
                    overhead_remaining = (
                        self.config.overhead_epsilon
                    )

                if overhead_remaining > 0:

                    is_overhead = True
                    overhead_remaining -= 1
                    self.overhead_ticks += 1

                else:

                    selected_job.executed += 1
                    selected_job.remaining_exec -= 1

                    executed_job = selected_job

                # Preserve dispatch identity even during overhead.
                previous_running_job = selected_job

            # 4. PER-TICK ATTRIBUTION

            self.trace.append(
                self.attribution_handler.record_tick(
                    tick=tick,
                    active_jobs=list(self.active_jobs),
                    running_job=(
                        selected_job if is_overhead else executed_job
                    ),
                    is_overhead=is_overhead,
                    system_ceiling=(
                        self.protocol_handler.system_ceiling()
                    ),
                )
            )

            # 5. UNLOCK AND COMPLETE

            if executed_job is not None:

                self._handle_execution_completion(
                    executed_job,
                    tick,
                )

            # 6. DEADLINE CHECK

            self._check_deadlines(tick)

        return SimulationResult(
            config=self.config,
            jobs=self.all_jobs,
            trace=self.trace,
            context_switches=self.context_switches,
            lock_ops=self.lock_ops,
            priority_changes=self.priority_changes,
            overhead_ticks=self.overhead_ticks,
        )
