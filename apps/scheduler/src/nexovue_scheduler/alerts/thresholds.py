"""Service error rate and p95 (FR5, FR6): a reading over a window, judged every
minute, with waits before firing and before resolving.

**The window ends two minutes ago.** The rollup is by span time and spans arrive
late; judging the newest minute would fire on a half-counted minute and resolve
when it filled in.

**Each rule and service has a position** — ok, pending, firing, resolving or
insufficient — kept in `alert_evaluations` with the time it was reached, so a
scheduler restart resumes a wait rather than starting it again.

**Too little traffic judges nothing** (FR6): below the rule's minimum request
count the position does not move, so a quiet night neither opens nor resolves an
incident.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Final, Literal

from nexovue_platform import readings
from nexovue_platform.alerts.model import (
    ErrorRateCondition,
    LatencyCondition,
    parse_condition,
    service_subject,
)
from nexovue_platform.telemetry.logging import get_logger
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.alerts import incidents
from nexovue_scheduler.alerts.rules import Rule, rules_of_kinds
from nexovue_scheduler.alerts.telemetry import TelemetryStore

logger = get_logger(__name__)

KINDS: Final = ["service_error_rate", "service_latency_p95"]
SETTLING: Final = timedelta(minutes=2)

Phase = Literal["ok", "pending", "firing", "resolving", "insufficient"]

_POSITION: Final = text(
    """
    SELECT phase, phase_since FROM alert_evaluations
    WHERE org_id = :org_id AND rule_id = :rule_id AND subject_key = :subject_key
    FOR UPDATE
    """
)
_SAVE_POSITION: Final = text(
    """
    INSERT INTO alert_evaluations
        (org_id, project_id, rule_id, subject_key, phase, phase_since, last_value, evaluated_at)
    VALUES (:org_id, :project_id, :rule_id, :subject_key, :phase, :since, :value, :now)
    ON CONFLICT (rule_id, subject_key) DO UPDATE
    SET phase = excluded.phase, phase_since = excluded.phase_since,
        last_value = excluded.last_value, evaluated_at = excluded.evaluated_at
    """
)


@dataclass(frozen=True, slots=True)
class Step:
    phase: Phase
    since: datetime
    action: Literal["fire", "clear"] | None = None


def step(
    phase: Phase,
    since: datetime,
    breached: bool | None,
    *,
    now: datetime,
    fire_after: timedelta,
    resolve_after: timedelta,
) -> Step:
    """One minute's judgement. `breached` is None when there was too little traffic.

    Pure: every transition the waits imply is here and nowhere else."""
    if breached is None:
        # Nothing to judge by. An open incident stays open; anything else waits.
        if phase in ("firing", "resolving"):
            return Step(phase, since)
        return Step("insufficient", since if phase == "insufficient" else now)
    if breached:
        if phase == "firing":
            return Step("firing", since)
        if phase == "resolving":
            return Step("firing", now)  # breached again before it cleared: one incident
        started = since if phase == "pending" else now
        if now - started >= fire_after:
            return Step("firing", now, "fire")
        return Step("pending", started)
    # Not breached.
    if phase == "firing":
        phase, since = "resolving", now
    if phase == "resolving":
        if now - since >= resolve_after:
            return Step("ok", now, "clear")
        return Step("resolving", since)
    return Step("ok", since if phase == "ok" else now)


@dataclass(frozen=True, slots=True)
class Reading:
    service: str
    requests: int
    errors: int
    p95_ms: float


def judge(
    condition: ErrorRateCondition | LatencyCondition, reading: Reading | None
) -> tuple[bool | None, float | None]:
    """Whether a service's reading breaches the rule, and the value it was judged on."""
    if reading is None or reading.requests < condition.min_requests:
        return None, None
    if isinstance(condition, ErrorRateCondition):
        percent = 100 * readings.failure_ratio(reading.errors, reading.requests)
        return percent >= condition.threshold_percent, percent
    return reading.p95_ms >= condition.threshold_ms, reading.p95_ms


async def evaluate(
    engine: AsyncEngine,
    store: TelemetryStore,
    *,
    now: datetime,
    org_id: uuid.UUID | None = None,
) -> int:
    """Every threshold rule, once. Returns how many notifications were written."""
    async with engine.connect() as connection:
        rules = await rules_of_kinds(connection, KINDS, org_id=org_id)
    end = now.replace(second=0, microsecond=0) - SETTLING
    written = 0
    read: dict[tuple[Any, Any, int], list[dict[str, Any]] | None] = {}
    for rule in rules:
        try:
            condition = parse_condition(rule.condition)
        except (ValidationError, ValueError):
            logger.error("an alert rule's condition does not parse", rule_id=str(rule.id))
            continue
        if not isinstance(condition, ErrorRateCondition | LatencyCondition):
            continue
        # Rules that share a project and a window share one read: the cost of a sweep
        # grows with the projects and windows in use, not with the rules.
        key = (rule.org_id, rule.project_id, condition.window_minutes)
        if key not in read:
            try:
                read[key] = store.for_project(rule.org_id, rule.project_id).service_readings(
                    start=end - timedelta(minutes=condition.window_minutes), end=end
                )
            except Exception:
                # A minute nobody saw moves no position (design, *Failure handling*).
                logger.exception("the telemetry store could not be read; skipping this minute")
                read[key] = None
        rows = read[key]
        if rows is None:
            continue
        seen = {
            str(row["service_name"]): Reading(
                service=str(row["service_name"]),
                requests=int(row["request_count"]),
                errors=int(row["error_count"]),
                p95_ms=float(row["p95_ms"]),
            )
            for row in rows
        }
        async with engine.begin() as connection:
            watched = set(seen) | await _positions(connection, rule)
            for service in sorted(watched):
                if condition.covers(service):
                    written += await incidents.judged_alone(
                        connection,
                        rule,
                        partial(
                            _judge_one,
                            connection,
                            rule,
                            condition,
                            service,
                            seen.get(service),
                            now=now,
                        ),
                    )
    return written


async def _positions(connection: AsyncConnection, rule: Rule) -> set[str]:
    """Services this rule already holds a position on — so a service that stops
    reporting while firing is still looked at, and waits rather than vanishing."""
    rows = await connection.execute(
        text(
            "SELECT subject_key FROM alert_evaluations "
            "WHERE org_id = :org_id AND rule_id = :rule_id"
        ),
        {"org_id": rule.org_id, "rule_id": rule.id},
    )
    return {row.subject_key.removeprefix("service:") for row in rows}


async def _judge_one(
    connection: AsyncConnection,
    rule: Rule,
    condition: ErrorRateCondition | LatencyCondition,
    service: str,
    reading: Reading | None,
    *,
    now: datetime,
) -> int:
    subject = incidents.Subject(key=service_subject(service), label=service)
    keys = {"org_id": rule.org_id, "rule_id": rule.id, "subject_key": subject.key}
    stored = (await connection.execute(_POSITION, keys)).first()
    # Waits are whole minutes of readings, so positions are kept on the minute: the
    # second a sweep happens to run must not move a fire a minute later.
    minute = now.replace(second=0, microsecond=0)
    phase: Phase = stored.phase if stored else "ok"
    since: datetime = stored.phase_since if stored else minute
    breached, value = judge(condition, reading)
    moved = step(
        phase,
        since,
        breached,
        now=minute,
        fire_after=timedelta(minutes=condition.fire_after_minutes),
        resolve_after=timedelta(minutes=condition.resolve_after_minutes),
    )
    await connection.execute(
        _SAVE_POSITION,
        keys
        | {
            "project_id": rule.project_id,
            "phase": moved.phase,
            "since": moved.since,
            "value": value,
            "now": now,
        },
    )
    if moved.action == "fire":
        return await incidents.fire(
            connection,
            rule,
            subject,
            severity=condition.severity,
            at=now,
            detail=_detail(condition, service, value, reading),
        )
    if moved.action == "clear":
        return await incidents.clear(connection, rule, subject, at=now)
    return 0


def _detail(
    condition: ErrorRateCondition | LatencyCondition,
    service: str,
    value: float | None,
    reading: Reading | None,
) -> dict[str, Any]:
    shared = {
        "service": service,
        "value": value,
        "requests": reading.requests if reading else None,
        "window_minutes": condition.window_minutes,
    }
    if isinstance(condition, ErrorRateCondition):
        return shared | {
            "reading": "error rate",
            "threshold": condition.threshold_percent,
            "unit": "%",
        }
    return shared | {"reading": "p95 latency", "threshold": condition.threshold_ms, "unit": "ms"}
