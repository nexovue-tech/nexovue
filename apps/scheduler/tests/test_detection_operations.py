"""What detection tells its operator (F6 plan T7.1, C8): the sweep's own record,
which *detection stalled* reads, and the project a failing job was judging,
named in the log because no metric label may name it.

Every store is replaced; what beat reads from the record is proved against
Postgres in `test_scheduler_metrics_live.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from structlog.testing import capture_logs

from nexovue_scheduler.detection import coordinator, holds, project_job
from nexovue_scheduler.detection.project_job import IntakeTrouble
from nexovue_scheduler.detection.settle import Settled

SETTLED: Final = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
MINUTE: Final = timedelta(minutes=1)


def _run_here[Result](work: Callable[[Any], Coroutine[Any, Any, Result]]) -> Result:
    return asyncio.run(work(None))


# ------------------------------------------------------------ the sweep's record


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[datetime | None]:
    """What each sweep records about itself."""
    records: list[datetime | None] = []

    async def record(_engine: object, settled: datetime | None) -> None:
        records.append(settled)

    monkeypatch.setattr(coordinator, "_report_stopped", lambda: None)
    monkeypatch.setattr(coordinator, "_intake", lambda _now: None)
    monkeypatch.setattr(coordinator, "with_engine", _run_here)
    monkeypatch.setattr(coordinator, "_record", record)
    return records


def test_a_sweep_that_settles_nothing_still_records_that_it_ran(
    monkeypatch: pytest.MonkeyPatch, recorded: list[datetime | None]
) -> None:
    """A sweep whose pipeline settles nothing must read as alive but stalled, not
    as silent."""
    monkeypatch.setattr(coordinator, "_settled", lambda: None)

    coordinator.sweep()

    assert recorded == [None]


def test_a_sweep_records_the_minute_it_handed_to_the_project_jobs(
    monkeypatch: pytest.MonkeyPatch, recorded: list[datetime | None]
) -> None:
    found = Settled(minute=SETTLED, current=SETTLED, missing=(), lagging=(), now=SETTLED)
    held = SETTLED - 5 * MINUTE

    async def decide(_engine: object, _found: Settled, _intake: object) -> holds.Decision:
        return holds.Decision(minute=held, trouble=IntakeTrouble())

    async def queue(_engine: object, _settled: datetime, _trouble: object, *, now: datetime) -> int:
        return 0

    monkeypatch.setattr(coordinator, "_settled", lambda: found)
    monkeypatch.setattr(holds, "decide", decide)
    monkeypatch.setattr(coordinator, "queue_project_jobs", queue)

    coordinator.sweep()

    assert recorded == [held]


# ------------------------------------------------------- a failing project job


@pytest.mark.parametrize("failing", ["store_for_process", "with_engine"])
def test_a_project_job_that_fails_names_its_project_in_the_log(
    monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    """C8: *project detection failing* counts projects and names none, so the log
    line is how an operator finds which one, and the worker's own failure line
    names only the task. Opening ClickHouse fails as much as judging does."""
    project = uuid.uuid4()

    def fail(*_args: object) -> None:
        raise ConnectionError("ClickHouse did not answer")

    monkeypatch.setattr(project_job, "store_for_process", object)
    monkeypatch.setattr(project_job, "with_engine", fail)
    monkeypatch.setattr(project_job, failing, fail)

    with capture_logs() as logs, pytest.raises(ConnectionError):
        project_job.judge(project_id=str(project), settled_minute=SETTLED.isoformat())

    failed = [entry for entry in logs if entry.get("project_id") == str(project)]
    assert [entry["log_level"] for entry in failed] == ["error"]
    assert failed[0]["settled_minute"] == SETTLED.isoformat()
