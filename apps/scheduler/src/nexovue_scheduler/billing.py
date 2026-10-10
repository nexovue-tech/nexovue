"""Ends a payment's grace period once it has run out (readiness group 3).

When the processor stops retrying a failed payment, the API keeps the paid plan
and sets `billing_subscriptions.grace_until` 7 days out. This sweep is the other
half: past that moment the arrangement is `lapsed` and the organization is on the
free tier. `lapsed`, not `cancelled` — nobody ended it, so a late successful
charge brings the plan back (the API's webhook handling).

One statement, so the arrangement and the plan cannot disagree.
"""

from __future__ import annotations

from typing import Final

from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine

logger = get_logger(__name__)

END_LAPSED_GRACE_TASK: Final = "nexovue_scheduler.billing.end_lapsed_grace"

_END_LAPSED_GRACE: Final = text(
    """
    WITH lapsed AS (
        UPDATE billing_subscriptions SET status = 'lapsed'
        WHERE status = 'active' AND grace_until < now()
        RETURNING org_id
    )
    UPDATE organizations SET plan = 'free'
    WHERE id IN (SELECT org_id FROM lapsed) AND plan <> 'free'
    RETURNING id
    """
)


async def end_lapsed_grace(engine: AsyncEngine) -> int:
    """Lapse every arrangement whose grace has run out; how many organizations moved."""
    async with engine.begin() as connection:
        return len((await connection.execute(_END_LAPSED_GRACE)).all())


@task(END_LAPSED_GRACE_TASK)
def sweep_lapsed_grace() -> None:
    moved = with_engine(end_lapsed_grace)
    if moved:
        logger.info(
            "payment grace periods ended; organizations moved to the free plan", moved=moved
        )
