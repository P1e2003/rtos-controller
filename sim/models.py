from dataclasses import dataclass, field
from enum import Enum
from math import isfinite


def _require_int(name: str, value: int) -> None:
    """Reject values that are not actual integers, including booleans."""
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")


# ENUMS


class SchedulerType(str, Enum):
    RM = "RM"
    DM = "DM"
    EDF = "EDF"


class ProtocolType(str, Enum):
    NONE = "NONE"
    PIP = "PIP"
    ICPP = "ICPP"
    SRP = "SRP"


class OverloadPolicy(str, Enum):
    CONTINUE = "CONTINUE"
    ABORT = "ABORT"


class TickCategory(str, Enum):
    RUNNING = "RUNNING"
    OVERHEAD = "OVERHEAD"
    INTERFERENCE = "INTERFERENCE"
    DIRECT_BLOCKING = "DIRECT_BLOCKING"
    CEILING_OR_PUSH_THROUGH_BLOCKING = (
        "CEILING_OR_PUSH_THROUGH_BLOCKING"
    )
    INVERSION = "INVERSION"


class MissCause(str, Enum):
    INVERSION_MISS = "INVERSION_MISS"
    MIXED_OVERLOAD_AND_INVERSION = (
        "MIXED_OVERLOAD_AND_INVERSION"
    )
    OVERRUN_OR_OVERHEAD_MISS = (
        "OVERRUN_OR_OVERHEAD_MISS"
    )
    OVERLOAD_MISS = "OVERLOAD_MISS"
    SCHEDULABILITY_OR_BLOCKING_MISS = (
        "SCHEDULABILITY_OR_BLOCKING_MISS"
    )


# CRITICAL SECTION


@dataclass
class CriticalSection:
    offset: int
    length: int
    resource_id: str

    def __post_init__(self) -> None:
        _require_int("offset", self.offset)
        _require_int("length", self.length)

        if self.offset < 0:
            raise ValueError("Critical section offset cannot be negative")

        if self.length <= 0:
            raise ValueError("Critical section length must be positive")

        if not self.resource_id or not self.resource_id.strip():
            raise ValueError("Resource ID cannot be empty")

    @property
    def end(self) -> int:
        return self.offset + self.length


# TASK


@dataclass
class Task:
    id: str
    C: int
    T: int
    D: int
    base_priority: int = 0
    phase: int = 0
    bcet: int | None = None
    sections: tuple[CriticalSection, ...] = ()

    def __post_init__(self) -> None:
        for name in ("C", "T", "D", "phase"):
            _require_int(name, getattr(self, name))

        if self.bcet is not None:
            _require_int("bcet", self.bcet)

        if not self.id or not self.id.strip():
            raise ValueError("Task ID cannot be empty")

        if not (0 < self.C <= self.D <= self.T):
            raise ValueError("Task must satisfy 0 < C <= D <= T")

        if self.phase < 0:
            raise ValueError("Task phase cannot be negative")

        if self.bcet is None:
            self.bcet = self.C

        if not (0 < self.bcet <= self.C):
            raise ValueError("BCET must satisfy 0 < BCET <= C")

        ordered_sections = tuple(
            sorted(self.sections, key=lambda section: section.offset)
        )
        self.sections = ordered_sections

        previous_end = 0

        for section in self.sections:
            if not isinstance(section, CriticalSection):
                raise TypeError(
                    "Task sections must contain CriticalSection objects"
                )

            if section.end > self.C:
                raise ValueError(
                    "Critical section must be within task execution time"
                )

            if section.offset < previous_end:
                raise ValueError("Critical sections cannot overlap")

            previous_end = section.end

        if self.sections:
            latest_end = max(
                section.end for section in self.sections
            )

            if self.bcet < latest_end:
                raise ValueError(
                    "BCET must be at least the end of the latest "
                    "critical section"
                )


# JOB


@dataclass
class Job:
    task_id: str
    job_index: int
    release_time: int
    abs_deadline: int
    wcet: int
    actual_exec: int
    remaining_exec: int
    base_priority: int
    effective_priority: int
    preemption_level: int

    executed: int = 0
    held_resources: list[str] = field(default_factory=list)
    blocked_on: str | None = None
    missed: bool = False
    miss_cause: MissCause | None = None
    finish_time: int | None = None

    tick_counts: dict[TickCategory, int] = field(
        default_factory=lambda: {
            category: 0 for category in TickCategory
        }
    )

    @property
    def job_id(self) -> str:
        return f"{self.task_id}_{self.job_index}"


# MUTEX


@dataclass
class Mutex:
    id: str
    ceiling: int = 0
    owner: Job | None = None
    wait_queue: list[Job] = field(default_factory=list)


# TASK SET VALIDATION


def validate_task_set(tasks: list[Task]) -> None:
    """Validate that a task set is non-empty and has unique task IDs."""
    if not tasks:
        raise ValueError("Scenario must contain at least one task")

    task_ids = [task.id for task in tasks]

    if len(task_ids) != len(set(task_ids)):
        raise ValueError("Task IDs must be unique")


# SCENARIO CONFIGURATION


@dataclass
class ScenarioConfig:
    name: str
    duration: int
    scheduler: SchedulerType
    protocol: ProtocolType
    tasks: list[Task]

    overhead_epsilon: int = 0
    overload_policy: OverloadPolicy = OverloadPolicy.CONTINUE
    seed: int = 42
    jitter_enabled: bool = False
    overrun_factor: float = 1.0

    def __post_init__(self) -> None:
        _require_int("duration", self.duration)
        _require_int("overhead_epsilon", self.overhead_epsilon)
        _require_int("seed", self.seed)

        if not self.name or not self.name.strip():
            raise ValueError("Scenario name cannot be empty")

        if self.duration <= 0:
            raise ValueError("Duration must be positive")

        validate_task_set(self.tasks)

        if self.overhead_epsilon < 0:
            raise ValueError("Overhead epsilon cannot be negative")

        if (
            isinstance(self.overrun_factor, bool)
            or not isinstance(self.overrun_factor, (int, float))
        ):
            raise TypeError("Overrun factor must be a number")

        if (
            not isfinite(self.overrun_factor)
            or self.overrun_factor <= 0
        ):
            raise ValueError(
                "Overrun factor must be positive and finite"
            )


# TICK RECORD


@dataclass
class TickRecord:
    tick: int
    running_job_id: str | None
    running_task_id: str | None
    is_overhead: bool
    job_states: dict[str, TickCategory]
    system_ceiling: int


# SIMULATION RESULT


@dataclass
class SimulationResult:
    config: ScenarioConfig
    jobs: list[Job]
    trace: list[TickRecord]

    context_switches: int = 0
    lock_ops: int = 0
    priority_changes: int = 0
    overhead_ticks: int = 0
    admission_accepted: bool | None = None