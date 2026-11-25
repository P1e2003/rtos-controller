
from math import lcm
from pathlib import Path

import yaml

from sim.models import (
    CriticalSection,
    OverloadPolicy,
    ProtocolType,
    ScenarioConfig,
    SchedulerType,
    Task,
)


def compute_hyperperiod(tasks: list[Task]) -> int:
    """Calculate the least common multiple of all task periods."""
    if not tasks:
        raise ValueError("Cannot calculate hyperperiod of an empty task set")

    periods = [task.T for task in tasks]

    if any(period <= 0 for period in periods):
        raise ValueError("Task periods must be positive")

    return lcm(*periods)


def load_scenario(path: str | Path) -> ScenarioConfig:
    """
    Load a YAML scenario and convert it into a ScenarioConfig.

    Duration can be an integer, 'auto', 'AUTO', null, or omitted.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if not isinstance(data, dict):
        raise TypeError("Scenario YAML must contain a dictionary")

    name = data.get("name", path.stem)

    scheduler = SchedulerType(
        data.get("scheduler", "RM").upper()
    )

    protocol = ProtocolType(
        data.get("protocol", "NONE").upper()
    )

    overload_policy = OverloadPolicy(
        data.get("overload_policy", "CONTINUE").upper()
    )

    task_data = data.get("tasks")

    if not isinstance(task_data, list) or not task_data:
        raise ValueError("Scenario must contain a non-empty task list")

    tasks: list[Task] = []

    for item in task_data:
        if not isinstance(item, dict):
            raise TypeError("Each task must be a YAML dictionary")

        section_data = item.get("sections") or []

        if not isinstance(section_data, list):
            raise TypeError("Task sections must be a YAML list or null")

        sections: list[CriticalSection] = []

        for section in section_data:
            if not isinstance(section, dict):
                raise TypeError(
                    "Each critical section must be a YAML dictionary"
                )

            sections.append(
                CriticalSection(
                    offset=section["offset"],
                    length=section["length"],
                    resource_id=section["resource_id"],
                )
            )

        task = Task(
            id=item["id"],
            C=item["C"],
            T=item["T"],
            D=item["D"],
            base_priority=item.get("base_priority", 0),
            phase=item.get("phase", 0),
            bcet=item.get("bcet"),
            sections=tuple(sections),
        )

        tasks.append(task)

    duration = data.get("duration", "auto")

    if (
        duration is None
        or (
            isinstance(duration, str)
            and duration.strip().upper() == "AUTO"
        )
    ):
        duration = (
            max(task.phase for task in tasks)
            + compute_hyperperiod(tasks)
        )

    if type(duration) is not int:
        raise TypeError("Duration must be a positive integer or 'auto'")

    if duration <= 0:
        raise ValueError("Duration must be positive")

    return ScenarioConfig(
        name=name,
        duration=duration,
        scheduler=scheduler,
        protocol=protocol,
        tasks=tasks,
        overhead_epsilon=data.get("overhead_epsilon", 0),
        overload_policy=overload_policy,
        seed=data.get("seed", 42),
        jitter_enabled=data.get("jitter_enabled", False),
        overrun_factor=data.get("overrun_factor", 1.0),
    )
