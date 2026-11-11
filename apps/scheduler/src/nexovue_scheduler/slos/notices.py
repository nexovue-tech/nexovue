"""F19's notices: once per target, whatever the rules (design, *Notices*; FR13b, FR19,
FR29).

- **Nothing left** (FR19): when a target's allowance reaches zero, one warning to the
  union of the channels its unmuted rules name, never withheld, even while a burn is
  open. Not while it is Building history. Said again only once the allowance has come
  back above 5% and run out again, and at most once in 7 days (both guesses). With no
  rule covering it nothing is sent; the screen shows it.
- **Not being judged** (FR13b): `report_blind`, below, from the delivery sweep.
- **Exclusion** (FR29): each new exclusion, one warning to the union of the channels its
  rules name, muted or not, or to the organization's owners with no rule, so removing a
  rule first cannot hide one. It says who left out how long from which target as
  "Someone": it holds no reason and no person (FR33).

Each notice is an incident closed as it opens (`incidents.fire_notice`), filed under the
first covering rule by name. Its `about_signal` stays null, which is what exempts it
from withholding (Step 5).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from nexovue_platform import slo
from nexovue_platform.alerts.model import slo_subject
from nexovue_platform.detection import DEFAULT_TIMEZONE
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.alerts import incidents
from nexovue_scheduler.alerts.rules import Rule
from nexovue_scheduler.detection.store import ProjectBinding
from nexovue_scheduler.slos import alerting, burns

if TYPE_CHECKING:
    from nexovue_scheduler.slos.job import Target

NOTICE_SEVERITY: Final = "warning"
# FR19's guesses: said again only after this long, and once the allowance is back above
# this share of what the window allows.
EXHAUSTED_REPEAT_AFTER: Final = timedelta(days=7)
EXHAUSTED_REARM_SHARE: Final = Decimal("0.05")
# How far back an exclusion is looked for that has had no notice yet: one written while
# judging was stopped is still told when it returns.
EXCLUSION_NOTICE_LOOKBACK: Final = timedelta(days=1)


def exhausted_subject(target_id: uuid.UUID) -> str:
    return f"{slo_subject(target_id)}:exhausted"


def exclusion_subject(target_id: uuid.UUID, exclusion_id: uuid.UUID) -> str:
    return f"{slo_subject(target_id)}:excluded:{exclusion_id}"


# Claims the warning once: two overlapping runs cannot both send it (AC23).
_CLAIM_EXHAUSTED: Final = text(
    """
    UPDATE slo_targets
    SET exhausted_armed = false, exhausted_warned_at = :at
    WHERE org_id = :org_id AND id = :id AND exhausted_armed
      AND (exhausted_warned_at IS NULL OR exhausted_warned_at <= :repeat_before)
    RETURNING id
    """
)
_REARM_EXHAUSTED: Final = text(
    """
    UPDATE slo_targets SET exhausted_armed = true
    WHERE org_id = :org_id AND id = :id AND NOT exhausted_armed
    """
)
# The project's new exclusions not yet told: no notice incident carries their subject.
_UNTOLD_EXCLUSIONS: Final = text(
    """
    SELECT e.id, e.slo_id, e.starts_at, e.ends_at
    FROM slo_exclusions AS e
    WHERE e.org_id = :org_id AND e.project_id = :project_id AND e.slo_id = ANY(:ids)
      AND e.created_at >= :since
      AND NOT EXISTS (
          SELECT 1 FROM incidents AS i
          WHERE i.org_id = e.org_id AND i.project_id = e.project_id
            AND i.subject_key = 'slo:' || e.slo_id || ':excluded:' || e.id)
    ORDER BY e.created_at, e.id
    """
)
_OWNERS: Final = text(
    "SELECT id FROM notification_channels WHERE org_id = :org_id AND kind = 'owners'"
)


async def exhausted_notice(
    connection: AsyncConnection,
    binding: ProjectBinding,
    target: Target,
    found: burns.Inputs,
    *,
    at: datetime,
) -> int:
    """FR19's warning, once, or its re-arming. Returns how many notifications were written."""
    totals = found.totals.get(target.id)
    if totals is None or totals.total <= 0:
        return 0
    keys = {"org_id": uuid.UUID(binding.org_id), "id": target.id}
    allowed = slo.allowed_failures(target.objective, totals.total)
    left = allowed - totals.failures
    if left > EXHAUSTED_REARM_SHARE * allowed:
        await connection.execute(_REARM_EXHAUSTED, keys)
        return 0
    if left > 0 or burns.building_history(target, totals):
        return 0
    claimed = await connection.scalar(
        _CLAIM_EXHAUSTED, keys | {"at": at, "repeat_before": at - EXHAUSTED_REPEAT_AFTER}
    )
    if claimed is None:
        return 0
    covering = [
        rule
        for rule in found.rules
        if (condition := alerting.covers(rule, target.id)) is not None
        and condition.admits(NOTICE_SEVERITY)
        and not rule.muted_at(at)
    ]
    if not covering:
        return 0
    return await incidents.fire_notice(
        connection,
        _to(found, covering),
        incidents.Subject(key=exhausted_subject(target.id), label=target.label),
        condition_kind=burns.SLO_BURN,
        severity=NOTICE_SEVERITY,
        at=at,
        detail={
            "notice": "exhausted",
            "slo_id": str(target.id),
            "kind": target.kind,
            "objective_percent": slo.objective_text(target.objective),
            "window_days": target.window_days,
            "allowed": allowed,
            "failures": totals.failures,
            "zone": str(found.zone),
        },
    )


async def exclusion_notices(
    connection: AsyncConnection,
    binding: ProjectBinding,
    targets: Sequence[Target],
    found: burns.Inputs,
    *,
    at: datetime,
) -> int:
    """FR29's notice for each exclusion not yet told. Returns notifications written."""
    by_id = {target.id: target for target in targets}
    rows = await connection.execute(
        _UNTOLD_EXCLUSIONS,
        {
            "org_id": found.org_id,
            "project_id": found.project_id,
            "ids": list(by_id),
            "since": at - EXCLUSION_NOTICE_LOOKBACK,
        },
    )
    written = 0
    owners: tuple[uuid.UUID, ...] | None = None
    for row in rows.fetchall():
        target = by_id[row.slo_id]
        covering = [rule for rule in found.rules if alerting.covers(rule, target.id)]
        if covering:
            to = _to(found, covering)
        else:
            if owners is None:
                owners = tuple(await connection.scalars(_OWNERS, {"org_id": found.org_id}))
            to = incidents.NoticeTo(
                org_id=found.org_id,
                project_id=found.project_id,
                rule_id=None,
                rule_name=slo.EXCLUSION_NOTICE_RULE_NAME,
                channel_ids=owners,
            )
        written += await incidents.fire_notice(
            connection,
            to,
            incidents.Subject(key=exclusion_subject(target.id, row.id), label=target.label),
            condition_kind=burns.SLO_BURN,
            severity=NOTICE_SEVERITY,
            at=at,
            detail={
                "notice": "excluded",
                "slo_id": str(target.id),
                "exclusion_id": str(row.id),
                "starts_at": row.starts_at.isoformat(),
                "ends_at": row.ends_at.isoformat(),
                "minutes": int((row.ends_at - row.starts_at).total_seconds() // 60),
                "zone": str(found.zone),
            },
        )
    return written


def _to(found: burns.Inputs, covering: Sequence[Rule]) -> incidents.NoticeTo:
    """The union of the covering rules' channels, filed under the first rule by name."""
    first = min(covering, key=lambda rule: (rule.name, str(rule.id)))
    channels = sorted({channel for rule in covering for channel in rule.channel_ids}, key=str)
    return incidents.NoticeTo(
        org_id=found.org_id,
        project_id=found.project_id,
        rule_id=first.id,
        rule_name=first.name,
        channel_ids=tuple(channels),
    )


# ------------------------------------------------------- when nexovue goes blind
#
# FR13b, D58: raised by the delivery sweep on `scheduler-deliveries`, a different container
# from the one judging, needing only Postgres. One incident per project, filed under no
# rule, with a fixed name, so deleting a rule changes nothing about it; kept single by
# `uq_incidents_blind_open` and its own open and resolve path, never `fire` and `clear`.

BLIND_SEVERITY: Final = "critical"
# FR13b: judging older than this, or every tier short of coverage this long (a guess).
BLIND_AFTER: Final = timedelta(minutes=15)
BLIND_PREFIX: Final = "slo-project:"


def blind_subject(project_id: uuid.UUID) -> str:
    return f"{BLIND_PREFIX}{project_id}:blind"


# Across organizations on purpose: one sweep finds every project whose enabled targets
# are not being judged. Each row carries the `org_id` everything it writes binds.
_BLIND_PROJECTS: Final = text(
    """
    SELECT t.org_id, t.project_id, p.name AS project_name, p.timezone,
           min(least(coalesce(s.evaluated_minute, t.created_at),
                     coalesce(s.unjudged_since, 'infinity'::timestamptz))) AS since
    FROM slo_targets AS t
    JOIN project_retention AS live ON live.org_id = t.org_id AND live.project_id = t.project_id
    JOIN projects AS p ON p.org_id = t.org_id AND p.id = t.project_id
    LEFT JOIN slo_status AS s ON s.org_id = t.org_id AND s.slo_id = t.id
    WHERE t.deleted_at IS NULL AND t.enabled
      AND (coalesce(s.evaluated_minute, t.created_at) < :stale_before
           OR s.unjudged_since < :stale_before)
    GROUP BY t.org_id, t.project_id, p.name, p.timezone
    """
)
# Across organizations on purpose: every open blind notice, to resolve those whose
# project is judged again. Each row carries its own `org_id`.
_OPEN_BLIND: Final = text(
    """
    SELECT id, org_id, project_id FROM incidents
    WHERE rule_id IS NULL AND resolved_at IS NULL AND subject_key LIKE 'slo-project:%'
    """
)
_OPEN_BLIND_NOTICE: Final = text(
    """
    INSERT INTO incidents (org_id, project_id, rule_id, rule_name, condition_kind,
        subject_key, subject_label, severity, opened_at, detail)
    VALUES (:org_id, :project_id, NULL, :rule_name, :condition_kind, :subject_key,
        :subject_label, :severity, :at, CAST(:detail AS jsonb))
    ON CONFLICT (project_id, subject_key)
        WHERE rule_id IS NULL AND resolved_at IS NULL AND subject_key LIKE 'slo-project:%'
        DO NOTHING
    RETURNING id
    """
)
# The union of the project's enabled, unmuted burn rules' channels.
_BURN_CHANNELS: Final = text(
    """
    SELECT DISTINCT c.channel_id
    FROM alert_rules AS r
    JOIN alert_rule_channels AS c ON c.org_id = r.org_id AND c.rule_id = r.id
    WHERE r.org_id = :org_id AND r.project_id = :project_id
      AND r.condition_kind = 'slo_burn' AND r.enabled
      AND (r.muted_until IS NULL OR r.muted_until <= :at)
    """
)
_RESOLVE_BLIND: Final = text(
    """
    UPDATE incidents SET resolved_at = :at
    WHERE org_id = :org_id AND id = :id AND resolved_at IS NULL
    RETURNING id
    """
)
# One `resolved` row for each channel that holds its `opened` row, whoever is told now.
_TELL_JUDGING_AGAIN: Final = text(
    """
    INSERT INTO notifications (org_id, project_id, incident_id, channel_id, kind, sequence,
                               severity, deliver_after, created_at)
    SELECT org_id, project_id, incident_id, channel_id, 'resolved', 0, severity, :at, :at
    FROM notifications
    WHERE org_id = :org_id AND incident_id = :id AND kind = 'opened'
    ON CONFLICT (incident_id, channel_id, kind, sequence) DO NOTHING
    """
)
_TELL_BLIND: Final = text(
    """
    INSERT INTO notifications (org_id, project_id, incident_id, channel_id, kind, sequence,
                               severity, deliver_after, created_at)
    SELECT :org_id, :project_id, :id, notification_channels.id, 'opened', 0, :severity,
           :at, :at
    FROM notification_channels
    WHERE notification_channels.org_id = :org_id
      AND notification_channels.id = ANY(CAST(:channel_ids AS uuid[]))
    ON CONFLICT (incident_id, channel_id, kind, sequence) DO NOTHING
    """
)


async def report_blind(engine: AsyncEngine, *, now: datetime) -> int:
    """Open a notice for each project newly not being judged, and resolve each whose
    judging returned. Returns how many projects changed."""
    changed = 0
    async with engine.begin() as connection:
        blind = {
            (row.org_id, row.project_id): row
            for row in await connection.execute(
                _BLIND_PROJECTS, {"stale_before": now - BLIND_AFTER}
            )
        }
        for row in blind.values():
            changed += await _open_blind(connection, row, at=now)
        for row in (await connection.execute(_OPEN_BLIND)).fetchall():
            if (row.org_id, row.project_id) not in blind:
                keys = {"org_id": row.org_id, "id": row.id, "at": now}
                if await connection.scalar(_RESOLVE_BLIND, keys) is not None:
                    await connection.execute(_TELL_JUDGING_AGAIN, keys)
                    changed += 1
    return changed


async def _open_blind(connection: AsyncConnection, row: Any, *, at: datetime) -> int:
    keys = {"org_id": row.org_id, "project_id": row.project_id}
    opened = await connection.scalar(
        _OPEN_BLIND_NOTICE,
        keys
        | {
            "rule_name": slo.BLIND_RULE_NAME,
            "condition_kind": burns.SLO_BURN,
            "subject_key": blind_subject(row.project_id),
            "subject_label": f"Reliability targets in {row.project_name}",
            "severity": BLIND_SEVERITY,
            "at": at,
            "detail": json.dumps(
                {
                    "notice": "blind",
                    "project_id": str(row.project_id),
                    "since": row.since.isoformat(),
                    "zone": row.timezone or DEFAULT_TIMEZONE,
                }
            ),
        },
    )
    if opened is None:
        return 0
    channels = list(await connection.scalars(_BURN_CHANNELS, keys | {"at": at}))
    if not channels:
        channels = list(await connection.scalars(_OWNERS, {"org_id": row.org_id}))
    await connection.execute(
        _TELL_BLIND,
        keys | {"id": opened, "severity": BLIND_SEVERITY, "at": at, "channel_ids": channels},
    )
    return 1
