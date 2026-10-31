"""Handing out the project jobs (design, "Where it runs"; plan T2c.3, C23).

Which projects are chosen is proved against Postgres in
`test_detection_project_job_live.py`; this is the order and the shape of the jobs,
and the queue the worker that runs them takes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from nexovue_scheduler.__main__ import worker_queues
from nexovue_scheduler.detection import DETECTION_CONCURRENCY, DETECTION_QUEUE, JUDGE_PROJECT_TASK
from nexovue_scheduler.detection.coordinator import project_requests
from nexovue_scheduler.detection.project_job import (
    PROJECT_HARD_LIMIT_SECONDS,
    PROJECT_SOFT_LIMIT_SECONDS,
    minutes_to_judge,
)
from nexovue_scheduler.detection.projects import ActiveProject, fair_order
from nexovue_scheduler.jobs import registry
from nexovue_scheduler.jobs.worker import QUEUE_CAPS

SETTLED: Final = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
BIG, SMALL, OTHER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _projects() -> list[ActiveProject]:
    return [
        *(ActiveProject(BIG, uuid.uuid4()) for _ in range(4)),
        ActiveProject(SMALL, uuid.uuid4()),
        *(ActiveProject(OTHER, uuid.uuid4()) for _ in range(2)),
    ]


def test_each_organization_takes_its_turn_before_any_takes_a_second() -> None:
    """Design 2.1, M5: a tenant with many projects cannot hold the rest back."""
    ordered = [project.org_id for project in fair_order(_projects())]

    assert ordered[:3] == [BIG, SMALL, OTHER]
    assert ordered[3:5] == [BIG, OTHER]
    assert ordered[5:] == [BIG, BIG]


def test_each_project_gets_one_job_on_the_detection_queue_in_the_fair_order() -> None:
    projects = _projects()

    requests = project_requests(projects, SETTLED)

    assert len(requests) == len(projects)
    assert {request.queue for request in requests} == {DETECTION_QUEUE}
    assert {request.task for request in requests} == {JUDGE_PROJECT_TASK}
    assert [r.org_id for r in requests] == [str(p.org_id) for p in fair_order(projects)]
    # The claim takes the earliest first: the order survives one shared now().
    countdowns = [request.countdown_seconds for request in requests]
    assert countdowns == sorted(countdowns) and len(set(countdowns)) == len(countdowns)
    first = requests[0]
    assert first.kwargs["settled_minute"] == SETTLED.isoformat()
    assert first.expires_seconds == 60
    assert first.soft_limit_seconds == PROJECT_SOFT_LIMIT_SECONDS == 20
    assert first.hard_limit_seconds == PROJECT_HARD_LIMIT_SECONDS


def test_a_default_worker_takes_detection_beside_its_probes_capped_at_two() -> None:
    """C23, D32: `scheduler-light` runs `worker` with no queues named, so its default
    is what takes detection. Naming queues replaces it, as `scheduler-deliveries` does."""
    assert worker_queues("", region="mumbai") == ["light", "light.mumbai", DETECTION_QUEUE]
    assert worker_queues("deliveries", region="mumbai") == ["deliveries"]
    assert QUEUE_CAPS == {DETECTION_QUEUE: DETECTION_CONCURRENCY}
    assert DETECTION_CONCURRENCY == 2


def test_every_detection_task_is_registered_with_the_workers() -> None:
    names = registry.registered()

    for task in (
        "nexovue_scheduler.detection.sweep",
        "nexovue_scheduler.detection.judge_project",
        "nexovue_scheduler.detection.sweep_baselines",
        "nexovue_scheduler.detection.compute_baselines",
    ):
        assert task in names


def test_a_run_catches_up_two_minutes_and_restarts_past_ten() -> None:
    minute = SETTLED.replace(minute=0)

    def behind(minutes: int) -> datetime:
        return minute.replace(hour=11, minute=60 - minutes)

    assert minutes_to_judge(None, minute) == [minute]
    assert minutes_to_judge(minute, minute) == []
    assert minutes_to_judge(behind(1), minute) == [minute]
    assert minutes_to_judge(behind(5), minute) == [behind(4), behind(3)]
    assert minutes_to_judge(behind(10), minute) == [behind(9), behind(8)]
    assert minutes_to_judge(behind(11), minute) == [minute]
