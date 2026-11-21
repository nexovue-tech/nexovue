"""How the scheduler ends a case (F22, AC103): one ending shared by `cases_sweep`
(`no_activity`, `recovered_untouched`) and the tracker sync (`tracker_closed`).

It runs the shared statements the API's `findings/cases._resolve_root` runs too, so a
person's, the sync's and the sweep's endings write the same rows apart from the actor:
resolve, detach every open joined finding with its event (FR6, AC57), end a hold
(D73), the resolution's event, and an audit row naming nobody (FD9). Kept apart from
both callers so `scheduler-connections` loads no sweep and `light` no tracker code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from nexovue_platform.cases import (
    CLEAR_HOLD_SQL,
    DETACH_OPEN_JOINED_SQL,
    RECORD_CASE_AUDIT_SQL,
    RECORD_FINDING_EVENT_SQL,
    RESOLVE_CASE_SQL,
    case_audit_parameters,
    clear_hold_parameters,
    detach_open_joined_parameters,
    finding_event_parameters,
    resolve_case_parameters,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# The audit actions the scheduler's case writers use, as the API's `audit.CASE_*`.
CASE_RESOLVED_ACTION: Final = "case.resolved"
CASE_REOPENED_ACTION: Final = "case.reopened"
FINDING_TARGET: Final = "finding:{}"


async def record_event(
    connection: AsyncConnection,
    kind: str,
    *,
    org_id: Any,
    finding_id: Any,
    at: datetime,
    actor_kind: str = "system",
    **fields: Any,
) -> None:
    """One timeline event naming no person, through the shared writer (AC24)."""
    await connection.execute(
        text(RECORD_FINDING_EVENT_SQL),
        finding_event_parameters(
            kind, org_id=org_id, finding_id=finding_id, actor_kind=actor_kind, at=at, **fields
        ),
    )


async def record_audit(
    connection: AsyncConnection, *, org_id: Any, root_id: Any, action: str, detail: dict[str, Any]
) -> None:
    await connection.execute(
        text(RECORD_CASE_AUDIT_SQL),
        case_audit_parameters(
            org_id=org_id,
            actor_user_id=None,
            action=action,
            target=FINDING_TARGET.format(root_id),
            detail=detail,
        ),
    )


async def end_case(
    connection: AsyncConnection,
    *,
    org_id: Any,
    root_id: Any,
    resolution: str,
    at: datetime,
    actor_kind: str = "system",
) -> bool:
    """End an active case in the caller's transaction; False if it was not active."""
    resolved = await connection.execute(
        text(RESOLVE_CASE_SQL),
        resolve_case_parameters(
            org_id=org_id, finding_id=root_id, resolution=resolution, at=at, resolved_by=None
        ),
    )
    if resolved.first() is None:
        return False
    detached = await connection.execute(
        text(DETACH_OPEN_JOINED_SQL),
        detach_open_joined_parameters(org_id=org_id, root_finding_id=root_id),
    )
    for left_id in detached.scalars():
        await record_event(
            connection,
            "finding_left_case",
            org_id=org_id,
            finding_id=root_id,
            at=at,
            actor_kind=actor_kind,
            left_finding_id=left_id,
        )
    await connection.execute(
        text(CLEAR_HOLD_SQL), clear_hold_parameters(org_id=org_id, finding_id=root_id)
    )
    await record_event(
        connection,
        "case_resolved",
        org_id=org_id,
        finding_id=root_id,
        at=at,
        actor_kind=actor_kind,
        resolution=resolution,
    )
    await record_audit(
        connection,
        org_id=org_id,
        root_id=root_id,
        action=CASE_RESOLVED_ACTION,
        detail={"resolution": resolution},
    )
    return True
