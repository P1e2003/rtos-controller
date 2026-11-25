import random

from sim.models import (
    CriticalSection,
    Job,
    Mutex,
    OverloadPolicy,
    ScenarioConfig,
    SchedulerType,
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
    Basic NONE mutex implementation used when sim.protocols
    is unavailable.

    Clears ownership on release and wakes all waiters so the
    scheduler dispatches the highest-priority ready job next.
    """

    def system_ceiling(self, mutexes: dict[str, Mutex]) -> int:
        return 0

    def try_acquire(
        self,
        job: Job,
        resource_id: str,
        mutexes: dict[str, Mutex],
    ) -> tuple[bool, int]:
        mutex = mutexes.get(resource_id)

        if mutex is None:
            raise ValueError(f"Unknown resource: {resource_id}")

        if mutex.owner is None:
            mutex.owner = job

            if resource_id not in job.held_resources:
                job.held_resources.append(resource_id)

            job.blocked_on = None
            return True, 0

        if mutex.owner is job:
            return True, 0

        if job not in mutex.wait_queue:
            mutex.wait_queue.append(job)

        job.blocked_on = resource_id
        return False, 0

    def release(
        self,
        job: Job,
        resource_id: str,
        mutexes: dict[str, Mutex],
    ) -> int:
        mutex = mutexes.get(resource_id)

        if mutex is None or mutex.owner is not job:
            return 0

        mutex.owner = None

        if resource_id in job.held_resources:
            job.held_resources.remove(resource_id)

        # Wake waiters without directly transferring ownership so the
        # scheduler selects the highest-priority ready job on next dispatch.
        for waiter in mutex.wait_queue:
            if waiter.blocked_on == resource_id:
                waiter.blocked_on = None

        mutex.wait_queue.clear()
        return 0

    def on_job_abort(
        self,
        job: Job,
        mutexes: dict[str, Mutex],
    ) -> int:
        for resource_id in list(job.held_resources):
            self.release(job, resource_id, mutexes)

        for mutex in mutexes.values():
            if job in mutex.wait_queue:
                mutex.wait_queue.remove(job)

        job.blocked_on = None
        return 0


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
        mutexes: dict[str, Mutex],
        scheduler: SchedulerType,
    ) -> TickRecord:
        job_states: dict[str, TickCategory] = {}

        for job in active_jobs:
            if is_overhead:
                category = TickCategory.OVERHEAD
            elif job is running_job:
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


def _load_optional_handler(module_name: str, factory_name: str, *args):
    """Load a handler if the module exposes its agreed factory."""
    from importlib import import_module

    try:
        module = import_module(module_name)
    except ImportError:
        return None

    factory = getattr(module, factory_name, None)

    if factory is None:
        return None

    return factory(*args)


class Simulator:
    def __init__(
        self,
        config: ScenarioConfig,
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
                config.protocol,
                config.scheduler,
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
        self.mutexes = self._create_mutexes()

        # Active jobs currently in the simulation.
        self.active_jobs = []

        # All jobs released during the simulation.
        self.all_jobs = []

        # Track the next job index for each task.
        self.job_indices = {
            task.id: 0
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
                self.priority_table[task.id][1]
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

        try:
            return self.tasks_by_id[job.task_id]
        except KeyError:
            raise ValueError(
                f"Unknown task: {job.task_id}"
            ) from None

    def _release_resource(self, job: Job, resource_id: str) -> None:
        """Release a mutex and account for protocol priority changes."""

        delta = self.protocol_handler.release(
            job,
            resource_id,
            self.mutexes,
        )

        self.lock_ops += 1
        self.priority_changes += delta

    def _release_all_resources(self, job: Job) -> None:
        """Defensively release all resources held by a job."""

        held_count = len(job.held_resources)

        delta = self.protocol_handler.on_job_abort(
            job,
            self.mutexes,
        )

        self.lock_ops += held_count
        self.priority_changes += delta
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
                # Mark miss once; detailed MissCause belongs to sim.attribution.
                job.missed = True

            if self.config.overload_policy == OverloadPolicy.ABORT:

                self._release_all_resources(job)

                if job in self.active_jobs:
                    self.active_jobs.remove(job)

    def run(self) -> SimulationResult:
        """Execute the complete deterministic simulation."""

        previous_running_job: Job | None = None
        overhead_remaining = 0

        for tick in range(self.config.duration):

            # 1. RELEASE

            self._release_new_jobs(tick)

            # 2 & 3. SCHEDULE, OVERHEAD, AND LOCK ACQUISITION

            executed_job: Job | None = None
            is_overhead = False

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
                    system_ceiling=self.protocol_handler.system_ceiling(
                        self.mutexes
                    ),
                )

                if selected_job is None:
                    previous_running_job = None
                    overhead_remaining = 0
                    break

                # Consume context-switch overhead before acquiring new locks
                # so a newly switched-in job never holds a mutex at executed=0.
                if (
                    selected_job is not previous_running_job
                    and previous_running_job is not None
                    and self.config.overhead_epsilon > 0
                ):
                    self.context_switches += 1
                    overhead_remaining = self.config.overhead_epsilon
                    previous_running_job = selected_job

                if overhead_remaining > 0:
                    is_overhead = True
                    overhead_remaining -= 1
                    self.overhead_ticks += 1
                    previous_running_job = selected_job
                    break

                section = self._find_critical_section(selected_job)

                if section is not None:
                    acquired, delta = self.protocol_handler.try_acquire(
                        selected_job,
                        section.resource_id,
                        self.mutexes,
                    )

                    self.priority_changes += delta

                    if acquired:
                        self.lock_ops += 1
                        continue

                    # Blocked on mutex: re-select a runnable job within this tick.
                    selected_job.blocked_on = section.resource_id
                    continue

                if (
                    selected_job is not previous_running_job
                    and previous_running_job is not None
                ):
                    self.context_switches += 1

                selected_job.executed += 1
                selected_job.remaining_exec -= 1
                executed_job = selected_job
                previous_running_job = selected_job
                break

            # 4. PER-TICK ATTRIBUTION

            self.trace.append(
                self.attribution_handler.record_tick(
                    tick=tick,
                    active_jobs=list(self.active_jobs),
                    running_job=None if is_overhead else executed_job,
                    is_overhead=is_overhead,
                    system_ceiling=self.protocol_handler.system_ceiling(
                        self.mutexes
                    ),
                    mutexes=self.mutexes,
                    scheduler=self.config.scheduler,
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
