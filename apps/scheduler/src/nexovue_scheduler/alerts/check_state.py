"""A check's confirmed state change, judged against the project's check rules.

Called by `probes/state_store._apply` inside the transaction that records the
change, so the state and every alert it causes commit together or not at all
(design, *Check state — inline*). A paused check records nothing and a run inside
a maintenance window changes no state (F18), so neither reaches here — which is
the whole of BR2 and BR3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from nexovue_platform.alerts.model import CheckStateCondition, Severity, check_subject
from nexovue_platform.telemetry.logging import get_logger
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.alerts import incidents
from nexovue_scheduler.alerts.rules import enabled_rules

logger = get_logger(__name__)

CONDITION_KIND: Final = "check_state"

_CHECK_NAME: Final = text("SELECT name FROM checks WHERE org_id = :org_id AND id = :check_id")


@dataclass(frozen=True, slots=True)
class StateChange:
    org_id: uuid.UUID
    project_id: uuid.UUID
    check_id: uuid.UUID
    from_state: str | None
    to_state: str
    at: datetime
    run_id: str
    location: str
    # BR9-safe: built by `alerts.reasons`, never the run's own error text.
    alert_reason: str | None


@dataclass(frozen=True, slots=True)
class Fire:
    severity: Severity


@dataclass(frozen=True, slots=True)
class Clear:
    pass


def judge(condition: CheckStateCondition, to_state: str) -> Fire | Clear:
    """What one rule makes of a check reaching `to_state`.

    Pure. A state the rule selected fires at that state's severity; anything else —
    `up`, or a state the rule did not ask about — clears. *Misconfigured* is never
    implied by *down* (FR7).
    """
    severity = condition.severity_of(to_state)
    return Fire(severity) if severity is not None else Clear()


async def on_check_state_change(connection: AsyncConnection, change: StateChange) -> int:
    """Open, escalate or resolve each covering rule's incident. Returns how many
    notifications were written, so the caller knows whether to wake delivery."""
    rules = await enabled_rules(
        connection,
        org_id=change.org_id,
        project_id=change.project_id,
        condition_kind=CONDITION_KIND,
    )
    if not rules:
        return 0

    subject = incidents.Subject(
        key=check_subject(change.check_id), label=await _check_name(connection, change)
    )
    detail = _detail(change)
    written = 0
    for rule in rules:
        try:
            condition = CheckStateCondition.model_validate(rule.condition)
        except ValidationError:
            # The API validates before saving, so this is a rule written by
            # something else. Skipping it is right; failing the state change that
            # every other rule depends on is not.
            logger.error("an alert rule's condition does not parse", rule_id=str(rule.id))
            continue
        if not condition.covers(change.check_id):
            continue
        match judge(condition, change.to_state):
            case Fire(severity=severity):
                written += await incidents.fire(
                    connection, rule, subject, severity=severity, at=change.at, detail=detail
                )
            case Clear():
                written += await incidents.clear(connection, rule, subject, at=change.at)
    return written


async def _check_name(connection: AsyncConnection, change: StateChange) -> str:
    name = (
        await connection.execute(
            _CHECK_NAME, {"org_id": change.org_id, "check_id": change.check_id}
        )
    ).scalar_one_or_none()
    return str(name) if name else "a deleted check"


def _detail(change: StateChange) -> dict[str, Any]:
    return {
        "check_id": str(change.check_id),
        "state": change.to_state,
        "from_state": change.from_state,
        "run_id": change.run_id,
        "location": change.location,
        "reason": change.alert_reason,
    }
