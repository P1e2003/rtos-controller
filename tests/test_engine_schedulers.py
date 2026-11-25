
from sim.engine import Simulator
from sim.models import (
    CriticalSection,
    Job,
    OverloadPolicy,
    ProtocolType,
    ScenarioConfig,
    SchedulerType,
    Task,
    TickCategory,
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

    # Constrained deadline (D < T) distinguishes DM from RM.
    constrained_tasks = [
        Task(id="ShortT", C=1, T=8, D=8),
        Task(id="ShortD", C=1, T=12, D=4),
    ]
    rm_constrained = assign_priorities_and_levels(
        constrained_tasks,
        SchedulerType.RM,
    )
    dm_constrained = assign_priorities_and_levels(
        constrained_tasks,
        SchedulerType.DM,
    )
    assert rm_constrained["ShortT"][0] > rm_constrained["ShortD"][0]
    assert dm_constrained["ShortD"][0] > dm_constrained["ShortT"][0]

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

    task_hi = Task(
        id="H",
        C=1,
        T=10,
        D=10,
    )
    task_lo = Task(
        id="L",
        C=3,
        T=20,
        D=20,
        sections=(
            CriticalSection(
                offset=0,
                length=2,
                resource_id="S",
            ),
        ),
    )

    # Tick 0: H runs while L is ready; L must NOT acquire S before dispatch.
    sim_tick0 = Simulator(make_config([task_hi, task_lo], duration=1))
    res_tick0 = sim_tick0.run()
    assert res_tick0.lock_ops == 0
    assert sim_tick0.mutexes["S"].owner is None

    # Ticks 0..2: H runs tick 0, L acquires S at tick 1, unlocks after tick 2.
    simulator = Simulator(make_config([task_hi, task_lo], duration=3))
    result = simulator.run()

    # One acquire and one release.
    assert result.lock_ops == 2

    # Resource must be unlocked after the second execution tick (offset 0 + length 2).
    assert simulator.mutexes["S"].owner is None

    job_lo = next(job for job in result.jobs if job.task_id == "L")
    assert job_lo.remaining_exec == 1


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
        sections=(
            CriticalSection(
                offset=0,
                length=2,
                resource_id="S",
            ),
        ),
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
    # Tick 1: Switch to B, consuming overhead without acquiring S yet.
    assert result.overhead_ticks == 1
    assert result.trace[1].is_overhead is True
    assert result.trace[1].running_job_id is None
    assert job_b.tick_counts[TickCategory.OVERHEAD] == 1
    assert simulator.mutexes["S"].owner is None
    assert job_b.held_resources == []

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
    # Detailed MissCause classification belongs to sim.attribution (#4).
    assert job.miss_cause is None

    # Job continues until execution completes.
    assert job.remaining_exec == 0

    assert job.finish_time == 3

    # The job remains in results only once.
    assert len(result.jobs) == 1

    # Completion must not leave any mutex held.
    assert job.held_resources == []


# TEST 6: ABORT POLICY RELEASES HELD MUTEX

def test_abort_policy_releases_held_mutex():

    # A_hi preempts B_lo at tick 1 so B_lo misses D=4 while still holding S.
    task_hi = Task(
        id="A_hi",
        C=1,
        T=10,
        D=10,
        phase=1,
    )

    task_lo = Task(
        id="B_lo",
        C=4,
        T=10,
        D=4,
        phase=0,
        sections=(
            CriticalSection(
                offset=0,
                length=4,
                resource_id="S",
            ),
        ),
    )

    task_wait = Task(
        id="C_wait",
        C=1,
        T=20,
        D=20,
        phase=2,
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
            [task_hi, task_lo, task_wait],
            duration=6,
            overload_policy=OverloadPolicy.ABORT,
        ),
    )

    result = simulator.run()

    job_lo = next(job for job in result.jobs if job.task_id == "B_lo")
    job_wait = next(job for job in result.jobs if job.task_id == "C_wait")

    # B_lo misses its deadline while still holding S (executed=3 < 4) and is aborted.
    # C_wait acquires S on the next tick and completes cleanly.
    assert job_lo.missed is True
    assert job_lo.executed == 3
    assert job_lo.remaining_exec == 1
    assert job_lo.held_resources == []

    assert job_wait.missed is False
    assert job_wait.executed == 1
    assert job_wait.finish_time is not None
    assert simulator.mutexes["S"].owner is None


# TEST 7: SRP PREEMPTION AND RESUME OF ALREADY-STARTED LOCK HOLDER

def test_srp_already_started_lock_holder_resumes_after_preemption():
    job_l = make_job("L", priority=1, preemption_level=1, remaining_exec=2)
    job_l.executed = 1
    job_l.held_resources = ["S"]

    job_m = make_job("M", priority=2, preemption_level=2, remaining_exec=2)
    job_hh = make_job("HH", priority=4, preemption_level=4, remaining_exec=1)

    # While L holds S (ceiling=3), HH (level 4 > 3) preempts L, M (level 2 <= 3) cannot.
    selected = select_next_job(
        ready_jobs=[job_l, job_m, job_hh],
        running_job=job_l,
        scheduler=SchedulerType.RM,
        protocol=ProtocolType.SRP,
        system_ceiling=3,
    )
    assert selected is job_hh

    # Once HH finishes (running_job=None), L (executed > 0) must resume instead of idle deadlock.
    resumed = select_next_job(
        ready_jobs=[job_l, job_m],
        running_job=None,
        scheduler=SchedulerType.RM,
        protocol=ProtocolType.SRP,
        system_ceiling=3,
    )
    assert resumed is job_l


# TEST 8: RELEASE WAKES WAITERS BY PRIORITY ORDER (NO FIFO HANDOFF)

def test_release_wakes_waiters_for_priority_dispatch():
    tasks = [
        Task(id="L", C=3, T=30, D=30, phase=0, sections=(CriticalSection(0, 3, "S"),)),
        Task(id="M", C=1, T=20, D=20, phase=1, sections=(CriticalSection(0, 1, "S"),)),
        Task(id="H", C=1, T=10, D=10, phase=2, sections=(CriticalSection(0, 1, "S"),)),
    ]
    result = Simulator(make_config(tasks, duration=5)).run()

    # L runs ticks 0,1,2 and releases S at end of tick 2.
    # Even though M blocked before H, H (higher priority) must run at tick 3 and M at tick 4.
    assert [record.running_task_id for record in result.trace] == [
        "L",
        "L",
        "L",
        "H",
        "M",
    ]
    assert result.lock_ops == 6
