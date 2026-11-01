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
subject, so they get one event per notification and are not shaped.

**A notification has one subject** (ADR-022): an incident, a finding, or — for
detection stopping and running again (D24) — the project itself. The claim reads
each through one `Subject`, so everything after it treats them alike.

**One problem is one PagerDuty alert, whichever fires first** (D29, D35). A
finding and an incident linked on one service share the alert of whichever
reached the channel first; the other's triggers are notes on it. Each row's key
is fixed at its subject's first claim there and kept, and the alert is resolved
only by the last of them to end: PagerDuty drops events for a resolved alert and
opens a new one on the next trigger, so an early resolve would page again.

**These statements read across organizations on purpose.** Delivery serves every
tenant from one queue; each row carries its own `org_id`, and every write back
names it.
"""

from __future__ import annotations

import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from nexovue_platform.alerts.channels import READ_BY_PEOPLE
from nexovue_platform.alerts.model import ENDING_KINDS
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
ALERT_STILL_OPEN_REASON: Final = (
    "not sent: the PagerDuty alert stays open for a linked problem that is still open"
)

# The channel whose alerts one problem shares (D35). A webhook keys on each subject.
PAGERDUTY: Final = "pagerduty"

# A finding's notices are routed by rules of this kind; a project's are detection's own.
ANOMALY_FINDING: Final = "anomaly_finding"
DETECTION: Final = "detection"

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

# A notification waits while an earlier one of the same subject on the same
# channel waits on a retry, so *resolved* never overtakes *opened*. Rows only held
# by quiet hours or the burst cap (never attempted) do not block: a critical must
# not wait for a held warning. A project's own notices name neither subject, so
# the project and both nulls make their subject.
_IN_ORDER: Final = """
    NOT EXISTS (
        SELECT 1 FROM notifications AS earlier
        WHERE earlier.org_id = notifications.org_id
          AND earlier.channel_id = notifications.channel_id
          AND earlier.project_id = notifications.project_id
          AND earlier.incident_id IS NOT DISTINCT FROM notifications.incident_id
          AND earlier.finding_id IS NOT DISTINCT FROM notifications.finding_id
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
# A subject's own PagerDuty key: the kind leads, so a finding and an incident never
# share one by accident.
_OWN_KEY: Final = """
    CASE WHEN notifications.incident_id IS NOT NULL
             THEN 'incident:' || CAST(notifications.incident_id AS text)
         WHEN notifications.finding_id IS NOT NULL
             THEN 'finding:' || CAST(notifications.finding_id AS text)
         ELSE 'project:' || CAST(notifications.project_id AS text)
    END
"""


def _open_for_another(key: str) -> str:
    """Whether the alert `key` names on this row's channel still carries a subject
    other than this row's that has not ended."""
    return f"""
    EXISTS (
        SELECT 1 FROM notifications AS member
        LEFT JOIN incidents
            ON incidents.id = member.incident_id AND incidents.org_id = member.org_id
        LEFT JOIN findings
            ON findings.id = member.finding_id AND findings.org_id = member.org_id
        WHERE member.org_id = notifications.org_id
          AND member.channel_id = notifications.channel_id
          AND member.dedup_key = {key}
          AND NOT (member.incident_id IS NOT DISTINCT FROM notifications.incident_id
                   AND member.finding_id IS NOT DISTINCT FROM notifications.finding_id)
          AND ((member.incident_id IS NOT NULL AND incidents.resolved_at IS NULL)
               OR findings.state = 'open')
    )
    """


_ENDING: Final = ", ".join(f"'{kind}'" for kind in sorted(ENDING_KINDS))
# Fixed once per subject and channel: the key its earlier rows went under; else, for
# news rather than an ending, the alert of a linked subject that is still open
# there (either order, D35); else its own. Linked through a finding, two incidents
# share the first one's alert too, because the finding's rows carry that key.
_PIN_KEY: Final = text(
    f"""
    UPDATE notifications SET dedup_key = coalesce(
        (
            SELECT earlier.dedup_key FROM notifications AS earlier
            WHERE earlier.org_id = notifications.org_id
              AND earlier.channel_id = notifications.channel_id
              AND (earlier.incident_id = notifications.incident_id
                   OR earlier.finding_id = notifications.finding_id)
              AND earlier.dedup_key IS NOT NULL
            LIMIT 1
        ),
        (
            SELECT host.dedup_key
            FROM finding_incidents AS link
            JOIN notifications AS host
                ON host.org_id = link.org_id
               AND host.channel_id = notifications.channel_id
               AND ((link.finding_id = notifications.finding_id
                     AND host.incident_id = link.incident_id)
                    OR (link.incident_id = notifications.incident_id
                        AND host.finding_id = link.finding_id))
            WHERE notifications.kind NOT IN ({_ENDING})
              AND link.org_id = notifications.org_id
              AND (link.finding_id = notifications.finding_id
                   OR link.incident_id = notifications.incident_id)
              AND host.dedup_key IS NOT NULL
              AND {_open_for_another("host.dedup_key")}
            ORDER BY host.created_at, host.id
            LIMIT 1
        ),
        {_OWN_KEY}
    )
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[])) AND dedup_key IS NULL
    """
)
# An ending sent while the alert still carries another open subject would resolve
# that subject's alert too; the last of them to end resolves it instead.
_WITHHOLD_ENDING: Final = text(
    f"""
    UPDATE notifications
    SET state = 'dropped', outcome_reason = :reason, lease_until = NULL
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[]))
      AND kind IN ({_ENDING})
      AND {_open_for_another("notifications.dedup_key")}
    RETURNING id
    """
)
_TAKEN: Final = text(
    f"""
    SELECT notifications.id, notifications.project_id, notifications.incident_id,
           notifications.finding_id, notifications.kind, notifications.severity,
           notifications.created_at, notifications.attempts,
           incidents.rule_id, incidents.rule_name, incidents.condition_kind,
           incidents.subject_key, incidents.subject_label, incidents.opened_at,
           incidents.resolved_at, incidents.detail,
           findings.scope, findings.service_name, findings.title, findings.signals,
           findings.started_at, findings.closed_at, findings.closed_reason,
           projects.name AS project_name,
           finding_rule.id AS finding_rule_id, finding_rule.name AS finding_rule_name,
           coalesce(notifications.dedup_key, {_OWN_KEY}) AS pagerduty_key,
           {_OWN_KEY} AS own_key,
           adopted.shifts AS adopted_shifts
    FROM notifications
    JOIN projects
        ON projects.id = notifications.project_id AND projects.org_id = notifications.org_id
    LEFT JOIN incidents
        ON incidents.id = notifications.incident_id AND incidents.org_id = notifications.org_id
    LEFT JOIN findings
        ON findings.id = notifications.finding_id AND findings.org_id = notifications.org_id
    -- A finding's rows name no rule: one rule of its kind that sends to this channel
    -- names the message, while one exists.
    LEFT JOIN LATERAL (
        SELECT alert_rules.id, alert_rules.name
        FROM alert_rules
        JOIN alert_rule_channels
            ON alert_rule_channels.rule_id = alert_rules.id
           AND alert_rule_channels.org_id = alert_rules.org_id
        WHERE notifications.finding_id IS NOT NULL
          AND notifications.kind <> 'adopted'
          AND alert_rules.org_id = notifications.org_id
          AND alert_rules.project_id = notifications.project_id
          AND alert_rules.condition_kind = 'anomaly_finding'
          AND alert_rule_channels.channel_id = notifications.channel_id
        ORDER BY alert_rules.name, alert_rules.id
        LIMIT 1
    ) AS finding_rule ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(jsonb_build_object('signal', shifts.signal, 'ratio', shifts.ratio)
                         ORDER BY shifts.signal) AS shifts
        FROM baseline_shifts AS shifts
        WHERE notifications.kind = 'adopted'
          AND shifts.org_id = notifications.org_id
          AND shifts.project_id = notifications.project_id
          AND shifts.service_name = findings.service_name
          AND shifts.reason = 'adopted'
          AND shifts.from_minute >= findings.started_at
    ) AS adopted ON true
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


SubjectKind = Literal["incident", "finding", "project"]

# A subject's name can come from telemetry, which anyone with a public browser key can
# send. Long enough for any real name; short enough that one cannot bury the message.
NAME_LIMIT: Final = 200
# C0 and C1 controls and the Unicode line and paragraph separators: anything that could
# start a line the message did not write.
_CONTROLS: Final = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")


def one_line(name: str) -> str:
    """A name as one line of at most `NAME_LIMIT` characters, so it can only ever be
    words inside a message, never a line of its own."""
    flat = _CONTROLS.sub(" ", name).strip()
    return flat if len(flat) <= NAME_LIMIT else flat[: NAME_LIMIT - 1] + "…"


@dataclass(frozen=True, slots=True)
class Subject:
    """What a notification is about, whichever kind it is (ADR-022). Its label is
    flattened here, once, so every message and every machine payload carries it as
    one short line (`docs/product/setup/alerts/webhooks.md`)."""

    kind: SubjectKind
    # The incident's, the finding's, or the project's own id.
    id: uuid.UUID
    # Identity for people and machines: the incident's subject key, the finding's
    # service, or the project.
    key: str
    # Words for people: a check, a service or a project. Telemetry text for a
    # service, so it is escaped by every channel that shows it.
    label: str
    opened_at: datetime
    closed_at: datetime | None
    rule_id: uuid.UUID | None
    rule_name: str | None
    condition_kind: str
    detail: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", one_line(self.label))


@dataclass(frozen=True, slots=True)
class Notice:
    """One notification in a batch, with the subject it is about."""

    id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    severity: str
    created_at: datetime
    # Attempts already made, before the one in hand.
    attempts: int
    subject: Subject
    # The PagerDuty alert this notice goes to: its subject's own, or, for a
    # PagerDuty channel, a linked subject's that reached it first (D29, D35).
    pagerduty_key: str
    # It goes to a linked subject's alert, as a note there rather than a page.
    noted: bool = False


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
        if row.kind == PAGERDUTY:
            withheld = await _one_alert_per_problem(connection, row.org_id, ids)
            claimed.dropped += len(withheld)
            ids = [taken for taken in ids if taken not in withheld]
            if not ids:
                continue
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


async def _one_alert_per_problem(
    connection: AsyncConnection, org_id: uuid.UUID, ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Fix each taken row's PagerDuty key, and withhold an ending whose alert still
    carries another open subject (D35). Returns the withheld rows."""
    keys = {"org_id": org_id, "ids": ids}
    await connection.execute(_PIN_KEY, keys)
    withheld = await connection.execute(
        _WITHHOLD_ENDING, keys | {"reason": ALERT_STILL_OPEN_REASON}
    )
    return {row.id for row in withheld}


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
        kind=row.kind,
        severity=row.severity,
        created_at=row.created_at,
        attempts=row.attempts,
        subject=_subject(row),
        pagerduty_key=row.pagerduty_key,
        noted=row.pagerduty_key != row.own_key,
    )


def _subject(row: Any) -> Subject:
    if row.incident_id is not None:
        return Subject(
            kind="incident",
            id=row.incident_id,
            key=row.subject_key,
            label=row.subject_label,
            opened_at=row.opened_at,
            closed_at=row.resolved_at,
            rule_id=row.rule_id,
            rule_name=row.rule_name,
            condition_kind=row.condition_kind,
            detail=row.detail,
        )
    if row.finding_id is not None:
        return Subject(
            kind="finding",
            id=row.finding_id,
            key=f"service:{row.service_name}" if row.service_name is not None else "project",
            label=row.service_name if row.service_name is not None else row.project_name,
            opened_at=row.started_at,
            closed_at=row.closed_at,
            rule_id=row.finding_rule_id,
            rule_name=row.finding_rule_name,
            condition_kind=ANOMALY_FINDING,
            detail={
                "scope": row.scope,
                "title": row.title,
                "signals": row.signals,
                "closed_reason": row.closed_reason,
                "project": row.project_name,
                "adopted_shifts": row.adopted_shifts or [],
            },
        )
    # Detection stopping and running again: the project is the subject (D24).
    return Subject(
        kind="project",
        id=row.project_id,
        key="project",
        label=row.project_name,
        opened_at=row.created_at,
        closed_at=None,
        rule_id=None,
        rule_name=None,
        condition_kind=DETECTION,
        detail={},
    )
