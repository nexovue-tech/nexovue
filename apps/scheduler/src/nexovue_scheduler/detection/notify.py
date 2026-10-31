"""Who is told what about a finding, and about detection itself (design,
"Notifications"; spec FR21, FR25, FR28, FR29, BR9; D22, D24, D29).

Every function takes the caller's connection and never commits: a notification
commits with the judgement that decided it, or not at all (ADR-013). Delivery
shapes and sends them later (`alerts/outbox.py`).

**Rules only route.** A finding opens whatever the rules say. Each enabled
*anomaly finding* rule whose services and minimum severity admit it adds its
channels; a muted rule adds none (F21 BR5). One row per channel, whichever rules
name it.

**What is sent.** One message when a finding opens, one when it turns critical,
one each time a new signal or endpoint joins it (D29), and one when it closes —
to the channels told at opening, escalation or joining, whatever the rules have
become since, so a PagerDuty alert that was triggered is the one resolved (AC22).

**Mute and snooze** (BR9, D8, D25). A finding opened while its service is muted
is marked muted and nothing is ever sent for it. Muting or snoozing a service
whose finding is open stops what comes after, except the recovery a PagerDuty or
webhook channel needs to close what it was sent.

**The flap guard** (C7) is F21's, per project and service: a fifth opening in an
hour is marked flapping and sent once as *flapping*; nothing more is said about
the service's findings until it has stayed normal for 30 minutes.

**The owners are told outside any rule** when a finding is adopted (D22) and when
detection stops judging a project for 15 minutes, or has never judged one that
has had its chance, and again when it runs (D24, D34).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from nexovue_platform.alerts.channels import READ_BY_PEOPLE
from nexovue_platform.alerts.model import AnomalyFindingCondition, parse_condition
from nexovue_platform.telemetry.logging import get_logger
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.alerts.incidents import FLAP_OPENINGS, FLAP_QUIET, FLAP_WINDOW
from nexovue_scheduler.alerts.outbox import ANOMALY_FINDING
from nexovue_scheduler.alerts.rules import Rule, enabled_rules
from nexovue_scheduler.detection.projects import keep_expected

logger = get_logger(__name__)

# D24: a project not judged for this long has its owners told, once.
STOPPED_AFTER: Final = timedelta(minutes=15)
# A project's detection stopping is said at the level that reaches the owners at
# night: while it is stopped no critical finding can open (BR4).
STOPPED_SEVERITY: Final = "critical"
# Adoption is never of a critical finding (D22), so its mail is a warning.
ADOPTED_SEVERITY: Final = "warning"

Told = Literal["opened", "escalated", "joined", "flapping"]
_TOLD_KINDS: Final = ["opened", "escalated", "joined", "flapping"]

_FINDING: Final = text(
    """
    SELECT findings.id, findings.org_id, findings.project_id, findings.scope,
           findings.service_name, findings.muted, findings.flapping, findings.started_at,
           coalesce(settings.muted_until > :at, false) AS service_muted,
           coalesce(settings.snoozed_until > :at, false) AS service_snoozed
    FROM findings
    LEFT JOIN service_detection_settings AS settings
        ON settings.org_id = findings.org_id AND settings.project_id = findings.project_id
       AND settings.service_name = findings.service_name
    WHERE findings.org_id = :org_id AND findings.id = :finding_id
    """
)
# F21's guard over findings (C7), for one scope: when its last flapping spell ended
# (openings before it were counted once already), and its openings since then.
_FLAP_ENDED: Final = text(
    """
    SELECT max(closed_at) FROM findings
    WHERE org_id = :org_id AND project_id = :project_id AND id <> :finding_id
      AND scope = :scope AND service_name IS NOT DISTINCT FROM :service_name AND flapping
    """
)
_OPENINGS_SINCE: Final = text(
    """
    SELECT count(*) FROM findings
    WHERE org_id = :org_id AND project_id = :project_id AND id <> :finding_id
      AND scope = :scope AND service_name IS NOT DISTINCT FROM :service_name
      AND started_at >= :since
    """
)
_LAST_FINDING: Final = text(
    """
    SELECT flapping, closed_at FROM findings
    WHERE org_id = :org_id AND project_id = :project_id AND id <> :finding_id
      AND scope = :scope AND service_name IS NOT DISTINCT FROM :service_name
    ORDER BY started_at DESC, id DESC
    LIMIT 1
    """
)
_MARK_FLAPPING: Final = text(
    "UPDATE findings SET flapping = true WHERE org_id = :org_id AND id = :finding_id"
)
_TOLD_CHANNELS: Final = text(
    """
    SELECT DISTINCT notifications.channel_id, notification_channels.kind
    FROM notifications
    JOIN notification_channels
        ON notification_channels.id = notifications.channel_id
       AND notification_channels.org_id = notifications.org_id
    WHERE notifications.org_id = :org_id AND notifications.finding_id = :finding_id
      AND notifications.kind = ANY(CAST(:kinds AS text[]))
    """
)
_INSERT_FOR_FINDING: Final = text(
    """
    INSERT INTO notifications
        (org_id, project_id, finding_id, channel_id, kind, sequence, severity,
         deliver_after, created_at)
    SELECT :org_id, :project_id, :finding_id, notification_channels.id, :kind, :sequence,
           :severity, :at, :at
    FROM notification_channels
    WHERE notification_channels.org_id = :org_id
      AND notification_channels.id = ANY(CAST(:channel_ids AS uuid[]))
    ON CONFLICT (finding_id, channel_id, kind, sequence) DO NOTHING
    RETURNING id
    """
)
_OWNERS_CHANNEL: Final = text(
    "SELECT id FROM notification_channels WHERE org_id = :org_id AND kind = 'owners'"
)
# The project's own notices name no subject; each new stop or start is the next in
# its sequence, so the unique key keeps one per stop or start and no more.
_INSERT_FOR_PROJECT: Final = text(
    """
    INSERT INTO notifications
        (org_id, project_id, channel_id, kind, sequence, severity, deliver_after, created_at)
    SELECT :org_id, :project_id, :channel_id, :kind,
           coalesce(max(earlier.sequence) + 1, 0), :severity, :at, :at
    FROM (SELECT 1) AS one
    LEFT JOIN notifications AS earlier
        ON earlier.org_id = :org_id AND earlier.project_id = :project_id
       AND earlier.channel_id = :channel_id AND earlier.kind = :kind
       AND earlier.incident_id IS NULL AND earlier.finding_id IS NULL
    ON CONFLICT DO NOTHING
    RETURNING id
    """
)
# Across organizations on purpose: one sweep watches every project's freshness. Each
# row carries the `org_id` everything written for it is scoped by. `expected_since`
# is set only while detection works on a live project, so one that went quiet, or
# was deleted, is not "stopped". A stop counts from the later of the last run and
# that, so a project never judged, or just sending again, has its chance (D34).
_NEWLY_STOPPED: Final = text(
    """
    SELECT runs.org_id, runs.project_id
    FROM detection_project_runs AS runs
    WHERE runs.stopped_reported_at IS NULL
      AND runs.expected_since < :stopped_before
      AND (runs.last_completed_at IS NULL OR runs.last_completed_at < :stopped_before)
      AND (CAST(:org_id AS uuid) IS NULL OR runs.org_id = CAST(:org_id AS uuid))
    ORDER BY runs.org_id, runs.project_id
    FOR UPDATE OF runs SKIP LOCKED
    """
)
_MARK_STOPPED: Final = text(
    """
    UPDATE detection_project_runs SET stopped_reported_at = :at
    WHERE org_id = :org_id AND project_id = :project_id
    """
)


@dataclass(frozen=True, slots=True)
class _Finding:
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    scope: str
    service_name: str | None
    muted: bool
    flapping: bool
    started_at: datetime
    # The service is muted (BR9) or snoozed (D25) now: people hear nothing more.
    quiet_now: bool

    @property
    def keys(self) -> dict[str, Any]:
        return {"org_id": self.org_id, "project_id": self.project_id, "finding_id": self.id}


async def finding_opened(
    connection: AsyncConnection,
    finding_id: uuid.UUID,
    *,
    org_id: uuid.UUID,
    severity: str,
    services: Iterable[str] = (),
    at: datetime,
) -> int:
    """A finding opened: its matching rules' channels are told, unless the guard says
    it is flapping. `services` are a project finding's (FR15), which a rule listing
    services matches on. Returns how many notifications were written."""
    finding = await _read(connection, finding_id, org_id=org_id, at=at)
    if finding.muted:
        return 0
    flap = await _flap_guard(connection, finding, at=at)
    if flap == "still" or finding.quiet_now:
        return 0
    channels = await _rule_channels(connection, finding, severity, services=services, at=at)
    return await _write(
        connection,
        finding,
        channels,
        kind="flapping" if flap == "starts" else "opened",
        severity=severity,
        at=at,
    )


async def finding_escalated(
    connection: AsyncConnection, finding_id: uuid.UUID, *, org_id: uuid.UUID, at: datetime
) -> int:
    """It turned critical: the rules that admit a critical, and so the default rule
    (AC22), hear it, as do the channels already told."""
    return await _while_open(
        connection, finding_id, org_id=org_id, kind="escalated", severity="critical", at=at
    )


async def finding_joined(
    connection: AsyncConnection,
    finding_id: uuid.UUID,
    *,
    org_id: uuid.UUID,
    severity: str,
    keys: int,
    at: datetime,
) -> int:
    """A new signal or endpoint joined it (D29). `keys` is how many it now holds,
    which only grows while it is open, so each join is its own row per channel."""
    return await _while_open(
        connection,
        finding_id,
        org_id=org_id,
        kind="joined",
        severity=severity,
        sequence=keys,
        at=at,
    )


async def finding_closed(
    connection: AsyncConnection,
    finding_id: uuid.UUID,
    *,
    org_id: uuid.UUID,
    severity: str,
    at: datetime,
) -> int:
    """It closed, however it closed: one *resolved* to every channel that was told
    anything about it (AC22). A muted, snoozed or flapping finding tells only the
    machines that hold an alert for it, so they can close it (F21 D8)."""
    finding = await _read(connection, finding_id, org_id=org_id, at=at)
    told = await _told(connection, finding)
    if finding.muted or finding.flapping or finding.quiet_now:
        told = {channel: kind for channel, kind in told.items() if kind not in READ_BY_PEOPLE}
    return await _write(connection, finding, list(told), kind="resolved", severity=severity, at=at)


async def finding_adopted(
    connection: AsyncConnection, finding_id: uuid.UUID, *, org_id: uuid.UUID, at: datetime
) -> int:
    """D22: adoption emails the owners whatever the rules say. A muted finding is the
    exception: BR9 says nothing is sent for it, ever."""
    finding = await _read(connection, finding_id, org_id=org_id, at=at)
    if finding.muted:
        return 0
    owners = await _owners_channel(connection, org_id)
    return await _write(
        connection, finding, [owners], kind="adopted", severity=ADOPTED_SEVERITY, at=at
    )


async def report_stopped(
    connection: AsyncConnection, *, now: datetime, org_id: uuid.UUID | None = None
) -> int:
    """D24, D34: each project detection has not judged for 15 minutes of being
    expected to, while it still has traffic or an open finding, has its owners told
    once — every organization's, or one's. Whether it ever ran does not matter.
    Returns how many notifications were written."""
    await keep_expected(connection, now=now, org_id=org_id)
    rows = list(
        await connection.execute(
            _NEWLY_STOPPED, {"stopped_before": now - STOPPED_AFTER, "org_id": org_id}
        )
    )
    written = 0
    for row in rows:
        keys = {"org_id": row.org_id, "project_id": row.project_id}
        await connection.execute(_MARK_STOPPED, keys | {"at": now})
        written += await _project_notice(
            connection, row.org_id, row.project_id, kind="stopped", at=now
        )
    return written


async def running_again(
    connection: AsyncConnection, *, org_id: uuid.UUID, project_id: uuid.UUID, at: datetime
) -> int:
    """D24's second message: the run that clears `stopped_reported_at` says so."""
    return await _project_notice(connection, org_id, project_id, kind="running_again", at=at)


# ------------------------------------------------------------------ internals


async def _read(
    connection: AsyncConnection, finding_id: uuid.UUID, *, org_id: uuid.UUID, at: datetime
) -> _Finding:
    row = (
        await connection.execute(_FINDING, {"org_id": org_id, "finding_id": finding_id, "at": at})
    ).one()
    return _Finding(
        id=row.id,
        org_id=row.org_id,
        project_id=row.project_id,
        scope=row.scope,
        service_name=row.service_name,
        muted=row.muted,
        flapping=row.flapping,
        started_at=row.started_at,
        quiet_now=row.service_muted or row.service_snoozed,
    )


async def _while_open(
    connection: AsyncConnection,
    finding_id: uuid.UUID,
    *,
    org_id: uuid.UUID,
    kind: Told,
    severity: str,
    at: datetime,
    sequence: int = 0,
) -> int:
    finding = await _read(connection, finding_id, org_id=org_id, at=at)
    if finding.muted or finding.flapping or finding.quiet_now:
        return 0
    channels = set(await _rule_channels(connection, finding, severity, services=(), at=at))
    channels |= set(await _told(connection, finding))
    return await _write(
        connection,
        finding,
        sorted(channels),
        kind=kind,
        severity=severity,
        at=at,
        sequence=sequence,
    )


async def _flap_guard(
    connection: AsyncConnection, finding: _Finding, *, at: datetime
) -> Literal["no", "starts", "still"]:
    """F21's FR11 over one project and service (C7). "still": a flapping spell has not
    had its 30 quiet minutes; "starts": this is the fifth opening within the hour."""
    keys = finding.keys | {"scope": finding.scope, "service_name": finding.service_name}
    last = (await connection.execute(_LAST_FINDING, keys)).first()
    still = bool(
        last is not None
        and last.flapping
        and (last.closed_at is None or at - last.closed_at < FLAP_QUIET)
    )
    if still:
        await connection.execute(_MARK_FLAPPING, finding.keys)
        return "still"
    ended = (await connection.execute(_FLAP_ENDED, keys)).scalar_one_or_none()
    since = at - FLAP_WINDOW if ended is None else max(at - FLAP_WINDOW, ended)
    earlier = (await connection.execute(_OPENINGS_SINCE, keys | {"since": since})).scalar_one()
    if earlier < FLAP_OPENINGS:
        return "no"
    await connection.execute(_MARK_FLAPPING, finding.keys)
    return "starts"


async def _rule_channels(
    connection: AsyncConnection,
    finding: _Finding,
    severity: str,
    *,
    services: Iterable[str],
    at: datetime,
) -> list[uuid.UUID]:
    """The channels of every enabled, unmuted *anomaly finding* rule that admits it."""
    rules = await enabled_rules(
        connection,
        org_id=finding.org_id,
        project_id=finding.project_id,
        condition_kind=ANOMALY_FINDING,
    )
    named = [finding.service_name] if finding.service_name is not None else list(services)
    channels: set[uuid.UUID] = set()
    for rule in rules:
        condition = _condition(rule)
        if condition is None or rule.muted_at(at) or not condition.admits(severity):
            continue
        if condition.services is None or any(condition.covers(name) for name in named):
            channels.update(rule.channel_ids)
    return sorted(channels)


def _condition(rule: Rule) -> AnomalyFindingCondition | None:
    try:
        condition = parse_condition(rule.condition)
    except (ValidationError, ValueError):
        logger.error("an alert rule's condition does not parse", rule_id=str(rule.id))
        return None
    return condition if isinstance(condition, AnomalyFindingCondition) else None


async def _told(connection: AsyncConnection, finding: _Finding) -> dict[uuid.UUID, str]:
    rows = await connection.execute(
        _TOLD_CHANNELS,
        {"org_id": finding.org_id, "finding_id": finding.id, "kinds": _TOLD_KINDS},
    )
    return {row.channel_id: row.kind for row in rows}


async def _write(
    connection: AsyncConnection,
    finding: _Finding,
    channel_ids: list[uuid.UUID],
    *,
    kind: str,
    severity: str,
    at: datetime,
    sequence: int = 0,
) -> int:
    if not channel_ids:
        return 0
    written = await connection.execute(
        _INSERT_FOR_FINDING,
        finding.keys
        | {
            "channel_ids": channel_ids,
            "kind": kind,
            "sequence": sequence,
            "severity": severity,
            "at": at,
        },
    )
    return len(written.fetchall())


async def _owners_channel(connection: AsyncConnection, org_id: uuid.UUID) -> uuid.UUID:
    owners = (await connection.execute(_OWNERS_CHANNEL, {"org_id": org_id})).scalar_one_or_none()
    if owners is None:
        # Every organization is made with one, and it cannot be deleted
        # (`api/alerts/defaults.py`): without it the owners can never be told.
        raise RuntimeError(f"organization {org_id} has no owners channel")
    found: uuid.UUID = owners
    return found


async def _project_notice(
    connection: AsyncConnection,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    kind: Literal["stopped", "running_again"],
    at: datetime,
) -> int:
    owners = await _owners_channel(connection, org_id)
    written = await connection.execute(
        _INSERT_FOR_PROJECT,
        {
            "org_id": org_id,
            "project_id": project_id,
            "channel_id": owners,
            "kind": kind,
            "severity": STOPPED_SEVERITY,
            "at": at,
        },
    )
    return len(written.fetchall())
