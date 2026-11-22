"""When rung 3's sweep last finished a whole pass, against real Postgres (F5 plan T9.1; design,
*Observability and operations*: the alert on `sweep_fixes` not finishing; open decision 70).

The pass itself is replaced by a stand-in here: the real one moves every organization's due
rows, and these tests must reach no other suite's rows. What is under test is that the end of
a pass is written, and that a pass which raises writes nothing, so beat's age keeps growing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime

import pytest
from alert_fixtures import postgres_reachable
from live import require_stack
from nexovue_platform.fixes.records import FIX_SWEEP_NAME
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.connections import fix_sweep

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_sweep_status_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

_FINISHED = text("SELECT finished_at FROM fix_sweep_status WHERE sweep = :sweep")
_SET = text(
    """
    INSERT INTO fix_sweep_status (sweep, finished_at) VALUES (:sweep, :at)
    ON CONFLICT (sweep) DO UPDATE SET finished_at = EXCLUDED.finished_at
    """
)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


class PassFailedError(RuntimeError):
    pass


async def _finished(engine: AsyncEngine) -> datetime | None:
    async with engine.connect() as connection:
        found: datetime | None = await connection.scalar(_FINISHED, {"sweep": FIX_SWEEP_NAME})
        return found


async def _set(engine: AsyncEngine, at: datetime) -> None:
    async with engine.begin() as connection:
        await connection.execute(_SET, {"sweep": FIX_SWEEP_NAME, "at": at})


def _clock() -> Callable[[], datetime]:
    return lambda: datetime.now(UTC)


async def test_a_whole_pass_records_when_it_finished(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def finishes(engine: AsyncEngine, **_: object) -> fix_sweep.SweptFixes:
        return fix_sweep.SweptFixes(ended=0, cleared=0, expired=0, refreshes=0)

    monkeypatch.setattr(fix_sweep, "sweep", finishes)
    await _set(engine, LONG_AGO)

    await fix_sweep.sweep_and_record(engine, clock=_clock())

    finished = await _finished(engine)
    assert finished is not None
    assert finished > LONG_AGO


async def test_a_pass_that_raises_records_nothing(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def raises(engine: AsyncEngine, **_: object) -> fix_sweep.SweptFixes:
        raise PassFailedError

    monkeypatch.setattr(fix_sweep, "sweep", raises)
    await _set(engine, LONG_AGO)

    with pytest.raises(PassFailedError):
        await fix_sweep.sweep_and_record(engine, clock=_clock())

    assert await _finished(engine) == LONG_AGO


def test_the_task_runs_the_recording_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """The beat-scheduled task goes through `sweep_and_record`, so a production pass records."""
    process_engine = object()
    passes: list[object] = []
    nothing = fix_sweep.SweptFixes(ended=0, cleared=0, expired=0, refreshes=0)

    async def recording(given: object, **_: object) -> fix_sweep.SweptFixes:
        passes.append(given)
        return nothing

    def run_here(work: Callable[[object], Coroutine[None, None, object]]) -> object:
        return asyncio.run(work(process_engine))

    monkeypatch.setattr(fix_sweep, "sweep_and_record", recording)
    monkeypatch.setattr(fix_sweep, "with_engine", run_here)

    fix_sweep.sweep_fixes_task()

    assert passes == [process_engine]
