from __future__ import annotations

from fractions import Fraction

import pytest

from sim.admission import (
    AdmissionResult,
    check_edf_srp_admission,
    check_fixed_priority_admission,
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


def _make_config(
    tasks: list[Task],
    scheduler: SchedulerType = SchedulerType.RM,
    protocol: ProtocolType = ProtocolType.NONE,
    duration: int = 100,
) -> ScenarioConfig:
    return ScenarioConfig(
        name="admission-test-scenario",
        duration=duration,
        scheduler=scheduler,
        protocol=protocol,
        tasks=tasks,
    )


def test_rm_admitted_without_blocking() -> None:
    t1 = Task(id="T1", C=2, T=5, D=5)
    t2 = Task(id="T2", C=1, T=10, D=10)
    cfg = _make_config([t2, t1], scheduler=SchedulerType.RM, protocol=ProtocolType.NONE)

    result = evaluate_admission(cfg)

    assert isinstance(result, AdmissionResult)
    assert result.accepted is True
    assert result.reasons == []
    assert result.details["response_times"]["T1"] == 2
    assert result.details["response_times"]["T2"] == 3


def test_rm_rejected_due_to_interference() -> None:
    t1 = Task(id="T1", C=3, T=4, D=4)
    t2 = Task(id="T2", C=3, T=5, D=5)
    cfg = _make_config([t1, t2], scheduler=SchedulerType.RM, protocol=ProtocolType.NONE)

    result = check_fixed_priority_admission(cfg)

    assert result.accepted is False
    assert len(result.reasons) >= 1
    assert any("T2" in reason for reason in result.reasons)


def test_rm_push_through_blocking_pip_edge_case() -> None:
    # T1 (high prio): C=2, T=5, D=5
    # T2 (mid prio): C=2, T=8, D=8 (no section)
    # T3 (low prio): C=5, T=10, D=10, holds critical section length 4 -> push-through to T1
    t1 = Task(id="T1", C=2, T=5, D=5)
    t2 = Task(id="T2", C=1, T=15, D=15)
    t3 = Task(
        id="T3",
        C=4,
        T=20,
        D=20,
        sections=(CriticalSection(offset=0, length=4, resource_id="R_SHARED"),),
    )

    cfg_none = _make_config(
        [t1, t2, t3], scheduler=SchedulerType.RM, protocol=ProtocolType.NONE
    )
    cfg_pip = _make_config(
        [t1, t2, t3], scheduler=SchedulerType.RM, protocol=ProtocolType.PIP
    )

    res_none = evaluate_admission(cfg_none)
    res_pip = evaluate_admission(cfg_pip)

    assert res_none.accepted is True
    assert res_none.details["blockings"]["T1"] == 0
    assert res_pip.accepted is False
    assert res_pip.details["blockings"]["T1"] == 4
    assert any("T1" in reason for reason in res_pip.reasons)


def test_adjacent_none_protocol_ignores_critical_sections() -> None:
    # Adjacent critical sections [0, 2) and [2, 4) in T2 under ProtocolType.NONE
    # must not add blocking to higher-priority T1.
    t1 = Task(id="T1", C=2, T=6, D=6)
    t2 = Task(
        id="T2",
        C=5,
        T=12,
        D=12,
        sections=(
            CriticalSection(offset=0, length=2, resource_id="RES_A"),
            CriticalSection(offset=2, length=2, resource_id="RES_B"),
        ),
    )
    cfg = _make_config([t1, t2], scheduler=SchedulerType.RM, protocol=ProtocolType.NONE)

    res = evaluate_admission(cfg)

    assert res.accepted is True
    assert res.details["blockings"]["T1"] == 0
    assert res.details["blockings"]["T2"] == 0
    assert res.details["response_times"]["T1"] == 2


def test_dm_prioritizes_shorter_deadline_over_shorter_period() -> None:
    task_a = Task(id="TA", C=2, T=10, D=4)
    task_b = Task(id="TB", C=2, T=12, D=8)
    cfg = _make_config([task_a, task_b], scheduler=SchedulerType.DM)

    result = evaluate_admission(cfg)

    assert result.accepted is True
    assert result.details["response_times"]["TA"] == 2
    assert result.details["response_times"]["TB"] == 4


def test_compute_response_time_fixed_point_convergence() -> None:
    hp_task = Task(id="TH", C=1, T=4, D=4)
    target = Task(id="TL", C=2, T=10, D=10)

    r_time, ok = compute_response_time(target, [hp_task], blocking=1)

    assert ok is True
    assert r_time == 4


def test_compute_response_time_negative_blocking_raises() -> None:
    task = Task(id="T1", C=1, T=10, D=10)
    with pytest.raises(ValueError, match="Blocking cannot be negative"):
        compute_response_time(task, [], blocking=-1)


def test_edf_equal_d_exact_fraction_boundary() -> None:
    # Equal relative deadlines D=6: densities 1/6 + 2/6 + 3/6 = 6/6 = Fraction(1, 1) -> accepted
    t1 = Task(id="T1", C=1, T=10, D=6)
    t2 = Task(id="T2", C=2, T=10, D=6)
    t3 = Task(id="T3", C=3, T=10, D=6)
    ok_cfg = _make_config(
        [t1, t2, t3], scheduler=SchedulerType.EDF, protocol=ProtocolType.NONE
    )
    ok_res = evaluate_admission(ok_cfg)
    assert ok_res.accepted is True
    assert ok_res.details["total_density"] == Fraction(1, 1)
    assert ok_res.details["total_load"] == Fraction(1, 1)

    # Bump T3 C from 3 to 4 -> total_density = 7/6 > 1 -> rejected
    t3_over = Task(id="T3", C=4, T=10, D=6)
    fail_cfg = _make_config(
        [t1, t2, t3_over], scheduler=SchedulerType.EDF, protocol=ProtocolType.NONE
    )
    fail_res = check_edf_srp_admission(fail_cfg)
    assert fail_res.accepted is False
    assert fail_res.details["total_density"] == Fraction(7, 6)


def test_edf_srp_blocking_pushes_load_over_threshold() -> None:
    # Density = 5/10 + 3/10 = 4/5, SRP term = 5/10 = 1/2 -> total_load = 13/10 > 1
    t1 = Task(
        id="T1",
        C=5,
        T=10,
        D=10,
        sections=(CriticalSection(offset=0, length=5, resource_id="RES1"),),
    )
    t2 = Task(id="T2", C=3, T=10, D=10)
    cfg = _make_config([t1, t2], scheduler=SchedulerType.EDF, protocol=ProtocolType.SRP)

    res = evaluate_admission(cfg)

    assert res.accepted is False
    assert res.details["srp_blocking_term"] == Fraction(1, 2)
    assert res.details["total_load"] == Fraction(13, 10)