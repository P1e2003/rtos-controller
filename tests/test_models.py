
import pytest

from sim.loader import compute_hyperperiod, load_scenario
from sim.models import (
    CriticalSection,
    Job,
    ProtocolType,
    ScenarioConfig,
    SchedulerType,
    Task,
    TickCategory,
    validate_task_set,
)

# TEST 1: VALID TASK AND JOB INITIALIZATION


def test_valid_task_and_job_initialization():
    task = Task(
        id="A",
        C=4,
        T=10,
        D=8,
    )

    assert task.phase == 0
    assert task.bcet == task.C
    assert task.sections == ()

    job = Job(
        task_id="A",
        job_index=0,
        release_time=0,
        abs_deadline=8,
        wcet=4,
        actual_exec=4,
        remaining_exec=4,
        base_priority=1,
        effective_priority=1,
        preemption_level=1,
    )

    assert job.executed == 0
    assert job.held_resources == []
    assert job.blocked_on is None
    assert job.missed is False
    assert job.miss_cause is None
    assert job.finish_time is None

    assert len(job.tick_counts) == 6
    assert set(job.tick_counts) == set(TickCategory)
    assert all(
        count == 0
        for count in job.tick_counts.values()
    )


# TEST 2: INVALID WCET, DEADLINE, PERIOD, PHASE, AND BCET


@pytest.mark.parametrize(
    ("task_values", "error"),
    [
        ({"C": 0, "T": 10, "D": 10}, ValueError),
        ({"C": 6, "T": 10, "D": 5}, ValueError),
        ({"C": 4, "T": 5, "D": 6}, ValueError),
        ({"C": 4, "T": 10, "D": 8, "phase": -1}, ValueError),
        ({"C": 4, "T": 10, "D": 8, "bcet": 5}, ValueError),
        ({"C": 4, "T": 10, "D": 8, "bcet": 0}, ValueError),
    ],
)
def test_rejects_invalid_wcet_deadline_period(task_values, error):
    with pytest.raises(error):
        Task(id="A", **task_values)


# TEST 3: OVERLAPPING, NESTED, AND OUT-OF-BOUNDS SECTIONS


@pytest.mark.parametrize(
    "sections",
    [
        (
            CriticalSection(0, 3, "R1"),
            CriticalSection(2, 2, "R2"),
        ),
        (
            CriticalSection(0, 5, "R1"),
            CriticalSection(1, 2, "R2"),
        ),
        (
            CriticalSection(4, 3, "R1"),
        ),
    ],
)
def test_rejects_nested_or_overlapping_critical_sections(sections):
    with pytest.raises(ValueError):
        Task(
            id="A",
            C=5,
            T=10,
            D=10,
            sections=sections,
        )


# TEST 4: BCET MUST COVER THE LATEST CRITICAL SECTION END


def test_rejects_bcet_shorter_than_critical_section_end():
    with pytest.raises(ValueError):
        Task(
            id="A",
            C=5,
            T=10,
            D=10,
            bcet=3,
            sections=(
                CriticalSection(
                    offset=2,
                    length=2,
                    resource_id="R1",
                ),
            ),
        )


# TEST 5: DUPLICATE TASK IDS


def test_rejects_duplicate_task_ids():
    tasks = [
        Task(id="A", C=2, T=5, D=5),
        Task(id="A", C=1, T=10, D=10),
    ]

    with pytest.raises(ValueError, match="unique"):
        validate_task_set(tasks)

    with pytest.raises(ValueError, match="unique"):
        ScenarioConfig(
            name="duplicate_test",
            duration=10,
            scheduler=SchedulerType.RM,
            protocol=ProtocolType.NONE,
            tasks=tasks,
        )


# TEST 6: YAML LOADING AND AUTOMATIC HYPERPERIOD


def test_load_scenario_yaml_and_auto_hyperperiod(tmp_path):
    yaml_content = """
name: test_scenario
scheduler: RM
protocol: NONE
duration: auto

tasks:
  - id: A
    C: 4
    T: 4
    D: 4
    phase: 1
    bcet: 3
    sections:
      - offset: 1
        length: 2
        resource_id: R1

  - id: B
    C: 2
    T: 6
    D: 6
    phase: 3
    sections: null
"""

    scenario_file = tmp_path / "test_scenario.yaml"
    scenario_file.write_text(yaml_content, encoding="utf-8")

    config = load_scenario(scenario_file)

    assert config.name == "test_scenario"
    assert config.scheduler == SchedulerType.RM
    assert config.protocol == ProtocolType.NONE

    assert compute_hyperperiod(config.tasks) == 12

    # max(phase) + hyperperiod = 3 + 12 = 15
    assert config.duration == 15

    task_a = next(task for task in config.tasks if task.id == "A")
    task_b = next(task for task in config.tasks if task.id == "B")

    assert len(task_a.sections) == 1
    assert task_a.sections[0] == CriticalSection(
        offset=1,
        length=2,
        resource_id="R1",
    )

    # sections: null should safely become an empty tuple.
    assert task_b.sections == ()
