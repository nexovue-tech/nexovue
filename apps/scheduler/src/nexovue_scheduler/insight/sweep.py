"""The AI role's sweep: runs a killed child left behind, and wording nobody will write (F3
design, *Wording states*, *Failure handling*; plan T5.5).

Beat adds it to the `insight` queue every `SWEEP_SECONDS`, so it runs as `nexovue_insight`
on that account's own grants. Two passes, across every organization, each row written by
its own `org_id`:

- **Died.** A run still `started` past any run's hard limit and lease grace was killed with
  its child: `died`, its reservation moved to used and flagged estimated, and its
  explanation `failed`, reason `died`, if still pending at that generation and no later
  attempt of it is under way (AC54).
- **Expired.** Wording `pending` past its job's life with no run under way: the job expired
  or was spent unrun (the AI container may have been stopped), so `failed`, reason
  `expired` (AC81). A click works at once meanwhile.

Both act only on a matching generation, so a click after a killed run is never overwritten.

**Not here:** ending an AI-account session that holds a transaction or listens (status,
*Open decisions* 7). That needs the install account's `pg_terminate_backend`, which this
account must never hold, so its natural place is beat's leader, beside `metrics.read_queues`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

from nexovue_platform.ai_records import CALL_SECONDS_MAX
from nexovue_platform.evidence import (
    SWEEP_INSIGHT_TASK,
    WORD_EXPIRES_SECONDS,
    WORD_HARD_LIMIT_EXTRA_SECONDS,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.insight import store
from nexovue_scheduler.jobs.queue import LEASE_GRACE_SECONDS
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine

logger = get_logger(__name__)

# Beat's interval (design: every 5 minutes, a guess).
SWEEP_SECONDS: Final = 300
# Past any run's hard limit and its lease's grace, a `started` run's child is gone. The
# longest a connection allows, so no live run is ever called dead.
DIED_AFTER: Final = timedelta(
    seconds=CALL_SECONDS_MAX + WORD_HARD_LIMIT_EXTRA_SECONDS + LEASE_GRACE_SECONDS
)
EXPIRED_AFTER: Final = timedelta(seconds=WORD_EXPIRES_SECONDS)
BATCH: Final = 500


async def sweep(engine: AsyncEngine, *, clock: Callable[[], datetime]) -> store.Swept:
    now = clock()
    died = await store.sweep_died(engine, before=now - DIED_AFTER, now=now, batch=BATCH)
    expired = await store.sweep_expired(engine, before=now - EXPIRED_AFTER, now=now, batch=BATCH)
    return store.Swept(died=died, expired=expired)


@task(SWEEP_INSIGHT_TASK)
def sweep_task() -> None:
    swept = with_engine(lambda engine: sweep(engine, clock=lambda: datetime.now(UTC)))
    if swept.died or swept.expired:
        logger.info("the AI sweep ended stuck wording", died=swept.died, expired=swept.expired)
