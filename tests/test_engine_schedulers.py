
from sim.engine import Simulator
from sim.models import (
    CriticalSection,
    Job,
    OverloadPolicy,
    ProtocolType,
    ScenarioConfig,
    SchedulerType,
    Task,
)
from sim.schedulers import (
    assign_priorities_and_levels,
    select_next_job,
)


def make_job(
    task_id,
    job_index=0,
    deadline=10,
    priority=1,
    preemption_level=1,
    release_time=0,
    remaining_exec=3,
):
    """Helper for creating test jobs."""

    return Job(
        task_id=task_id,
        job_index=job_index,
        release_time=release_time,
        abs_deadline=deadline,
        wcet=remaining_exec,
        actual_exec=remaining_exec,
        remaining_exec=remaining_exec,
        executed=0,
        base_priority=priority,
        effective_priority=priority,
        preemption_level=preemption_level,
        held_resources=[],
    )


def make_config(
    tasks,
    scheduler=SchedulerType.RM,
    protocol=ProtocolType.NONE,
    duration=10,
    overhead_epsilon=0,
    overload_policy=OverloadPolicy.CONTINUE,
):
    return ScenarioConfig(
        name="test_scenario",
        duration=duration,
        scheduler=scheduler,
        protocol=protocol,
        tasks=tasks,
        overhead_epsilon=overhead_epsilon,
        overload_policy=overload_policy,
        seed=42,
    )


# TEST 1: RM / DM / EDF PRIORITY AND TIE BREAKING

def test_rm_dm_edf_priority_and_tie_breaking():

    tasks = [
        Task(id="C", C=1, T=20, D=20),
        Task(id="A", C=1, T=5, D=5),
        Task(id="B", C=1, T=5, D=5),
    ]

    rm = assign_priorities_and_levels(
        tasks,
        SchedulerType.RM,
    )

    dm = assign_priorities_and_levels(
        tasks,
        SchedulerType.DM,
    )

    edf = assign_priorities_and_levels(
        tasks,
        SchedulerType.EDF,
    )

    # Shorter period/deadline gets higher priority.
    assert rm["A"][0] > rm["C"][0]
    assert dm["A"][0] > dm["C"][0]

    # Alphabetical task ID breaks static priority ties.
    assert rm["A"][0] > rm["B"][0]
    assert dm["A"][0] > dm["B"][0]

    assert edf["A"][1] > edf["C"][1]

    # EDF uses earliest absolute deadline.
    jobs = [
        make_job("A", deadline=10),
        make_job("B", deadline=5),
        make_job("C", deadline=15),
    ]

    selected = select_next_job(
        ready_jobs=jobs,
        running_job=None,
        scheduler=SchedulerType.EDF,
        protocol=ProtocolType.NONE,
        system_ceiling=0,
    )

    assert selected.task_id == "B"

    # Equal deadlines: alphabetical task ID wins.
    tied_jobs = [
        make_job("B", deadline=5),
        make_job("A", deadline=5),
    ]

    selected = select_next_job(
        ready_jobs=tied_jobs,
        running_job=None,
        scheduler=SchedulerType.EDF,
        protocol=ProtocolType.NONE,
        system_ceiling=0,
    )

    assert selected.task_id == "A"


# TEST 2: NON-ZERO PHASE RELEASE TIMING

def test_non_zero_phase_release_timing():

    task = Task(
        id="A",
        C=1,
        T=3,
        D=3,
        phase=2,
    )

    simulator = Simulator(
        make_config([task], duration=10)
    )

    result = simulator.run()

    release_times = [
        job.release_time
        for job in result.jobs
    ]

    assert release_times == [2, 5, 8]

    assert all(
        release >= task.phase
        for release in release_times
    )


# TEST 3: LOCK ACQUISITION AND RELEASE TIMING

def test_lock_acquire_only_when_scheduled_and_unlock_after_final_tick():

    task = Task(
        id="A",
        C=3,
        T=10,
        D=10,
        sections=(
            __import__(
                "sim.models",
                fromlist=["CriticalSection"],
            ).CriticalSection(
                offset=0,
                length=2,
                resource_id="S",
            ),
        ),
    )

    simulator = Simulator(
        make_config([task], duration=2)
    )

    result = simulator.run()

    # One acquire and one release.
    assert result.lock_ops == 2

    # Resource must be unlocked after the second
    # execution tick (offset 0 + length 2).
    assert simulator.mutexes["S"].owner is None

    assert result.jobs[0].remaining_exec == 1


# TEST 4: OVERHEAD DOES NOT REDUCE EXECUTION TIME

def test_overhead_tick_does_not_decrement_remaining_exec():

    task_a = Task(
        id="A",
        C=1,
        T=10,
        D=10,
    )

    task_b = Task(
        id="B",
        C=3,
        T=10,
        D=10,
        base_priority=0,
        phase=0,
    )

    simulator = Simulator(
        make_config(
            [task_a, task_b],
            duration=2,
            overhead_epsilon=1,
        )
    )

    result = simulator.run()

    job_b = next(
        job for job in result.jobs
        if job.task_id == "B"
    )

    # Tick 0: A executes.
    # Tick 1: Switch to B, consuming overhead.
    assert result.overhead_ticks == 1

    # B has not executed yet.
    assert job_b.remaining_exec == 3
    assert job_b.executed == 0


# TEST 5: DEADLINE MISS RECORDED ONCE

def test_deadline_miss_recorded_once_under_continue_policy():

    task = Task(
        id="A",
        C=1,
        T=10,
        D=1,
    )

    simulator = Simulator(
        make_config(
            [task],
            duration=4,
        ),
        exec_time_fn=lambda task, job_index, rng: 3,
    )

    result = simulator.run()

    job = result.jobs[0]

    assert job.missed is True

    # Job continues until execution completes.
    assert job.remaining_exec == 0

    assert job.finish_time == 3

    # The job remains in results only once.
    assert len(result.jobs) == 1

    # Completion must not leave any mutex held.
    assert job.held_resources == []


# TEST 6: ABORT POLICY RELEASES HELD MUTEX

def test_abort_policy_releases_held_mutex():


    def execution_time(task, job_index, rng):
        if task.id == "A":
            return 6
        return task.C

    task_a = Task(
        id="A",
        C=5,
        T=10,
        D=5,
        sections=(
            CriticalSection(
                offset=0,
                length=5,
                resource_id="S",
            ),
        ),
    )

    task_b = Task(
        id="B",
        C=1,
        T=10,
        D=10,
        phase=1,
        sections=(
            CriticalSection(
                offset=0,
                length=1,
                resource_id="S",
            ),
        ),
    )

    simulator = Simulator(
        make_config(
            [task_a, task_b],
            duration=8,
            overhead_epsilon=1,
            overload_policy=OverloadPolicy.ABORT,
        ),
        exec_time_fn=execution_time,
    )

    result = simulator.run()

    job_a = next(
        job for job in result.jobs
        if job.task_id == "A"
    )

    job_b = next(
        job for job in result.jobs
        if job.task_id == "B"
    )

    # A acquires the mutex first.
    # B later waits for it.
    # A misses its deadline and is aborted.
    # The mutex is transferred to B.
    # B should have completed its critical section.
    assert job_a.missed is True
    
    assert job_a.remaining_exec > 0

    assert job_b.missed is False
    
    assert job_b.executed == 1
    
    assert job_b.finish_time is not None

    assert simulator.mutexes["S"].owner is None
