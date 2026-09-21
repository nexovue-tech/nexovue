"""Claiming due notifications and recording what delivering them did (ADR-013).

**A claim is a lease, not a lock held across the network.** Rows are marked
`delivering` with a lease in one short transaction; the message is sent outside
any transaction; the outcome is recorded in another. A worker that dies between
the two leaves its rows to be claimed again when the lease ends — the one
duplicate the design allows, which `webhook-id` and PagerDuty's `dedup_key` let a
receiver collapse.

**At most one batch in flight per channel.** A claim locks the channel rows it
takes (`SKIP LOCKED`), and a channel with a live lease is not offered again, so
two workers never talk to one receiver at once and a slow receiver holds one
delivery slot rather than all of them (NFR5).

**What people read is shaped here, where everything due for one channel is visible
together** (design, *Shaping at delivery time*). For email, Slack, Teams and
Discord, every row due at once goes as one message — which is what makes a shared
outage one message (FR13), a quiet-hours digest one message (FR15), and a burst
past ten a minute one summary (FR12). Warnings wait out quiet hours; criticals
never do (BR4). PagerDuty and webhooks are read by machines that key on each
incident, so they get one event per notification and are not shaped.

**These statements read across organizations on purpose.** Delivery serves every
tenant from one queue; each row carries its own `org_id`, and every write back
names it.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from nexovue_platform.alerts.channels import READ_BY_PEOPLE
from nexovue_platform.alerts.quiet_hours import parse_quiet_hours, quiet_until
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# Long enough for one send's 10-second timeout and its bookkeeping; short enough
# that a dead worker's notification is late by a minute, not an hour.
LEASE: Final = timedelta(seconds=60)

# Retried for up to an hour from the first attempt (FR19), then final.
RETRY_WINDOW: Final = timedelta(hours=1)
FIRST_RETRY_SECONDS: Final = 30
LONGEST_RETRY_SECONDS: Final = 600
# A receiver's own `Retry-After` is honoured, up to the end of the window.
LONGEST_RETRY_AFTER_SECONDS: Final = 3600

MUTED_CHANNEL_REASON: Final = "the channel was muted"

# FR12: at most this many messages a minute to one channel; the rest wait and go
# together as one.
BURST_LIMIT: Final = 10
BURST_WINDOW: Final = timedelta(minutes=1)

# The most rows one message carries. A digest past this is sent in parts.
MOST_IN_ONE_MESSAGE: Final = 50

_DUE: Final = """
    (notifications.state = 'pending' AND notifications.deliver_after <= :now)
    OR (notifications.state = 'delivering' AND notifications.lease_until < :now)
"""

# A notification waits while an earlier one of the same incident on the same
# channel waits on a retry, so *resolved* never overtakes *opened*. Rows only held
# by quiet hours or the burst cap (never attempted) do not block: a critical must
# not wait for a held warning.
_IN_ORDER: Final = """
    NOT EXISTS (
        SELECT 1 FROM notifications AS earlier
        WHERE earlier.org_id = notifications.org_id
          AND earlier.channel_id = notifications.channel_id
          AND earlier.incident_id = notifications.incident_id
          AND earlier.created_at < notifications.created_at
          AND earlier.attempts > 0
          AND earlier.state IN ('pending', 'delivering')
    )
"""

_CLAIMABLE_CHANNELS: Final = text(
    f"""
    SELECT id, org_id, kind, name, config, sealed_secret, muted_until, quiet_hours
    FROM notification_channels
    WHERE EXISTS (
            SELECT 1 FROM notifications
            WHERE notifications.channel_id = notification_channels.id AND ({_DUE})
              AND {_IN_ORDER}
        )
      AND NOT EXISTS (
            SELECT 1 FROM notifications
            WHERE notifications.channel_id = notification_channels.id
              AND notifications.state = 'delivering' AND notifications.lease_until >= :now
        )
      AND (CAST(:org_id AS uuid) IS NULL OR org_id = CAST(:org_id AS uuid))
    ORDER BY id
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
    """
)
_DROP_DUE: Final = text(
    f"""
    UPDATE notifications
    SET state = 'dropped', outcome_reason = :reason, lease_until = NULL
    WHERE org_id = :org_id AND channel_id = :channel_id AND ({_DUE})
    """
)
_OLDEST_DUE: Final = text(
    f"""
    SELECT id, batch_id FROM notifications
    WHERE org_id = :org_id AND channel_id = :channel_id AND ({_DUE}) AND {_IN_ORDER}
    ORDER BY created_at, id
    LIMIT 1
    """
)
_ALL_DUE: Final = text(
    f"""
    SELECT id FROM notifications
    WHERE org_id = :org_id AND channel_id = :channel_id AND batch_id IS NULL AND ({_DUE})
      AND {_IN_ORDER}
    ORDER BY created_at, id
    LIMIT {MOST_IN_ONE_MESSAGE}
    """
)
_HOLD_WARNINGS: Final = text(
    f"""
    UPDATE notifications SET deliver_after = :until
    WHERE org_id = :org_id AND channel_id = :channel_id AND severity = 'warning'
      AND batch_id IS NULL AND ({_DUE})
    """
)
_HOLD_ALL: Final = text(
    f"""
    UPDATE notifications SET deliver_after = :until
    WHERE org_id = :org_id AND channel_id = :channel_id AND batch_id IS NULL AND ({_DUE})
    """
)
_RECENT_MESSAGES: Final = text(
    """
    SELECT count(DISTINCT batch_id), min(attempted_at) FROM notification_attempts
    WHERE org_id = :org_id AND channel_id = :channel_id AND attempted_at > :since
    """
)
_DUE_IN_BATCH: Final = text(
    f"""
    SELECT id FROM notifications
    WHERE org_id = :org_id AND channel_id = :channel_id AND batch_id = :batch_id AND ({_DUE})
    """
)
_TAKE: Final = text(
    """
    UPDATE notifications
    SET state = 'delivering', lease_until = :lease_until, batch_id = :batch_id
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[]))
    """
)
_TAKEN: Final = text(
    """
    SELECT notifications.id, notifications.project_id, notifications.incident_id,
           notifications.kind, notifications.severity, notifications.created_at,
           notifications.attempts,
           incidents.rule_id, incidents.rule_name, incidents.condition_kind,
           incidents.subject_key, incidents.subject_label, incidents.opened_at,
           incidents.resolved_at, incidents.detail
    FROM notifications
    JOIN incidents ON incidents.id = notifications.incident_id
    WHERE notifications.org_id = :org_id AND notifications.id = ANY(CAST(:ids AS uuid[]))
    ORDER BY notifications.created_at
    """
)
_FIRST_ATTEMPT: Final = text(
    """
    SELECT min(attempted_at) FROM notification_attempts
    WHERE org_id = :org_id AND batch_id = :batch_id
    """
)
_INSERT_ATTEMPT: Final = text(
    """
    INSERT INTO notification_attempts
        (org_id, channel_id, batch_id, attempted_at, outcome, http_status, reason, kind)
    VALUES (:org_id, :channel_id, :batch_id, :now, :outcome, :http_status, :reason, :kind)
    """
)
_DELIVERED: Final = text(
    """
    UPDATE notifications
    SET state = 'delivered', delivered_at = :now, lease_until = NULL, attempts = attempts + 1
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[])) AND lease_until = :lease_until
    """
)
_RETRY_LATER: Final = text(
    """
    UPDATE notifications
    SET state = 'pending', deliver_after = :retry_at, lease_until = NULL,
        attempts = attempts + 1, outcome_reason = :reason
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[])) AND lease_until = :lease_until
    """
)
_FAILED: Final = text(
    """
    UPDATE notifications
    SET state = 'failed', lease_until = NULL, attempts = attempts + 1, outcome_reason = :reason
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[])) AND lease_until = :lease_until
    """
)
_CHANNEL_DELIVERED: Final = text(
    """
    UPDATE notification_channels
    SET last_delivered_at = :now, failing_since = NULL, failing_reported_at = NULL,
        health = 'healthy'
    WHERE org_id = :org_id AND id = :channel_id
    """
)
_CHANNEL_FAILED: Final = text(
    """
    UPDATE notification_channels SET failing_since = coalesce(failing_since, :now)
    WHERE org_id = :org_id AND id = :channel_id
    """
)
_PRUNE_ATTEMPTS: Final = text("DELETE FROM notification_attempts WHERE attempted_at < :before")


@dataclass(frozen=True, slots=True)
class Channel:
    id: uuid.UUID
    org_id: uuid.UUID
    kind: str
    name: str
    config: dict[str, Any]
    sealed_secret: bytes | None


@dataclass(frozen=True, slots=True)
class Notice:
    """One notification in a batch, with the incident it is about."""

    id: uuid.UUID
    project_id: uuid.UUID
    incident_id: uuid.UUID
    kind: str
    severity: str
    created_at: datetime
    # Attempts already made, before the one in hand.
    attempts: int
    rule_id: uuid.UUID | None
    rule_name: str
    condition_kind: str
    subject_key: str
    subject_label: str
    opened_at: datetime
    resolved_at: datetime | None
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Batch:
    """What one message to one channel carries. Its id is the webhook's `webhook-id`,
    the same on every retry."""

    id: uuid.UUID
    channel: Channel
    notices: tuple[Notice, ...]
    # The claim's own lease: an outcome is recorded against the rows only while
    # they still carry it, so a worker that outlived its lease cannot undo the
    # claim that replaced it.
    lease_until: datetime

    @property
    def ids(self) -> list[uuid.UUID]:
        return [notice.id for notice in self.notices]


@dataclass(frozen=True, slots=True)
class SendResult:
    """What a receiver did with one batch.

    `retry` is a failure worth trying again: the network, a timeout, 429 or 5xx.
    `failed` is final: any other refusal, whose reason is recorded (FR19).
    """

    outcome: Literal["delivered", "retry", "failed"]
    http_status: int | None = None
    # In words, for a person reading the delivery log. Never a destination's secret.
    reason: str | None = None
    retry_after_seconds: int | None = None


@dataclass(slots=True)
class Claim:
    batches: list[Batch] = field(default_factory=list)
    dropped: int = 0
    # Channels that had due work, whether it was sent, dropped by a mute or held:
    # zero is the only sign there is nothing left to do.
    channels: int = 0


async def claim(
    connection: AsyncConnection,
    *,
    now: datetime,
    limit: int,
    org_id: uuid.UUID | None = None,
) -> Claim:
    """Take the oldest due batch from up to `limit` channels, each under a lease —
    every organization's, or one's."""
    claimed = Claim()
    channels = await connection.execute(
        _CLAIMABLE_CHANNELS, {"now": now, "limit": limit, "org_id": org_id}
    )
    for row in channels.fetchall():
        claimed.channels += 1
        keys = {"org_id": row.org_id, "channel_id": row.id, "now": now}
        if row.muted_until is not None and row.muted_until > now:
            # Written as `dropped` rather than left or deleted, so the delivery log
            # still shows what the mute withheld (BR5).
            dropped = await connection.execute(_DROP_DUE, keys | {"reason": MUTED_CHANNEL_REASON})
            claimed.dropped += dropped.rowcount
            continue

        if row.kind in READ_BY_PEOPLE:
            await _shape(connection, row, now=now)

        oldest = (await connection.execute(_OLDEST_DUE, keys)).first()
        if oldest is None:
            continue
        if oldest.batch_id is None and row.kind in READ_BY_PEOPLE:
            # Everything due for people goes as one message.
            everything = await connection.execute(_ALL_DUE, keys)
            batch_id, ids = uuid.uuid4(), [due.id for due in everything]
        elif oldest.batch_id is None:
            batch_id, ids = uuid.uuid4(), [oldest.id]
        else:
            # A retry: the same rows under the same id, so the receiver can tell it
            # has seen this delivery before (FR4).
            batch_id = oldest.batch_id
            in_batch = await connection.execute(_DUE_IN_BATCH, keys | {"batch_id": batch_id})
            ids = [taken.id for taken in in_batch]
        lease_until = now + LEASE
        await connection.execute(
            _TAKE,
            {"org_id": row.org_id, "ids": ids, "batch_id": batch_id, "lease_until": lease_until},
        )
        notices = await connection.execute(_TAKEN, {"org_id": row.org_id, "ids": ids})
        claimed.batches.append(
            Batch(
                id=batch_id,
                channel=Channel(
                    id=row.id,
                    org_id=row.org_id,
                    kind=row.kind,
                    name=row.name,
                    config=row.config,
                    sealed_secret=row.sealed_secret,
                ),
                notices=tuple(_notice(notice) for notice in notices),
                lease_until=lease_until,
            )
        )
    return claimed


async def _shape(connection: AsyncConnection, row: Any, *, now: datetime) -> None:
    """Hold what should not go yet: warnings in quiet hours, anything past the burst cap."""
    keys = {"org_id": row.org_id, "channel_id": row.id, "now": now}
    try:
        quiet = quiet_until(parse_quiet_hours(row.quiet_hours), now)
    except ValidationError:
        quiet = None  # a stored shape the API would not have accepted; send as usual
    if quiet is not None:
        await connection.execute(_HOLD_WARNINGS, keys | {"until": quiet})

    sent, first_sent = (
        await connection.execute(_RECENT_MESSAGES, keys | {"since": now - BURST_WINDOW})
    ).one()
    if sent >= BURST_LIMIT and first_sent is not None:
        await connection.execute(_HOLD_ALL, keys | {"until": first_sent + BURST_WINDOW})


async def record(
    connection: AsyncConnection, batch: Batch, result: SendResult, *, now: datetime
) -> str:
    """Record one attempt and move the batch on. Returns the attempt's outcome."""
    keys = {"org_id": batch.channel.org_id, "ids": batch.ids, "lease_until": batch.lease_until}
    outcome = result.outcome
    reason = result.reason
    # From the first attempt, not the decision: a warning held overnight by quiet
    # hours still gets its hour of retries in the morning.
    first_attempt = (
        await connection.execute(
            _FIRST_ATTEMPT, {"org_id": batch.channel.org_id, "batch_id": batch.id}
        )
    ).scalar_one() or now
    if outcome == "retry" and now - first_attempt >= RETRY_WINDOW:
        outcome, reason = "failed", f"gave up after an hour of retrying: {reason}"

    await connection.execute(
        _INSERT_ATTEMPT,
        {
            "org_id": batch.channel.org_id,
            "channel_id": batch.channel.id,
            "batch_id": batch.id,
            "now": now,
            "outcome": "retrying" if outcome == "retry" else outcome,
            "http_status": result.http_status,
            "reason": reason,
            "kind": "notification",
        },
    )
    channel_keys = {"org_id": batch.channel.org_id, "channel_id": batch.channel.id, "now": now}
    if outcome == "delivered":
        await connection.execute(_DELIVERED, keys | {"now": now})
        await connection.execute(_CHANNEL_DELIVERED, channel_keys)
        return "delivered"

    await connection.execute(_CHANNEL_FAILED, channel_keys)
    if outcome == "retry":
        # This attempt is recorded above, so it counts: the first retry waits 30 s.
        attempts = 1 + max(notice.attempts for notice in batch.notices)
        wait = retry_delay(attempts, result)
        await connection.execute(
            _RETRY_LATER,
            keys | {"retry_at": now + timedelta(seconds=wait), "reason": reason},
        )
        return "retrying"
    await connection.execute(_FAILED, keys | {"reason": reason})
    return "failed"


def retry_delay(attempts: int, result: SendResult) -> float:
    """Exponential from 30 seconds, capped at ten minutes, with jitter so a
    receiver that came back is not met by every channel's retry in one second."""
    base = min(FIRST_RETRY_SECONDS * 2 ** max(attempts - 1, 0), LONGEST_RETRY_SECONDS)
    # Spreading load, not a secret: `random` is the right tool.
    delay: float = base * random.uniform(0.8, 1.2)
    if result.retry_after_seconds is not None:
        delay = max(delay, float(min(result.retry_after_seconds, LONGEST_RETRY_AFTER_SECONDS)))
    return delay


async def prune_attempts(connection: AsyncConnection, *, before: datetime) -> int:
    """The delivery log is kept 30 days (FR20)."""
    return (await connection.execute(_PRUNE_ATTEMPTS, {"before": before})).rowcount


def _notice(row: Any) -> Notice:
    return Notice(
        id=row.id,
        project_id=row.project_id,
        incident_id=row.incident_id,
        kind=row.kind,
        severity=row.severity,
        created_at=row.created_at,
        attempts=row.attempts,
        rule_id=row.rule_id,
        rule_name=row.rule_name,
        condition_kind=row.condition_kind,
        subject_key=row.subject_key,
        subject_label=row.subject_label,
        opened_at=row.opened_at,
        resolved_at=row.resolved_at,
        detail=row.detail,
    )
