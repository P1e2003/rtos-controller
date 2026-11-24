
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


def compute_hyperperiod(tasks):
    """
    Calculate the hyperperiod of a task set.

    The hyperperiod is the least common multiple
    of all task periods.
    """
    if not tasks:
        raise ValueError("Cannot calculate hyperperiod of an empty task set")

    periods = [task.T for task in tasks]

    if any(period <= 0 for period in periods):
        raise ValueError("Task periods must be positive")

    return lcm(*periods)


def load_scenario(path):
    """
    Load a YAML scenario and convert it into a ScenarioConfig.

    Duration can be:
        - An integer
        - 'auto'
        - Omitted (automatically calculated)
    """

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if not isinstance(data, dict):
        raise TypeError("Scenario YAML must contain a dictionary")

    # Read basic scenario information

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

    # Read tasks


    task_data = data.get("tasks")

    if not isinstance(task_data, list) or not task_data:
        raise ValueError("Scenario must contain a non-empty task list")

    tasks = []

    for item in task_data:
        if not isinstance(item, dict):
            raise TypeError("Each task must be a YAML dictionary")

        sections = []

        for section in item.get("critical_sections", []):
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


    # Calculate simulation duration

    duration = data.get("duration", "auto")

    if duration == "auto" or duration is None:
        duration = max(task.phase for task in tasks) + compute_hyperperiod(tasks)

    if not isinstance(duration, int) or isinstance(duration, bool):
        raise TypeError("Duration must be a positive integer or 'auto'")

    if duration <= 0:
        raise ValueError("Duration must be positive")

    # Build ScenarioConfig

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
