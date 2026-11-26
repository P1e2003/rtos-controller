from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from math import ceil

from sim.models import (
    ProtocolType,
    ScenarioConfig,
    SchedulerType,
    Task,
    validate_task_set,
)


@dataclass(frozen=True)
class TaskAdmissionDetail:
    task_id: str
    wcet: int
    period: int
    deadline: int
    blocking_bound: int
    response_time: int | None
    density_lhs: float | None
    schedulable: bool
    reason: str | None = None


@dataclass(frozen=True)
class AdmissionResult:
    accepted: bool
    scheduler: SchedulerType
    protocol: ProtocolType
    total_utilization: float
    details: list[TaskAdmissionDetail] = field(default_factory=list)
    rejection_reason: str | None = None


def _rank_fixed_priority_tasks(
    tasks: Sequence[Task], scheduler: SchedulerType
) -> list[Task]:
    """Rank tasks from highest priority (index 0) to lowest matching sim/schedulers.py."""
    if scheduler == SchedulerType.RM:
        return sorted(tasks, key=lambda t: (t.T, t.id))
    if scheduler == SchedulerType.DM:
        return sorted(tasks, key=lambda t: (t.D, t.id))
    raise ValueError(f"Unsupported fixed-priority scheduler: {scheduler}")


def _compute_ceilings(ordered_tasks: Sequence[Task]) -> dict[str, int]:
    """Compute ceiling[r] = max priority score among tasks accessing resource r.

    Higher priority score means higher priority (index 0 has score N, index N-1 has score 1).
    """
    n = len(ordered_tasks)
    ceilings: dict[str, int] = {}
    for idx, task in enumerate(ordered_tasks):
        prio = n - idx
        for sec in task.sections:
            rid = sec.resource_id
            if rid not in ceilings or prio > ceilings[rid]:
                ceilings[rid] = prio
    return ceilings


def _find_shared_resources(tasks: Sequence[Task]) -> set[str]:
    """Return resource_ids accessed by at least two distinct tasks."""
    access_count: dict[str, int] = {}
    for task in tasks:
        seen_in_task = {sec.resource_id for sec in task.sections}
        for rid in seen_in_task:
            access_count[rid] = access_count.get(rid, 0) + 1
    return {rid for rid, count in access_count.items() if count >= 2}


def _has_none_inversion_band(ordered_tasks: Sequence[Task]) -> bool:
    """Return True if any shared resource has H > M > L priority inversion band."""
    n = len(ordered_tasks)
    # idx 0 is highest priority, idx n-1 is lowest priority.
    # H at h_idx, M at m_idx, L at l_idx with h_idx < m_idx < l_idx means P_H > P_M > P_L.
    task_resources: list[set[str]] = [
        {sec.resource_id for sec in task.sections} for task in ordered_tasks
    ]
    for h_idx in range(n):
        for l_idx in range(h_idx + 2, n):
            shared_hl = task_resources[h_idx] & task_resources[l_idx]
            if shared_hl:
                # Any intervening task with h_idx < m_idx < l_idx creates inversion band
                return True
    return False


def _fixed_priority_blocking(
    target_idx: int,
    ordered_tasks: Sequence[Task],
    protocol: ProtocolType,
    ceilings: dict[str, int],
) -> int:
    """Compute blocking bound B_i for task at target_idx under RM/DM."""
    n = len(ordered_tasks)
    target_prio = n - target_idx
    target_task = ordered_tasks[target_idx]
    lp_tasks = ordered_tasks[target_idx + 1 :]

    if not lp_tasks:
        return 0

    if protocol == ProtocolType.NONE:
        target_res = {sec.resource_id for sec in target_task.sections}
        blocking = 0
        for lp in lp_tasks:
            for sec in lp.sections:
                if sec.resource_id in target_res:
                    blocking += sec.length
        return blocking

    if protocol in (ProtocolType.ICPP, ProtocolType.SRP):
        max_len = 0
        for lp in lp_tasks:
            for sec in lp.sections:
                if ceilings.get(sec.resource_id, 0) >= target_prio:
                    max_len = max(max_len, sec.length)
        return max_len

    if protocol == ProtocolType.PIP:
        # Term 1: sum_{k in lp(i)} max_{r in cs(k), ceiling[r] >= P_i} L_{k,r}
        per_task_sum = 0
        for lp in lp_tasks:
            max_for_lp = 0
            for sec in lp.sections:
                if ceilings.get(sec.resource_id, 0) >= target_prio:
                    max_for_lp = max(max_for_lp, sec.length)
            per_task_sum += max_for_lp

        # Term 2: sum_{r: ceiling[r] >= P_i} max_{k in lp(i)} L_{k,r}
        eligible_resources = {
            rid for rid, ceil_val in ceilings.items() if ceil_val >= target_prio
        }
        per_res_sum = 0
        for rid in eligible_resources:
            max_for_res = 0
            for lp in lp_tasks:
                for sec in lp.sections:
                    if sec.resource_id == rid:
                        max_for_res = max(max_for_res, sec.length)
            per_res_sum += max_for_res

        return min(per_task_sum, per_res_sum)

    raise ValueError(f"Unsupported protocol: {protocol}")


def compute_response_time(
    target_task: Task,
    higher_priority_tasks: Sequence[Task],
    blocking: int,
    max_iterations: int = 10_000,
) -> tuple[int, bool]:
    """Compute worst-case response time R_i via exact integer fixed-point iteration."""
    if blocking < 0:
        raise ValueError("Blocking cannot be negative")

    current_r = target_task.C + blocking
    if current_r > target_task.D:
        return current_r, False

    for _ in range(max_iterations):
        interference = sum(
            ceil(current_r / hp.T) * hp.C for hp in higher_priority_tasks
        )
        next_r = target_task.C + blocking + interference

        if next_r == current_r:
            return next_r, next_r <= target_task.D

        if next_r > target_task.D:
            return next_r, False

        current_r = next_r

    return current_r, False


def _check_fixed_priority(
    tasks: list[Task], scheduler: SchedulerType, protocol: ProtocolType
) -> AdmissionResult:
    ordered = _rank_fixed_priority_tasks(tasks, scheduler)
    ceilings = _compute_ceilings(ordered)
    total_util = float(sum((Fraction(t.C, t.T) for t in ordered), Fraction(0, 1)))

    if protocol == ProtocolType.NONE and _has_none_inversion_band(ordered):
        details = [
            TaskAdmissionDetail(
                task_id=t.id,
                wcet=t.C,
                period=t.T,
                deadline=t.D,
                blocking_bound=0,
                response_time=None,
                density_lhs=None,
                schedulable=False,
                reason="unbounded_priority_inversion_under_none",
            )
            for t in ordered
        ]
        return AdmissionResult(
            accepted=False,
            scheduler=scheduler,
            protocol=protocol,
            total_utilization=total_util,
            details=details,
            rejection_reason="unbounded_priority_inversion_under_none",
        )

    details: list[TaskAdmissionDetail] = []
    all_ok = True
    first_reason: str | None = None

    for idx, task in enumerate(ordered):
        hp_tasks = ordered[:idx]
        blocking = _fixed_priority_blocking(idx, ordered, protocol, ceilings)
        r_time, ok = compute_response_time(task, hp_tasks, blocking)
        reason = None if ok else f"R={r_time} > D={task.D} (B={blocking})"
        if not ok:
            all_ok = False
            if first_reason is None:
                first_reason = reason

        details.append(
            TaskAdmissionDetail(
                task_id=task.id,
                wcet=task.C,
                period=task.T,
                deadline=task.D,
                blocking_bound=blocking,
                response_time=r_time,
                density_lhs=None,
                schedulable=ok,
                reason=reason,
            )
        )

    return AdmissionResult(
        accepted=all_ok,
        scheduler=scheduler,
        protocol=protocol,
        total_utilization=total_util,
        details=details,
        rejection_reason=first_reason,
    )


def _check_edf(tasks: list[Task], protocol: ProtocolType) -> AdmissionResult:
    ordered = sorted(tasks, key=lambda t: (t.D, t.id))
    total_util = float(sum((Fraction(t.C, t.T) for t in ordered), Fraction(0, 1)))
    shared_res = _find_shared_resources(ordered)

    if shared_res and protocol != ProtocolType.SRP:
        details = [
            TaskAdmissionDetail(
                task_id=t.id,
                wcet=t.C,
                period=t.T,
                deadline=t.D,
                blocking_bound=0,
                response_time=None,
                density_lhs=None,
                schedulable=False,
                reason="edf_shared_resources_require_srp",
            )
            for t in ordered
        ]
        return AdmissionResult(
            accepted=False,
            scheduler=SchedulerType.EDF,
            protocol=protocol,
            total_utilization=total_util,
            details=details,
            rejection_reason="edf_shared_resources_require_srp",
        )

    n = len(ordered)
    ceilings = _compute_ceilings(ordered)
    details: list[TaskAdmissionDetail] = []
    all_ok = True
    first_reason: str | None = None
    cumulative_density = Fraction(0, 1)

    for k_idx, task in enumerate(ordered):
        pi_k = n - k_idx
        cumulative_density += Fraction(task.C, task.D)

        b_k = 0
        if protocol == ProtocolType.SRP:
            for lower_task in ordered[k_idx + 1 :]:
                for sec in lower_task.sections:
                    if ceilings.get(sec.resource_id, 0) >= pi_k:
                        b_k = max(b_k, sec.length)

        lhs_k = cumulative_density + Fraction(b_k, task.D)
        ok = lhs_k <= Fraction(1, 1)
        reason = None if ok else f"Baker LHS={lhs_k} > 1 at k={k_idx + 1} ({task.id})"
        if not ok:
            all_ok = False
            if first_reason is None:
                first_reason = reason

        details.append(
            TaskAdmissionDetail(
                task_id=task.id,
                wcet=task.C,
                period=task.T,
                deadline=task.D,
                blocking_bound=b_k,
                response_time=None,
                density_lhs=float(lhs_k),
                schedulable=ok,
                reason=reason,
            )
        )

    return AdmissionResult(
        accepted=all_ok,
        scheduler=SchedulerType.EDF,
        protocol=protocol,
        total_utilization=total_util,
        details=details,
        rejection_reason=first_reason,
    )


def check_admission(
    tasks: Sequence[Task],
    scheduler: SchedulerType,
    protocol: ProtocolType,
) -> AdmissionResult:
    """Public admission control entry point."""
    task_list = list(tasks)
    validate_task_set(task_list)

    if scheduler in (SchedulerType.RM, SchedulerType.DM):
        return _check_fixed_priority(task_list, scheduler, protocol)
    if scheduler == SchedulerType.EDF:
        return _check_edf(task_list, protocol)
    raise ValueError(f"Unsupported scheduler: {scheduler}")


def evaluate_admission(config: ScenarioConfig) -> AdmissionResult:
    """Convenience wrapper around check_admission for ScenarioConfig."""
    return check_admission(config.tasks, config.scheduler, config.protocol)
