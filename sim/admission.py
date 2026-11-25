from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

from sim.models import ProtocolType, ScenarioConfig, SchedulerType, Task


@dataclass
class AdmissionResult:
    accepted: bool
    scheduler: SchedulerType
    protocol: ProtocolType
    reasons: list[str] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)


def _rank_fixed_priority_tasks(
    tasks: list[Task], scheduler: SchedulerType
) -> list[Task]:
    """Return tasks ordered from highest priority to lowest priority."""
    if scheduler == SchedulerType.RM:
        return sorted(
            tasks,
            key=lambda task: (task.T, task.D, task.id),
        )
    if scheduler == SchedulerType.DM:
        return sorted(
            tasks,
            key=lambda task: (task.D, task.T, task.id),
        )
    raise ValueError(f"Unsupported fixed-priority scheduler: {scheduler}")


def _push_through_blocking(
    target_task: Task,
    ordered_tasks: list[Task],
    protocol: ProtocolType,
) -> int:
    """Compute maximum blocking from lower-priority tasks' critical sections."""
    if protocol == ProtocolType.NONE:
        return 0

    target_index = next(
        idx for idx, task in enumerate(ordered_tasks) if task.id == target_task.id
    )
    lower_priority_tasks = ordered_tasks[target_index + 1 :]

    max_blocking = 0
    for lp_task in lower_priority_tasks:
        for section in lp_task.sections:
            # Verify exact ceiling / push-through rule against docs/implementation_plan.md
            max_blocking = max(max_blocking, section.length)
    return max_blocking


def compute_response_time(
    target_task: Task,
    higher_priority_tasks: list[Task],
    blocking: int,
    max_iterations: int = 10_000,
) -> tuple[int, bool]:
    """Compute worst-case response time R_i via fixed-point iteration."""
    if blocking < 0:
        raise ValueError("Blocking cannot be negative")

    current_r = target_task.C + blocking
    if current_r > target_task.D:
        return current_r, False

    for _ in range(max_iterations):
        interference = sum(
            ceil(current_r / hp_task.T) * hp_task.C
            for hp_task in higher_priority_tasks
        )
        next_r = target_task.C + blocking + interference

        if next_r == current_r:
            return next_r, next_r <= target_task.D

        if next_r > target_task.D:
            return next_r, False

        current_r = next_r

    return current_r, False


def check_fixed_priority_admission(config: ScenarioConfig) -> AdmissionResult:
    """Run RTA with blocking for RM or DM."""
    ordered_tasks = _rank_fixed_priority_tasks(config.tasks, config.scheduler)
    reasons: list[str] = []
    response_times: dict[str, int] = {}
    blockings: dict[str, int] = {}

    for idx, task in enumerate(ordered_tasks):
        higher_tasks = ordered_tasks[:idx]
        blocking = _push_through_blocking(task, ordered_tasks, config.protocol)
        r_time, ok = compute_response_time(task, higher_tasks, blocking)

        blockings[task.id] = blocking
        response_times[task.id] = r_time

        if not ok:
            reasons.append(
                f"Task {task.id}: R={r_time} > D={task.D} (blocking={blocking})"
            )

    return AdmissionResult(
        accepted=len(reasons) == 0,
        scheduler=config.scheduler,
        protocol=config.protocol,
        reasons=reasons,
        details={
            "response_times": response_times,
            "blockings": blockings,
        },
    )


def check_edf_srp_admission(config: ScenarioConfig) -> AdmissionResult:
    """Run Baker-style density / SRP admission check for EDF."""
    densities: dict[str, float] = {}
    total_density = 0.0

    for task in config.tasks:
        effective_deadline = min(task.T, task.D)
        density = task.C / effective_deadline
        densities[task.id] = density
        total_density += density

    # Verify SRP blocking term formula against docs/implementation_plan.md
    srp_blocking_term = 0.0
    if config.protocol == ProtocolType.SRP:
        max_section_len = max(
            (
                section.length
                for task in config.tasks
                for section in task.sections
            ),
            default=0,
        )
        if max_section_len > 0:
            min_deadline = min(min(task.T, task.D) for task in config.tasks)
            srp_blocking_term = max_section_len / min_deadline

    total_load = total_density + srp_blocking_term
    accepted = total_load <= 1.0
    reasons = (
        []
        if accepted
        else [
            (
                f"EDF load={total_load:.4f} exceeds 1.0 "
                f"(density={total_density:.4f}, srp_term={srp_blocking_term:.4f})"
            )
        ]
    )

    return AdmissionResult(
        accepted=accepted,
        scheduler=config.scheduler,
        protocol=config.protocol,
        reasons=reasons,
        details={
            "densities": densities,
            "total_density": total_density,
            "srp_blocking_term": srp_blocking_term,
            "total_load": total_load,
        },
    )


def evaluate_admission(config: ScenarioConfig) -> AdmissionResult:
    """Entry point for pre-flight admission control."""
    if config.scheduler in (SchedulerType.RM, SchedulerType.DM):
        return check_fixed_priority_admission(config)
    if config.scheduler == SchedulerType.EDF:
        return check_edf_srp_admission(config)
    raise ValueError(f"Unsupported scheduler: {config.scheduler}")