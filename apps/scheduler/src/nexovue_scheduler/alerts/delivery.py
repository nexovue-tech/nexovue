"""Delivering what the outbox holds: claim, send outside any transaction, record.

Called by the fast-path task that `record_outcome` dispatches after a commit, and
by the sweep beat runs every 15 seconds for anything the fast path missed
(ADR-013). Both may run at once; the claim's locks and leases make that safe.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final

from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import messages, outbox
from nexovue_scheduler.alerts.dispatch import Outbound, Outgoing, send
from nexovue_scheduler.releases import facts

logger = get_logger(__name__)

# One channel per claim, so its lease starts just before its send. Taking several
# and sending them in turn let the last leases lapse while earlier sends ran, and
# another worker then sent them too.
CHANNELS_PER_CLAIM: Final = 1

# Stop claiming after this long, well inside the task's time limit, so a worker
# never holds leases it will not live to use.
BUDGET_SECONDS: Final = 20.0

# F28 FR44's read, including the wait for a connection (D51; a guess). Against the
# claim's 60-second lease, and at most one per claim, since a claim holds one channel.
RELEASE_READ_SECONDS: Final = 0.5


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
        for claimed_batch in claimed.batches:
            batch = await with_release_line(engine, claimed_batch)
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


async def with_release_line(engine: AsyncEngine, batch: outbox.Batch) -> outbox.Batch:
    """The batch with FR44's facts in its one opened service finding's subject (D51).

    Read after the claim committed, on a connection of its own, so a statement that
    fails or hangs cannot touch the claim's transaction. A grouped message names each
    notice in a line, and a project finding names no service, so neither carries it.
    On any failure the line is left out and the alert goes as it is. Never stored, so
    findings and outbox rows are the same with or without releases (AC42)."""
    if len(batch.notices) != 1:
        return batch
    notice = batch.notices[0]
    subject = notice.subject
    if (
        notice.kind != "opened"
        or subject.kind != "finding"
        or not subject.key.startswith(outbox.SERVICE_KEY_PREFIX)
    ):
        return batch
    org_id, project_id = batch.channel.org_id, notice.project_id
    service = subject.key.removeprefix(outbox.SERVICE_KEY_PREFIX)
    at = subject.opened_at
    try:
        async with asyncio.timeout(RELEASE_READ_SECONDS), engine.connect() as connection:
            before = await facts.release_before(
                connection, org_id=org_id, project_id=project_id, service=service, at=at
            )
            coverage = await facts.coverage(
                connection, org_id=org_id, project_id=project_id, service=service, at=at
            )
    except Exception as unread:  # any failure leaves the line out; the alert goes on
        logger.warning(
            "release line left out",
            batch_id=str(batch.id),
            reason=type(unread).__name__,
        )
        return batch
    released = {
        "version": None if before is None else before.version,
        "started_at": None if before is None else before.started_at.isoformat(),
        "already_running": before is not None and before.already_running,
        "coverage": coverage.value,
    }
    told = replace(subject, detail={**subject.detail, messages.RELEASE_FACTS: released})
    return replace(batch, notices=(replace(notice, subject=told),))


async def _send(
    engine: AsyncEngine, outbound: Outbound, batch: outbox.Batch, *, now: datetime
) -> outbox.SendResult:
    # One notice, or several shaped into one message for a channel people read.
    notice = batch.notices[0]
    subject = notice.subject
    outgoing = Outgoing(
        message=messages.compose_batch(batch.notices, app_base_url=outbound.app_base_url),
        message_id=str(batch.id),
        dedup_key=notice.pagerduty_key,
        noted=notice.noted,
        subject_kind=subject.kind,
        subject_id=str(subject.id),
        subject=subject.label,
        opened_at=subject.opened_at,
        resolved_at=subject.closed_at,
    )
    return await send(engine, outbound, batch.channel, outgoing, now=now)


def _count(report: Delivered, outcome: str) -> None:
    if outcome == "delivered":
        report.delivered += 1
    elif outcome == "retrying":
        report.retrying += 1
    else:
        report.failed += 1
