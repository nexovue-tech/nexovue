"""A target's burn under each rule covering it, once a minute (design, *What each job
does*, step 5; spec FR17, FR18).

- **The position** is kept in `alert_evaluations` by rule and subject (`slo:<id>`), and
  moved by thresholds' own pure `step()`, with no wait to fire, because the windows are
  the wait, and 10 minutes to resolve (FR18).
- **F19's own save.** On a minute no tier can judge, `evaluated_at` keeps its old value,
  so on a burn's row it means the last minute a tier could judge, which Step 5's
  withholding reads (D56). Thresholds' save writes it every minute.
- **`fire` every breached minute,** whatever `step()` says, because `step()` asks for
  nothing once firing: that is how a slow burn escalates to one `escalated` row (AC19).
- **`refresh_detail` while open,** before any `clear`, so a repeat, the recovery and the
  incident page read this minute's figures (AC19, AC104).
- **A burn on a source that stopped has an end** (FR17): silent for the longest window
  with that window at least 90% measured, it is closed and its channels told, so lost
  minutes never close one.

An incident holds numbers, ids and nexovue's composed label, never a target's name,
description or an exclusion's reason (FR33, AC72).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform import slo
from nexovue_platform.alerts.model import (
    SloBurnCondition,
    check_subject,
    service_subject,
    slo_subject,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.alerts import incidents
from nexovue_scheduler.alerts.rules import Rule
from nexovue_scheduler.alerts.thresholds import Phase, step
from nexovue_scheduler.slos.burn import BurnJudgment

# FR18: a burn fires on the first breached minute and resolves after this long with no
# tier holding (a guess, in the spec's register).
FIRE_AFTER: Final = timedelta(0)
RESOLVE_AFTER: Final = timedelta(minutes=10)
# Why a burn closed without recovering, for its message (FR17).
CLOSED_SOURCE_SILENT: Final = "source_silent"

_POSITION: Final = text(
    """
    SELECT phase, phase_since FROM alert_evaluations
    WHERE org_id = :org_id AND rule_id = :rule_id AND subject_key = :subject_key
    FOR UPDATE
    """
)
# Never `evaluated_at` on a minute no tier could judge (D56): a position first saved on
# such a minute has never been judged, which `-infinity` says and no freshness test
# mistakes for recent.
_SAVE_POSITION: Final = text(
    """
    INSERT INTO alert_evaluations
        (org_id, project_id, rule_id, subject_key, phase, phase_since, last_value, evaluated_at)
    VALUES (:org_id, :project_id, :rule_id, :subject_key, :phase, :since, :value,
            coalesce(CAST(:judged_at AS timestamptz), '-infinity'))
    ON CONFLICT (rule_id, subject_key) DO UPDATE
    SET phase = excluded.phase, phase_since = excluded.phase_since,
        last_value = coalesce(excluded.last_value, alert_evaluations.last_value),
        evaluated_at = coalesce(CAST(:judged_at AS timestamptz), alert_evaluations.evaluated_at)
    WHERE alert_evaluations.org_id = :org_id
    """
)
_FORGET_POSITION: Final = text(
    """
    DELETE FROM alert_evaluations
    WHERE org_id = :org_id AND rule_id = :rule_id AND subject_key = :subject_key
    """
)


@dataclass(frozen=True, slots=True)
class Minute:
    """One target's burn at one settled minute, as every rule covering it sees it."""

    subject: incidents.Subject
    minute: datetime
    judgment: BurnJudgment
    detail: dict[str, Any]
    # FR17: silent for the longest window, with that window measured.
    source_stopped: bool


def burn_subject(
    target_id: uuid.UUID,
    *,
    label: str,
    kind: str,
    service_name: str | None,
    endpoint_name: str | None,
    check_id: uuid.UUID | None,
) -> incidents.Subject:
    """A burn's subject: keyed by its target, so two targets on one service are two
    incidents, and about what the target watches (design, *The link to findings*): its
    service, or its check when it names none (D19), and its type for FR21's pairs."""
    about_key: str | None = None
    if service_name is not None:
        about_key = service_subject(service_name)
    elif check_id is not None:
        about_key = check_subject(check_id)
    return incidents.Subject(
        key=slo_subject(target_id),
        label=label,
        about_key=about_key,
        about_signal=slo.signal_of(kind),
        about_endpoint=endpoint_name,
        about_check_id=check_id,
    )


def covers(rule: Rule, target_id: uuid.UUID) -> SloBurnCondition | None:
    """The rule's condition when it covers this target; None otherwise."""
    try:
        condition = SloBurnCondition.model_validate(rule.condition)
    except ValueError:
        return None
    return condition if condition.covers_target(target_id) else None


async def judge_under(
    connection: AsyncConnection, rule: Rule, condition: SloBurnCondition, minute: Minute
) -> int:
    """One rule's position on one target, and the incident it opens, escalates, keeps
    current, resolves or closes. Returns how many notifications were written."""
    subject = minute.subject
    keys = {"org_id": rule.org_id, "rule_id": rule.id, "subject_key": subject.key}
    stored = (await connection.execute(_POSITION, keys)).first()
    phase: Phase = stored.phase if stored else "ok"
    since: datetime = stored.phase_since if stored else minute.minute
    breached = _breached_for(condition, minute.judgment)
    moved = step(
        phase,
        since,
        breached,
        now=minute.minute,
        fire_after=FIRE_AFTER,
        resolve_after=RESOLVE_AFTER,
    )
    await connection.execute(
        _SAVE_POSITION,
        keys
        | {
            "project_id": rule.project_id,
            "phase": moved.phase,
            "since": moved.since,
            "value": minute.judgment.rate,
            "judged_at": None if breached is None else minute.minute,
        },
    )
    written = 0
    severity = minute.judgment.severity
    if breached and severity is not None:
        written += await incidents.fire(
            connection, rule, subject, severity=severity, at=minute.minute, detail=minute.detail
        )
    await incidents.refresh_detail(connection, rule, subject, detail=minute.detail)
    if moved.action == "clear":
        written += await incidents.clear(connection, rule, subject, at=minute.minute)
    elif minute.source_stopped and moved.phase in ("firing", "resolving"):
        written += await incidents.close_unjudged(
            connection,
            rule,
            subject,
            at=minute.minute,
            detail=minute.detail | {"closed_why": CLOSED_SOURCE_SILENT},
        )
        await connection.execute(_FORGET_POSITION, keys)
    return written


def _breached_for(condition: SloBurnCondition, judgment: BurnJudgment) -> bool | None:
    """A rule asking for criticals only treats a slow burn as not breached (design, *Burn*)."""
    if judgment.breached and judgment.severity and not condition.admits(judgment.severity):
        return False
    return judgment.breached


# --------------------------------------------------------------------- detail


def burn_detail(
    *,
    target_id: uuid.UUID,
    kind: str,
    service_name: str | None,
    endpoint_name: str | None,
    check_id: uuid.UUID | None,
    objective: object,
    window_days: int,
    judgment: BurnJudgment,
    burning_since: datetime | None,
    allowed: int,
    left: int,
    zone: str,
    minute: datetime,
    exclusions: list[dict[str, Any]],
) -> dict[str, Any]:
    """What an incident and its messages say (design, *Messages*): numbers, ids, names a
    rollup already holds, and nexovue's words. Never a target's own name."""
    chart = judgment.chart_tier
    rate = judgment.rate
    if rate is None and chart is not None:
        rate = next(each.long_rate for each in judgment.tiers if each.tier is chart)
    return {
        "slo_id": str(target_id),
        "kind": kind,
        "service": service_name,
        "endpoint": endpoint_name,
        "check_id": None if check_id is None else str(check_id),
        "objective_percent": slo.objective_text(objective),  # type: ignore[arg-type]
        "window_days": window_days,
        "holding": judgment.holding is not None,
        "tier": None if chart is None else chart.name,
        "rate": rate,
        "hours_left": judgment.hours_left,
        "since": None if burning_since is None else burning_since.isoformat(),
        "allowed": allowed,
        "left": max(left, 0),
        "zone": zone,
        "as_of": minute.isoformat(),
        # FR29, AC60: exclusions overlapping this burn, by their times and ids only.
        "exclusions": exclusions,
    }
