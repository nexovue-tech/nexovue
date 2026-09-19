"""Delivering what the outbox holds: claim, send outside any transaction, record.

Called by the fast-path task that `record_outcome` dispatches after a commit, and
by the sweep beat runs every 15 seconds for anything the fast path missed
(ADR-013). Both may run at once; the claim's locks and leases make that safe.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import messages, outbox
from nexovue_scheduler.alerts.dispatch import Outbound, Outgoing, send

logger = get_logger(__name__)

# One channel per claim, so its lease starts just before its send. Taking several
# and sending them in turn let the last leases lapse while earlier sends ran, and
# another worker then sent them too.
CHANNELS_PER_CLAIM: Final = 1

# Stop claiming after this long, well inside the task's time limit, so a worker
# never holds leases it will not live to use.
BUDGET_SECONDS: Final = 20.0


@dataclass(slots=True)
class Delivered:
    delivered: int = 0
    retrying: int = 0
    failed: int = 0
    dropped: int = 0


async def deliver_due(
    engine: AsyncEngine,
    outbound: Outbound,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    org_id: uuid.UUID | None = None,
) -> Delivered:
    report = Delivered()
    started = time.monotonic()
    while time.monotonic() - started < BUDGET_SECONDS:
        async with engine.begin() as connection:
            claimed = await outbox.claim(
                connection, now=now(), limit=CHANNELS_PER_CLAIM, org_id=org_id
            )
        report.dropped += claimed.dropped
        if not claimed.channels:
            break
        for batch in claimed.batches:
            result = await _send(engine, outbound, batch, now=now())
            async with engine.begin() as connection:
                recorded = await outbox.record(connection, batch, result, now=now())
            _count(report, recorded)
            logger.info(
                "alert delivery attempted",
                channel_kind=batch.channel.kind,
                batch_id=str(batch.id),
                notifications=len(batch.notices),
                outcome=recorded,
                http_status=result.http_status,
            )
    return report


async def _send(
    engine: AsyncEngine, outbound: Outbound, batch: outbox.Batch, *, now: datetime
) -> outbox.SendResult:
    # One notice, or several shaped into one message for a channel people read.
    notice = batch.notices[0]
    outgoing = Outgoing(
        message=messages.compose_batch(batch.notices, app_base_url=outbound.app_base_url),
        message_id=str(batch.id),
        dedup_key=str(notice.incident_id),
        subject=notice.subject_label,
        opened_at=notice.opened_at,
        resolved_at=notice.resolved_at,
    )
    return await send(engine, outbound, batch.channel, outgoing, now=now)


def _count(report: Delivered, outcome: str) -> None:
    if outcome == "delivered":
        report.delivered += 1
    elif outcome == "retrying":
        report.retrying += 1
    else:
        report.failed += 1
