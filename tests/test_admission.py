from __future__ import annotations

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
    # Classic schedulable RM task set: U = 2/5 + 1/10 = 0.50 <= 1.0
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
    # T1 (period 4, C=3) + T2 (period 5, C=3): T2 misses deadline D=5
    t1 = Task(id="T1", C=3, T=4, D=4)
    t2 = Task(id="T2", C=3, T=5, D=5)
    cfg = _make_config([t1, t2], scheduler=SchedulerType.RM, protocol=ProtocolType.NONE)

    result = check_fixed_priority_admission(cfg)

    assert result.accepted is False
    assert len(result.reasons) >= 1
    assert any("T2" in reason for reason in result.reasons)


def test_rm_push_through_blocking_causes_rejection() -> None:
    # T1 has C=2, T=5, D=5 (solo R1=2 <= 5).
    # T2 has lower priority (T=10, D=10) and holds a critical section of length 4.
    # With protocol != NONE, push-through blocking adds 4 to T1 -> R1 = 6 > D1 = 5.
    t1 = Task(id="T1", C=2, T=5, D=5)
    t2 = Task(
        id="T2",
        C=5,
        T=10,
        D=10,
        sections=(CriticalSection(offset=0, length=4, resource_id="R_SHARED"),),
    )

    cfg_no_proto = _make_config(
        [t1, t2], scheduler=SchedulerType.RM, protocol=ProtocolType.NONE
    )
    cfg_with_proto = _make_config(
        [t1, t2], scheduler=SchedulerType.RM, protocol=ProtocolType.PIP
    )

    res_no_proto = evaluate_admission(cfg_no_proto)
    res_with_proto = evaluate_admission(cfg_with_proto)

    assert res_no_proto.accepted is True
    assert res_with_proto.accepted is False
    assert res_with_proto.details["blockings"]["T1"] == 4
    assert any("T1" in reason for reason in res_with_proto.reasons)


def test_dm_prioritizes_shorter_deadline_over_shorter_period() -> None:
    # Task A: T=10, D=4, C=2
    # Task B: T=6,  D=8, C=2
    # Under DM, Task A (D=4) has higher priority than Task B (D=8).
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


def test_edf_density_admitted_and_rejected() -> None:
    # Total density = 2/10 + 3/10 = 0.50 <= 1.0 -> accepted
    t1 = Task(id="T1", C=2, T=10, D=10)
    t2 = Task(id="T2", C=3, T=10, D=10)
    ok_cfg = _make_config([t1, t2], scheduler=SchedulerType.EDF, protocol=ProtocolType.NONE)
    ok_res = evaluate_admission(ok_cfg)
    assert ok_res.accepted is True
    assert ok_res.details["total_density"] == pytest.approx(0.5)

    # Total density = 6/10 + 6/10 = 1.20 > 1.0 -> rejected
    t3 = Task(id="T3", C=6, T=10, D=10)
    t4 = Task(id="T4", C=6, T=10, D=10)
    fail_cfg = _make_config(
        [t3, t4], scheduler=SchedulerType.EDF, protocol=ProtocolType.NONE
    )
    fail_res = check_edf_srp_admission(fail_cfg)
    assert fail_res.accepted is False
    assert len(fail_res.reasons) == 1


def test_edf_srp_blocking_pushes_load_over_threshold() -> None:
    # Density = 3/10 + 3/10 = 0.60, SRP term with section length 5 and min deadline 10 is 0.50
    # Total load = 1.10 > 1.0 -> rejected under SRP
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
    assert res.details["srp_blocking_term"] == pytest.approx(0.5)
    assert res.details["total_load"] == pytest.approx(1.3)