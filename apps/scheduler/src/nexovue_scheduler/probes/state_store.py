"""Reading and writing check state in Postgres, from the worker.

The one place a probe worker touches the control plane, and never on the path of
a measurement: a run publishes first, then dispatches a separate task to record
its state, so a busy database delays a state change and never a reading (Q1 of
the F18 plan, recorded in ADR-010's amendment).

Named columns and plain SQL, as `checks.py` reads: the API owns these tables and
deploys separately, so importing its models would couple the two.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.alerts.check_state import StateChange, on_check_state_change
from nexovue_scheduler.probes.state import CurrentState, decide

_ATTEMPTS: Final = 2

_CURRENT: Final = text(
    """
    SELECT state, last_run_at FROM check_states
    WHERE org_id = :org_id AND check_id = :check_id
    FOR UPDATE
    """
)
_INSERT_STATE: Final = text(
    """
    INSERT INTO check_states
        (org_id, project_id, check_id, state, since, run_id, location, last_run_at)
    VALUES (:org_id, :project_id, :check_id, :state, :at, :run_id, :location, :at)
    """
)
_UPDATE_STATE: Final = text(
    """
    UPDATE check_states
    SET state = :state, since = :at, run_id = :run_id, location = :location, last_run_at = :at
    WHERE org_id = :org_id AND check_id = :check_id
    """
)
_TOUCH_STATE: Final = text(
    """
    UPDATE check_states SET last_run_at = :at, run_id = :run_id, location = :location
    WHERE org_id = :org_id AND check_id = :check_id
    """
)
_INSERT_CHANGE: Final = text(
    """
    INSERT INTO check_state_changes
        (org_id, project_id, check_id, from_state, to_state, changed_at, run_id, location, reason)
    VALUES
        (:org_id, :project_id, :check_id, :from_state, :state, :at, :run_id, :location, :reason)
    """
)


@dataclass(frozen=True, slots=True)
class RecordedOutcome:
    check_id: str
    org_id: str
    project_id: str
    outcome: str
    run_id: str
    location: str
    observed_at: datetime
    reason: str | None
    # The reason as an alert may carry it (BR9); see `alerts.reasons`.
    alert_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Recorded:
    """What recording one run did: the state it changed to, if any, and how many
    alert notifications that change wrote for delivery (F21)."""

    changed_to: str | None = None
    notifications: int = 0


async def record(engine: AsyncEngine, outcome: RecordedOutcome) -> Recorded:
    """Apply one confirmed run.

    A check deleted between its run and this write has nothing to record against;
    the foreign key refuses the row and that is reported as no change. Two first
    runs recorded at once race to insert the one row; the loser tries once more
    and finds the winner's.
    """
    parameters: dict[str, Any] = {
        "org_id": uuid.UUID(outcome.org_id),
        "project_id": uuid.UUID(outcome.project_id),
        "check_id": uuid.UUID(outcome.check_id),
        "state": outcome.outcome,
        "at": outcome.observed_at,
        "run_id": outcome.run_id,
        "location": outcome.location,
        "reason": outcome.reason,
    }
    for _ in range(_ATTEMPTS):
        try:
            async with engine.begin() as connection:
                return await _apply(connection, outcome, parameters)
        except IntegrityError:
            continue
    return Recorded()


async def _apply(
    connection: AsyncConnection, outcome: RecordedOutcome, parameters: dict[str, Any]
) -> Recorded:
    row = (await connection.execute(_CURRENT, parameters)).first()
    current = CurrentState(state=row.state, last_run_at=row.last_run_at) if row else None
    decision = decide(current, outcome.outcome, outcome.observed_at)
    if not decision.record:
        return Recorded()
    if decision.change_to is None:
        await connection.execute(_TOUCH_STATE, parameters)
        return Recorded()

    if current is None:
        await connection.execute(_INSERT_STATE, parameters)
    else:
        await connection.execute(_UPDATE_STATE, parameters)
    await connection.execute(
        _INSERT_CHANGE, parameters | {"from_state": current.state if current else None}
    )
    # In this transaction, on purpose: the state change and every alert it causes
    # commit together or not at all (F21 design, *Check state — inline*). A crash
    # after the commit has already recorded the notification; one before it has
    # recorded neither, and `record_outcome`'s retry records both.
    notifications = await on_check_state_change(
        connection,
        StateChange(
            org_id=parameters["org_id"],
            project_id=parameters["project_id"],
            check_id=parameters["check_id"],
            from_state=current.state if current else None,
            to_state=decision.change_to,
            at=outcome.observed_at,
            run_id=outcome.run_id,
            location=outcome.location,
            alert_reason=outcome.alert_reason,
        ),
    )
    return Recorded(changed_to=decision.change_to, notifications=notifications)
