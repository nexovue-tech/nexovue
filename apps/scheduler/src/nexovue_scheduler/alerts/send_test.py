"""*Send test*: a labelled message through the real path, and its outcome (FR3).

Nothing is recorded in the delivery log — a test is not a notification — but a
success does mean the channel works, so it heals a failing channel as a delivery
would (FR21).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts.dispatch import Outbound, Outgoing, send
from nexovue_scheduler.alerts.messages import AlertMessage
from nexovue_scheduler.alerts.outbox import Channel, SendResult

NOT_FOUND: Final = "the channel no longer exists"

_CHANNEL: Final = text(
    """
    SELECT id, org_id, kind, name, config, sealed_secret FROM notification_channels
    WHERE org_id = :org_id AND id = :id
    """
)
_HEALED: Final = text(
    """
    UPDATE notification_channels
    SET last_delivered_at = :now, failing_since = NULL, failing_reported_at = NULL,
        health = 'healthy'
    WHERE org_id = :org_id AND id = :id
    """
)


@dataclass(frozen=True, slots=True)
class TestOutcome:
    delivered: bool
    http_status: int | None
    reason: str | None


async def send_test(
    engine: AsyncEngine,
    outbound: Outbound,
    *,
    org_id: uuid.UUID,
    channel_id: uuid.UUID,
    request_id: str,
    now: datetime,
) -> TestOutcome:
    async with engine.connect() as connection:
        row = (await connection.execute(_CHANNEL, {"org_id": org_id, "id": channel_id})).first()
    if row is None:
        return TestOutcome(delivered=False, http_status=None, reason=NOT_FOUND)
    channel = Channel(
        id=row.id,
        org_id=row.org_id,
        kind=row.kind,
        name=row.name,
        config=row.config,
        sealed_secret=row.sealed_secret,
    )
    base = outbound.app_base_url.rstrip("/")
    link = f"{base}/settings/channels/{channel.id}"
    dedup_key = f"nexovue-test-{request_id}"

    result = await send(
        engine, outbound, channel, _test(link, "opened", dedup_key=dedup_key), now=now
    )
    if result.outcome == "delivered" and channel.kind == "pagerduty":
        # A test must not leave someone paged: the trigger is resolved at once.
        result = await send(
            engine, outbound, channel, _test(link, "resolved", dedup_key=dedup_key), now=now
        )
    if result.outcome == "delivered":
        async with engine.begin() as connection:
            await connection.execute(_HEALED, {"org_id": org_id, "id": channel_id, "now": now})
    return _outcome(result)


def _test(link: str, kind: str, *, dedup_key: str) -> Outgoing:
    message = AlertMessage(
        kind=kind,
        severity="warning",
        summary="[test] A test message from nexovue",
        details=("This is a test. If you can read it, alerts sent to this channel arrive here.",),
        link=link,
        rule_name="Send test",
        manage_link=link,
    )
    return Outgoing(
        message=message,
        message_id=f"test-{uuid.uuid4()}",
        dedup_key=dedup_key,
        subject="Send test",
        opened_at=datetime.now(UTC),
        resolved_at=None,
    )


def _outcome(result: SendResult) -> TestOutcome:
    return TestOutcome(
        delivered=result.outcome == "delivered",
        http_status=result.http_status,
        reason=result.reason,
    )
