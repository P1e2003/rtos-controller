
from collections.abc import Sequence

from sim.models import (
    Job,
    ProtocolType,
    SchedulerType,
    Task,
)


def assign_priorities_and_levels(
    tasks: Sequence[Task],
    scheduler: SchedulerType,
) -> dict[str, tuple[int, int]]:
    """
    Assign static base priorities and preemption levels.

    Higher integers represent higher priorities.
    """

    if not tasks:
        raise ValueError("Task list cannot be empty")

    if len({task.id for task in tasks}) != len(tasks):
        raise ValueError("Task IDs must be unique")

    if scheduler == SchedulerType.RM:
        ordered_tasks = sorted(
            tasks,
            key=lambda task: (task.T, task.id),
        )

    elif scheduler == SchedulerType.DM or scheduler == SchedulerType.EDF:
        ordered_tasks = sorted(
            tasks,
            key=lambda task: (task.D, task.id),
        )

    else:
        raise ValueError(f"Unsupported scheduler: {scheduler}")

    result = {}

    total = len(ordered_tasks)

    for index, task in enumerate(ordered_tasks):
        priority = total - index

        result[task.id] = (priority, priority)

    return result


def _job_sort_key(
    job: Job,
    scheduler: SchedulerType,
):
    """
    Return a deterministic sorting key for a job.
    """

    if scheduler in (SchedulerType.RM, SchedulerType.DM):
        return (
            -job.effective_priority,
            job.task_id,
            job.release_time,
            job.job_index,
        )

    if scheduler == SchedulerType.EDF:
        return (
            job.abs_deadline,
            job.task_id,
            job.release_time,
            job.job_index,
        )

    raise ValueError(f"Unsupported scheduler: {scheduler}")


def _job_is_better(
    candidate: Job,
    current: Job,
    scheduler: SchedulerType,
) -> bool:
    """
    Determine whether candidate has higher scheduling preference.
    """

    return _job_sort_key(candidate, scheduler) < _job_sort_key(
        current,
        scheduler,
    )


def select_next_job(
    ready_jobs: Sequence[Job],
    running_job: Job | None,
    scheduler: SchedulerType,
    protocol: ProtocolType,
    system_ceiling: int,
) -> Job | None:
    """
    Select the next job to execute.
    """

    if not ready_jobs:
        return None

    if scheduler not in (
        SchedulerType.RM,
        SchedulerType.DM,
        SchedulerType.EDF,
    ):
        raise ValueError(f"Unsupported scheduler: {scheduler}")

    if protocol == ProtocolType.SRP:

        # A currently running job can continue without
        # having to pass the preemption gate again.
        if (
            running_job is not None
            and running_job in ready_jobs
            and running_job.remaining_exec > 0
        ):

            candidates = [
                job
                for job in ready_jobs
                if job is not running_job
                and job.remaining_exec > 0
                and job.preemption_level > system_ceiling
                and _job_is_better(job, running_job, scheduler)
            ]

            if not candidates:
                return running_job

            return min(
                candidates,
                key=lambda job: _job_sort_key(job, scheduler),
            )

        # No active running job: only jobs above the ceiling
        # can start execution.
        eligible_jobs = [
            job
            for job in ready_jobs
            if job.remaining_exec > 0
            and (
                job.executed > 0
                or job.preemption_level > system_ceiling
            )
        ]

        if not eligible_jobs:
            return None

        return min(
            eligible_jobs,
            key=lambda job: _job_sort_key(job, scheduler),
        )

    # Normal RM / DM / EDF dispatch.
    eligible_jobs = [
        job for job in ready_jobs
        if job.remaining_exec > 0
    ]

    if not eligible_jobs:
        return None

    return min(
        eligible_jobs,
        key=lambda job: _job_sort_key(job, scheduler),
    )
