
from dataclasses import dataclass, field
from enum import Enum

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

    def __post_init__(self):
        if self.offset < 0:
            raise ValueError("Critical section offset cannot be negative")

        if self.length <= 0:
            raise ValueError("Critical section length must be positive")

        if not self.resource_id or not self.resource_id.strip():
            raise ValueError("Resource ID cannot be empty")

    @property
    def end(self):
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

    def __post_init__(self):
        if not self.id or not self.id.strip():
            raise ValueError("Task ID cannot be empty")

        if not (0 < self.C <= self.D <= self.T):
            raise ValueError(
                "Task must satisfy 0 < C <= D <= T"
            )

        if self.phase < 0:
            raise ValueError("Task phase cannot be negative")

        if self.bcet is None:
            self.bcet = self.C

        if not (0 < self.bcet <= self.C):
            raise ValueError("BCET must satisfy 0 < BCET <= C")

        self.sections = tuple(self.sections)

        ordered_sections = sorted(
            self.sections,
            key=lambda section: section.offset,
        )

        previous_end = 0

        for section in ordered_sections:
            if section.end > self.C:
                raise ValueError(
                    "Critical section must be within task execution time"
                )

            if section.offset < previous_end:
                raise ValueError(
                    "Critical sections cannot overlap"
                )

            previous_end = section.end

        if ordered_sections:
            latest_end = max(
                section.end for section in ordered_sections
            )

            if self.bcet < latest_end:
                raise ValueError(
                    "BCET must be at least the end of the latest "
                    "critical section"
                )

    # Compatibility with the earlier engine implementation.
    @property
    def task_id(self):
        return self.id

    @property
    def critical_sections(self):
        return self.sections


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
    def job_id(self):
        return f"{self.task_id}_{self.job_index}"


# MUTEX

@dataclass
class Mutex:
    id: str
    ceiling: int = 0
    owner: str | None = None
    wait_queue: list[str] = field(default_factory=list)

    # Compatibility with the earlier engine implementation.
    @property
    def resource_id(self):
        return self.id


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

    def __post_init__(self):
        if not self.name or not self.name.strip():
            raise ValueError("Scenario name cannot be empty")

        if self.duration <= 0:
            raise ValueError("Duration must be positive")

        if not self.tasks:
            raise ValueError("Scenario must contain at least one task")

        task_ids = [task.id for task in self.tasks]

        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Task IDs must be unique")

        if self.overhead_epsilon < 0:
            raise ValueError("Overhead epsilon cannot be negative")

        if self.overrun_factor <= 0:
            raise ValueError("Overrun factor must be positive")



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
