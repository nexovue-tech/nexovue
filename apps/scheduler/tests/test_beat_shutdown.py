"""Beat survives being closed twice (P1-33).

Celery closes the scheduler more than once on the way down — `sync()` does, and
so does the beat service around it. The first version disposed its engine on the
first call, left the reference in place, and closed the loop; the second call
then ran `dispose()` again on a loop that no longer existed and beat died with
`RuntimeError: Event loop is closed` while it was already shutting down. That
buried the real reason for the shutdown under a stack trace about something
unrelated.

The engine has to be present for this to reproduce. A first attempt at this test
used a probe with no engine and passed even with the fix removed, because the
line that raises never ran.
"""

from __future__ import annotations

import asyncio

from nexovue_scheduler.beat import PostgresScheduler


class _RecordingEngine:
    """Stands in for the AsyncEngine, counting how often it is disposed."""

    def __init__(self) -> None:
        self.disposals = 0

    async def dispose(self) -> None:
        self.disposals += 1


def _scheduler_at_shutdown() -> tuple[PostgresScheduler, _RecordingEngine]:
    """A scheduler with only the state the shutdown path touches.

    Built without `__init__`, which would want Redis and Celery's base class;
    the defect lives entirely in `_shut_down_engine`.
    """
    scheduler = PostgresScheduler.__new__(PostgresScheduler)
    engine = _RecordingEngine()
    scheduler._loop = asyncio.new_event_loop()
    scheduler._engine = engine  # type: ignore[assignment]  # a stand-in, not an AsyncEngine
    return scheduler, engine


def test_closing_twice_does_not_raise_and_disposes_once() -> None:
    scheduler, engine = _scheduler_at_shutdown()

    scheduler._shut_down_engine()
    assert engine.disposals == 1
    assert scheduler._loop.is_closed()

    # The second call Celery makes. Before the fix this raised.
    scheduler._shut_down_engine()
    assert engine.disposals == 1, "the engine was disposed twice"
    assert scheduler._loop.is_closed()
