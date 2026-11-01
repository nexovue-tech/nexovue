"""Opening, escalating and resolving incidents, and writing what each tells whom.

Every function takes the caller's connection and never commits: an incident and
its notifications commit with whatever decided them, or not at all (ADR-013).

**One incident per breach** (BR1) is the database's to enforce — the partial
unique index on `(rule_id, subject_key) WHERE resolved_at IS NULL` — and this
reads the open one `FOR UPDATE` before deciding, so two decisions about one
subject serialize rather than race.

**A muted rule records and sends nothing** (BR5): the incident is written, marked
muted, and nothing about it is ever sent — not when it opens, escalates or
resolves — because a person who silenced a rule has said they will look.

**An incident on a service with an open finding is linked to it** (F6 BR2, AC20),
in the transaction that opens it: detection and a threshold rule watching one
service are one problem with two records, never two problems. The project job
links in the other direction when the finding comes second.

**A flapping subject is said once** (FR11). From the fifth opening in an hour the
incident is marked flapping and one *flapping* message goes out; after that,
people hear nothing about the subject until it has stayed resolved for 30
minutes. PagerDuty and webhooks are still told when the flapping incident
resolves, so an alert a machine opened is one it can close.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform.alerts.channels import READ_BY_PEOPLE
from nexovue_platform.alerts.model import SEVERITY_RANK, service_subject
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.alerts.rules import Rule

logger = get_logger(__name__)

# FR11: more than four openings in an hour is flapping, and it ends when the subject
# has stayed resolved this long.
FLAP_OPENINGS: Final = 4
FLAP_WINDOW: Final = timedelta(hours=1)
FLAP_QUIET: Final = timedelta(minutes=30)

# FR13: a check's opening waits this long for the others that fail with it, so a
# shared outage arrives as one message listing them. Only for channels people read.
GROUPING_WAIT_SECONDS: Final = 30

# FR10: at most this many repeats of one incident.
MAX_REPEATS: Final = 5

_HUMAN_KINDS: Final = sorted(READ_BY_PEOPLE)
SERVICE_PREFIX: Final = service_subject("")

_OPEN_INCIDENT: Final = text(
    """
    SELECT id, severity, muted, flapping FROM incidents
    WHERE org_id = :org_id AND rule_id = :rule_id AND subject_key = :subject_key
      AND resolved_at IS NULL
    FOR UPDATE
    """
)
_RECENT_OPENINGS: Final = text(
    """
    SELECT count(*) FROM incidents
    WHERE org_id = :org_id AND rule_id = :rule_id AND subject_key = :subject_key
      AND opened_at >= :since
    """
)
# When the subject's last flapping spell ended. Openings before it were counted
# once already; a subject that settled starts counting again from zero.
_LAST_FLAP_ENDED: Final = text(
    """
    SELECT max(resolved_at) FROM incidents
    WHERE org_id = :org_id AND rule_id = :rule_id AND subject_key = :subject_key AND flapping
    """
)
_LAST_INCIDENT: Final = text(
    """
    SELECT flapping, resolved_at FROM incidents
    WHERE org_id = :org_id AND rule_id = :rule_id AND subject_key = :subject_key
    ORDER BY opened_at DESC
    LIMIT 1
    """
)
_INSERT_INCIDENT: Final = text(
    """
    INSERT INTO incidents
        (org_id, project_id, rule_id, rule_name, condition_kind, subject_key, subject_label,
         severity, opened_at, muted, flapping, detail)
    VALUES
        (:org_id, :project_id, :rule_id, :rule_name, :condition_kind, :subject_key,
         :subject_label, :severity, :at, :muted, :flapping, CAST(:detail AS jsonb))
    ON CONFLICT (rule_id, subject_key) WHERE resolved_at IS NULL DO NOTHING
    RETURNING id
    """
)
# The kinds of incident a finding is linked to when they share a service (F6 BR2).
# Check and new-error subjects name no service.
SERVICE_KINDS: Final = frozenset({"service_error_rate", "service_latency_p95", "service_silent"})
_LINK_FINDING: Final = text(
    """
    INSERT INTO finding_incidents (org_id, finding_id, incident_id)
    SELECT :org_id, findings.id, :incident_id
    FROM findings
    WHERE findings.org_id = :org_id AND findings.project_id = :project_id
      AND findings.scope = 'service' AND findings.service_name = :service_name
      AND findings.state = 'open'
    ON CONFLICT DO NOTHING
    """
)
_ESCALATE: Final = text(
    """
    UPDATE incidents SET severity = :severity, detail = CAST(:detail AS jsonb)
    WHERE org_id = :org_id AND id = :incident_id
    """
)
_RESOLVE: Final = text(
    """
    UPDATE incidents SET resolved_at = :at
    WHERE org_id = :org_id AND id = :incident_id
    """
)
_CLOSE_EVENT: Final = text(
    """
    UPDATE incidents SET resolved_at = :at, is_event = true
    WHERE org_id = :org_id AND id = :incident_id
    """
)
# The channels an incident's opening was written for. Escalation, repeats and
# recovery go to exactly those, so a PagerDuty alert that was triggered is the one
# resolved, whatever the rule's channels have become since.
_TOLD_CHANNELS: Final = text(
    """
    SELECT DISTINCT notifications.channel_id, notification_channels.kind
    FROM notifications
    JOIN notification_channels ON notification_channels.id = notifications.channel_id
    WHERE notifications.org_id = :org_id AND notifications.incident_id = :incident_id
      AND notifications.kind IN ('opened', 'flapping')
    """
)
_INSERT_NOTIFICATIONS: Final = text(
    """
    INSERT INTO notifications
        (org_id, project_id, incident_id, channel_id, kind, sequence, severity, deliver_after,
         created_at)
    SELECT :org_id, :project_id, :incident_id, notification_channels.id, :kind, :sequence,
           :severity,
           CASE WHEN CAST(:group_wait AS integer) > 0
                     AND notification_channels.kind = ANY(CAST(:human_kinds AS text[]))
                THEN CAST(:at AS timestamptz) + make_interval(secs => CAST(:group_wait AS integer))
                ELSE CAST(:at AS timestamptz) END,
           CAST(:at AS timestamptz)
    FROM notification_channels
    WHERE notification_channels.org_id = :org_id
      AND notification_channels.id = ANY(CAST(:channel_ids AS uuid[]))
    ON CONFLICT (incident_id, channel_id, kind, sequence) DO NOTHING
    RETURNING id
    """
)
# Across organizations on purpose: one sweep serves every rule with a repeat set.
# Each row carries its own `org_id`, and the notifications written name it.
_DUE_REPEATS: Final = text(
    f"""
    SELECT incidents.id, incidents.org_id, incidents.project_id, incidents.severity,
           coalesce(max(notifications.sequence)
                    FILTER (WHERE notifications.kind = 'repeated'), 0) AS repeats,
           array_agg(DISTINCT notifications.channel_id)
               FILTER (WHERE notifications.kind = 'opened') AS channel_ids
    FROM incidents
    JOIN alert_rules
        ON alert_rules.id = incidents.rule_id AND alert_rules.org_id = incidents.org_id
    JOIN notifications
        ON notifications.incident_id = incidents.id AND notifications.org_id = incidents.org_id
    WHERE incidents.resolved_at IS NULL
      AND (CAST(:org_id AS uuid) IS NULL OR incidents.org_id = CAST(:org_id AS uuid))
      AND NOT incidents.muted AND NOT incidents.flapping AND NOT incidents.is_event
      AND alert_rules.enabled AND alert_rules.renotify_minutes IS NOT NULL
      AND (alert_rules.muted_until IS NULL OR alert_rules.muted_until <= :now)
    GROUP BY incidents.id, alert_rules.renotify_minutes
    HAVING coalesce(max(notifications.sequence)
                    FILTER (WHERE notifications.kind = 'repeated'), 0) < {MAX_REPEATS}
       AND max(notifications.created_at)
               FILTER (WHERE notifications.kind IN ('opened', 'escalated', 'repeated'))
           <= :now - make_interval(mins => alert_rules.renotify_minutes)
    LIMIT 200
    """
)


@dataclass(frozen=True, slots=True)
class Subject:
    """What an incident is about: its key for identity, its label for people."""

    key: str
    label: str


async def fire(
    connection: AsyncConnection,
    rule: Rule,
    subject: Subject,
    *,
    severity: str,
    at: datetime,
    detail: dict[str, Any],
) -> int:
    """The condition holds for this subject. Opens an incident, or escalates the
    open one if this is worse. Returns how many notifications were written."""
    keys = {"org_id": rule.org_id, "rule_id": rule.id, "subject_key": subject.key}
    current = (await connection.execute(_OPEN_INCIDENT, keys)).first()
    if current is None:
        return await _open(connection, rule, subject, severity=severity, at=at, detail=detail)
    if SEVERITY_RANK[severity] <= SEVERITY_RANK[current.severity]:
        # Same or better: still the one breach, and nothing new to say (BR1).
        return 0

    await connection.execute(
        _ESCALATE,
        {
            "org_id": rule.org_id,
            "incident_id": current.id,
            "severity": severity,
            "detail": json.dumps(detail),
        },
    )
    if current.muted or current.flapping or rule.muted_at(at):
        return 0
    told = await _told_channels(connection, rule.org_id, current.id)
    return await _notify(
        connection,
        rule,
        current.id,
        tuple(told),
        kind="escalated",
        severity=severity,
        at=at,
    )


async def clear(
    connection: AsyncConnection,
    rule: Rule,
    subject: Subject,
    *,
    at: datetime,
    event: bool = False,
) -> int:
    """The condition no longer holds. Resolves the open incident, if there is one.

    `event` closes an incident that was only ever a moment — a new error group —
    quietly, marked as an event: there was nothing to recover from, so nobody is
    told it recovered."""
    keys = {"org_id": rule.org_id, "rule_id": rule.id, "subject_key": subject.key}
    current = (await connection.execute(_OPEN_INCIDENT, keys)).first()
    if current is None:
        return 0
    if event:
        await connection.execute(
            _CLOSE_EVENT, {"org_id": rule.org_id, "incident_id": current.id, "at": at}
        )
        return 0
    await connection.execute(_RESOLVE, {"org_id": rule.org_id, "incident_id": current.id, "at": at})
    told = await _told_channels(connection, rule.org_id, current.id)
    if current.flapping or current.muted or rule.muted_at(at):
        # People hear nothing more (a flap was said once; a mute is BR5); a machine
        # that opened an alert for it is still told it closed, or it stays open there.
        told = {channel: kind for channel, kind in told.items() if kind not in READ_BY_PEOPLE}
    return await _notify(
        connection,
        rule,
        current.id,
        tuple(told),
        kind="resolved",
        severity=current.severity,
        at=at,
    )


async def renotify_due(
    connection: AsyncConnection, *, now: datetime, org_id: uuid.UUID | None = None
) -> int:
    """Repeat every open incident whose rule asks for it and whose last word is older
    than the interval (FR10). Returns how many notifications were written."""
    written = 0
    for row in (await connection.execute(_DUE_REPEATS, {"now": now, "org_id": org_id})).fetchall():
        if not row.channel_ids:
            continue
        result = await connection.execute(
            _INSERT_NOTIFICATIONS,
            {
                "org_id": row.org_id,
                "project_id": row.project_id,
                "incident_id": row.id,
                "channel_ids": list(row.channel_ids),
                "kind": "repeated",
                "sequence": row.repeats + 1,
                "severity": row.severity,
                "at": now,
                "group_wait": 0,
                "human_kinds": _HUMAN_KINDS,
            },
        )
        written += len(result.fetchall())
    return written


async def _open(
    connection: AsyncConnection,
    rule: Rule,
    subject: Subject,
    *,
    severity: str,
    at: datetime,
    detail: dict[str, Any],
) -> int:
    muted = rule.muted_at(at)
    keys = {"org_id": rule.org_id, "rule_id": rule.id, "subject_key": subject.key}
    flap_ended = (await connection.execute(_LAST_FLAP_ENDED, keys)).scalar_one_or_none()
    since = at - FLAP_WINDOW if flap_ended is None else max(at - FLAP_WINDOW, flap_ended)
    earlier = (await connection.execute(_RECENT_OPENINGS, keys | {"since": since})).scalar_one()
    last = (await connection.execute(_LAST_INCIDENT, keys)).first()
    still_flapping = bool(
        last is not None
        and last.flapping
        and (last.resolved_at is None or at - last.resolved_at < FLAP_QUIET)
    )
    starts_flapping = not still_flapping and earlier >= FLAP_OPENINGS
    flapping = still_flapping or starts_flapping

    incident_id = (
        await connection.execute(
            _INSERT_INCIDENT,
            {
                "org_id": rule.org_id,
                "project_id": rule.project_id,
                "rule_id": rule.id,
                "rule_name": rule.name,
                "condition_kind": rule.condition_kind,
                "subject_key": subject.key,
                "subject_label": subject.label,
                "severity": severity,
                "at": at,
                "muted": muted,
                "flapping": flapping,
                "detail": json.dumps(detail | ({"openings": earlier + 1} if flapping else {})),
            },
        )
    ).scalar_one_or_none()
    if incident_id is not None and rule.condition_kind in SERVICE_KINDS:
        await connection.execute(
            _LINK_FINDING,
            {
                "org_id": rule.org_id,
                "project_id": rule.project_id,
                "incident_id": incident_id,
                "service_name": subject.key.removeprefix(SERVICE_PREFIX),
            },
        )
    if incident_id is None or muted or still_flapping:
        # None: another decision opened it between our read and our write, and that
        # decision wrote its own notifications. Still flapping: said once already.
        return 0
    return await _notify(
        connection,
        rule,
        incident_id,
        rule.channel_ids,
        kind="flapping" if starts_flapping else "opened",
        severity=severity,
        at=at,
        # Only a check's opening waits to be grouped (FR13); a flapping notice is
        # itself the one message about a subject.
        group_wait=(
            GROUPING_WAIT_SECONDS
            if rule.condition_kind == "check_state" and not starts_flapping
            else 0
        ),
    )


async def _told_channels(
    connection: AsyncConnection, org_id: uuid.UUID, incident_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    rows = await connection.execute(_TOLD_CHANNELS, {"org_id": org_id, "incident_id": incident_id})
    return {row.channel_id: row.kind for row in rows}


async def _notify(
    connection: AsyncConnection,
    rule: Rule,
    incident_id: uuid.UUID,
    channel_ids: tuple[uuid.UUID, ...],
    *,
    kind: str,
    severity: str,
    at: datetime,
    group_wait: int = 0,
) -> int:
    if not channel_ids:
        return 0
    written = await connection.execute(
        _INSERT_NOTIFICATIONS,
        {
            "org_id": rule.org_id,
            "project_id": rule.project_id,
            "incident_id": incident_id,
            "channel_ids": list(channel_ids),
            "kind": kind,
            "sequence": 0,
            "severity": severity,
            "at": at,
            "group_wait": group_wait,
            "human_kinds": _HUMAN_KINDS,
        },
    )
    return len(written.fetchall())


async def judged_alone(
    connection: AsyncConnection, rule: Rule, judge: Callable[[], Awaitable[int]]
) -> int:
    """One subject's judgment inside a savepoint. What a sweep judges comes from
    customers' telemetry, and a value the database refuses (a NUL in a service name)
    must cost that one judgment — not the rule's other subjects, nor every tenant the
    sweep has yet to reach."""
    try:
        async with connection.begin_nested():
            return await judge()
    except Exception:
        logger.exception(
            "could not judge one alert subject; the others go on",
            rule_id=str(rule.id),
            org_id=str(rule.org_id),
        )
        return 0
