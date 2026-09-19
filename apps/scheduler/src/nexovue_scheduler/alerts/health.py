"""A channel that has not delivered for an hour is *failing*, and its organization's
owners hear about it — by email, never through the channel that is broken (FR21).

The next successful delivery makes it healthy again; that half is in
`outbox.record`, where the success is recorded.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from nexovue_platform.email import EmailError, EmailSender, Message
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts.messages import describe_duration
from nexovue_scheduler.alerts.senders.email import verified_owners

logger = get_logger(__name__)

FAILING_AFTER: Final = timedelta(hours=1)

KIND_NAMES: Final = {
    "owners": "email",
    "email": "email",
    "slack": "Slack",
    "teams": "Microsoft Teams",
    "discord": "Discord",
    "pagerduty": "PagerDuty",
    "webhook": "webhook",
}

# Across organizations on purpose, as delivery is. Newly failing, or failing and
# not yet reported because the last report could not be sent.
_TO_REPORT: Final = text(
    """
    SELECT id, org_id, kind, name, failing_since FROM notification_channels
    WHERE failing_since IS NOT NULL AND failing_since <= :cutoff
      AND (health = 'healthy' OR failing_reported_at IS NULL)
      AND (CAST(:org_id AS uuid) IS NULL OR org_id = CAST(:org_id AS uuid))
    ORDER BY id
    LIMIT 50
    FOR UPDATE SKIP LOCKED
    """
)
_MARK_FAILING: Final = text(
    "UPDATE notification_channels SET health = 'failing' WHERE org_id = :org_id AND id = :id"
)
_LAST_REASON: Final = text(
    """
    SELECT reason FROM notification_attempts
    WHERE org_id = :org_id AND channel_id = :id AND outcome <> 'delivered'
    ORDER BY attempted_at DESC LIMIT 1
    """
)
_REPORTED: Final = text(
    "UPDATE notification_channels SET failing_reported_at = :now "
    "WHERE org_id = :org_id AND id = :id"
)


@dataclass(frozen=True, slots=True)
class _Failing:
    id: uuid.UUID
    org_id: uuid.UUID
    kind: str
    name: str
    failing_since: datetime
    reason: str
    owners: list[str]


async def report_failing_channels(
    engine: AsyncEngine,
    *,
    email_sender: EmailSender | None,
    app_base_url: str,
    now: datetime,
    org_id: uuid.UUID | None = None,
) -> int:
    """Mark and report every channel that has failed for an hour. Returns how many
    reports were sent."""
    async with engine.begin() as connection:
        found = (
            await connection.execute(_TO_REPORT, {"cutoff": now - FAILING_AFTER, "org_id": org_id})
        ).fetchall()
        failing: list[_Failing] = []
        for row in found:
            keys = {"org_id": row.org_id, "id": row.id}
            await connection.execute(_MARK_FAILING, keys)
            reason = (await connection.execute(_LAST_REASON, keys)).scalar_one_or_none()
            failing.append(
                _Failing(
                    id=row.id,
                    org_id=row.org_id,
                    kind=row.kind,
                    name=row.name,
                    failing_since=row.failing_since,
                    reason=reason or "no delivery succeeded",
                    owners=await verified_owners(connection, row.org_id),
                )
            )

    reported = 0
    base = app_base_url.rstrip("/")
    for channel in failing:
        if email_sender is None or not channel.owners:
            # Nowhere to say it, and retrying will not make somewhere. Said once in the
            # log; the console still shows the channel as failing.
            logger.error(
                "an alert channel is failing and its owners cannot be told",
                channel_id=str(channel.id),
                channel_kind=channel.kind,
            )
            await _mark_reported(engine, channel, now)
            continue
        data = {
            "channel_name": channel.name,
            "channel_kind": KIND_NAMES.get(channel.kind, channel.kind),
            "failing_for": describe_duration(channel.failing_since, now),
            "reason": channel.reason.rstrip("."),
            "link": f"{base}/settings/channels/{channel.id}",
        }
        try:
            for owner in channel.owners:
                await email_sender.send(
                    to=owner,
                    message=Message.CHANNEL_FAILING,
                    data=data,
                    # Retried next sweep; an owner the last try reached is not mailed again.
                    idempotency_key=_report_key(channel, owner),
                )
        except EmailError as failure:
            logger.error("could not report a failing alert channel", reason=str(failure))
            continue
        await _mark_reported(engine, channel, now)
        reported += 1
    return reported


async def _mark_reported(engine: AsyncEngine, channel: _Failing, now: datetime) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            _REPORTED, {"org_id": channel.org_id, "id": channel.id, "now": now}
        )


def _report_key(channel: _Failing, owner: str) -> str:
    """One per failing spell and owner, hashed to stay inside the provider's limit."""
    spell = f"{channel.id}/{channel.failing_since.isoformat()}/{owner}"
    return f"channel-failing-{hashlib.sha256(spell.encode()).hexdigest()}"
