from __future__ import annotations

from fractions import Fraction

import pytest

from sim.admission import (
    AdmissionResult,
    TaskAdmissionDetail,
    check_admission,
    compute_response_time,
    evaluate_admission,
)
from sim.models import (
    CriticalSection,
    ProtocolType,
    ScenarioConfig,
    SchedulerType,
    Task,
)


def _detail_by_id(result: AdmissionResult, task_id: str) -> TaskAdmissionDetail:
    for item in result.details:
        if item.task_id == task_id:
            return item
    raise AssertionError(f"Missing detail for task {task_id}")


def test_rta_accepts_liu_layland_exceeding_harmonic_set() -> None:
    # Harmonic periods T = 5, 10, 20 with C = 2, 3, 5 -> U = 2/5 + 3/10 + 5/20 = 0.95 (> 0.78)
    tasks = [
        Task(id="T1", C=2, T=5, D=5),
        Task(id="T2", C=3, T=10, D=10),
        Task(id="T3", C=5, T=20, D=20),
    ]

    result = check_admission(tasks, SchedulerType.RM, ProtocolType.NONE)

    assert isinstance(result, AdmissionResult)
    assert result.accepted is True
    assert result.rejection_reason is None
    assert result.total_utilization == Fraction(19, 20)
    assert _detail_by_id(result, "T1").response_time == 2
    assert _detail_by_id(result, "T2").response_time == 5
    assert _detail_by_id(result, "T3").response_time == 19


def test_rta_terminates_and_rejects_overload_set() -> None:
    # U = 4/5 + 5/10 = 1.3 -> must terminate quickly without infinite loop and reject
    tasks = [
        Task(id="T1", C=4, T=5, D=5),
        Task(id="T2", C=5, T=10, D=10),
    ]

    result = check_admission(tasks, SchedulerType.RM, ProtocolType.NONE)

    assert result.accepted is False
    assert result.rejection_reason is not None
    assert _detail_by_id(result, "T2").schedulable is False


def test_pip_blocking_includes_push_through_for_medium_task() -> None:
    # H (highest, T=6): uses resource S (len 1)
    # M (middle,  T=12): does NOT use resource S, so ceiling["S"] >= P_M allows push-through
    # L (lowest,  T=24): uses resource S (len 3) -> B_M = 3
    task_h = Task(
        id="H",
        C=2,
        T=6,
        D=6,
        bcet=2,
        sections=(CriticalSection(offset=0, length=1, resource_id="S"),),
    )
    task_m = Task(id="M", C=2, T=12, D=12)
    task_l = Task(
        id="L",
        C=4,
        T=24,
        D=24,
        bcet=4,
        sections=(CriticalSection(offset=0, length=3, resource_id="S"),),
    )

    result = check_admission(
        [task_h, task_m, task_l], SchedulerType.RM, ProtocolType.PIP
    )

    detail_m = _detail_by_id(result, "M")
    assert detail_m.blocking_bound == 3


def test_icpp_blocking_is_single_longest_section_vs_pip_sum() -> None:
    # H (T=12, D=12, C=3) uses both S1 and S2; L1 (T=24, C=5) holds S1 (len 4);
    # L2 (T=30, C=5) holds S2 (len 4).
    # Under PIP: B_H = min(4 + 4, 4 + 4) = 8 -> R_H = 3 + 8 = 11 (or with interference > 12 -> rejected).
    # Under ICPP: B_H = max(4, 4) = 4 -> R_H = 3 + 4 = 7 <= 12 -> accepted.
    task_h = Task(
        id="H",
        C=5,
        T=12,
        D=12,
        bcet=3,
        sections=(
            CriticalSection(offset=0, length=1, resource_id="S1"),
            CriticalSection(offset=1, length=1, resource_id="S2"),
        ),
    )
    task_l1 = Task(
        id="L1",
        C=5,
        T=24,
        D=24,
        bcet=5,
        sections=(CriticalSection(offset=0, length=4, resource_id="S1"),),
    )
    task_l2 = Task(
        id="L2",
        C=5,
        T=30,
        D=30,
        bcet=5,
        sections=(CriticalSection(offset=0, length=4, resource_id="S2"),),
    )

    res_pip = check_admission(
        [task_h, task_l1, task_l2], SchedulerType.RM, ProtocolType.PIP
    )
    res_icpp = check_admission(
        [task_h, task_l1, task_l2], SchedulerType.RM, ProtocolType.ICPP
    )

    assert _detail_by_id(res_pip, "H").blocking_bound == 8
    assert res_pip.accepted is False
    assert _detail_by_id(res_icpp, "H").blocking_bound == 4
    assert res_icpp.accepted is True


def test_none_rejects_inversion_band_and_bounds_adjacent_pair() -> None:
    # 1) H, M, L where H and L share resource S under NONE -> inversion band rejected
    h_s = Task(
        id="H",
        C=1,
        T=8,
        D=8,
        bcet=1,
        sections=(CriticalSection(offset=0, length=1, resource_id="S"),),
    )
    m_s = Task(id="M", C=2, T=12, D=12)
    l_s = Task(
        id="L",
        C=2,
        T=20,
        D=20,
        bcet=2,
        sections=(CriticalSection(offset=0, length=2, resource_id="S"),),
    )

    res_inversion = check_admission(
        [h_s, m_s, l_s], SchedulerType.RM, ProtocolType.NONE
    )
    assert res_inversion.accepted is False
    assert res_inversion.rejection_reason == "unbounded_priority_inversion_under_none"

    # 2) Adjacent H and L without M, sharing resource S -> B_H is nonzero
    h_adj = Task(
        id="H",
        C=2,
        T=10,
        D=10,
        bcet=2,
        sections=(CriticalSection(offset=0, length=1, resource_id="S"),),
    )
    l_adj = Task(
        id="L",
        C=3,
        T=20,
        D=20,
        bcet=3,
        sections=(CriticalSection(offset=0, length=2, resource_id="S"),),
    )

    res_adj = check_admission([h_adj, l_adj], SchedulerType.RM, ProtocolType.NONE)
    assert _detail_by_id(res_adj, "H").blocking_bound == 2


def test_baker_edf_srp_exact_rational_and_rejects_non_srp_shared() -> None:
    # Equal-D tie handling with exact Fraction: 1/6 + 2/6 + 3/6 = Fraction(1, 1) <= 1
    t1 = Task(id="T1", C=1, T=10, D=6)
    t2 = Task(id="T2", C=2, T=10, D=6)
    t3 = Task(id="T3", C=3, T=10, D=6)

    res_srp = check_admission([t1, t2, t3], SchedulerType.EDF, ProtocolType.SRP)
    assert res_srp.accepted is True
    assert _detail_by_id(res_srp, "T3").density_lhs == Fraction(1, 1)

    # Shared lock under EDF + NONE or EDF + PIP must be rejected
    s1 = Task(
        id="S1",
        C=2,
        T=10,
        D=10,
        bcet=2,
        sections=(CriticalSection(offset=0, length=1, resource_id="LOCK"),),
    )
    s2 = Task(
        id="S2",
        C=2,
        T=12,
        D=12,
        bcet=2,
        sections=(CriticalSection(offset=0, length=1, resource_id="LOCK"),),
    )

    res_none = check_admission([s1, s2], SchedulerType.EDF, ProtocolType.NONE)
    res_pip = check_admission([s1, s2], SchedulerType.EDF, ProtocolType.PIP)

    assert res_none.accepted is False
    assert res_none.rejection_reason == "edf_shared_resources_require_srp"
    assert res_pip.accepted is False
    assert res_pip.rejection_reason == "edf_shared_resources_require_srp"


def test_compute_response_time_and_evaluate_admission_wrapper() -> None:
    hp = Task(id="HP", C=1, T=4, D=4)
    lp = Task(id="LP", C=2, T=10, D=10)

    r_time, ok = compute_response_time(lp, [hp], blocking=1)
    assert ok is True
    assert r_time == 4

    with pytest.raises(ValueError, match="Blocking cannot be negative"):
        compute_response_time(lp, [], blocking=-1)

    cfg = ScenarioConfig(
        name="wrapper-check",
        duration=20,
        scheduler=SchedulerType.RM,
        protocol=ProtocolType.NONE,
        tasks=[hp, lp],
    )
    wrapped = evaluate_admission(cfg)
    assert wrapped.accepted is True
