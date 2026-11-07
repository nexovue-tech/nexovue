"""The timeline events the project job writes (F22, plan T1.10; FR16, FR18, AC23).

Written beside the notify calls in `project_job`, never inside them, because
`notify.finding_opened` returns on a muted finding before the flap guard runs —
and a muted spell must still collapse to one `flapping_started` row (D35).

Two rules shape this module:

- **`spell_state` is read-only.** It reuses notify's `_OPENINGS_SINCE` and
  `_LAST_FINDING` statements and adds one read of its own — a recent
  `flapping_started` event on the scope — and never touches `_MARK_FLAPPING`,
  so F6's `flapping` column and messages do not change (AC23). It runs only
  when a finding opens, not on every minute judged.
- **A closing writes an event only if its opening did** (FR18), decided by one
  EXISTS on this finding's `finding_opened` row, so a spell never ends with an
  orphan close.

Every write goes through `RECORD_FINDING_EVENT_SQL` with
`finding_event_parameters()` — the one writer, which admits no free text (AC24).
Each statement binds `org_id`; nothing here crosses a tenant.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final, Literal

from nexovue_platform.cases import RECORD_FINDING_EVENT_SQL, finding_event_parameters
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.alerts.incidents import FLAP_OPENINGS, FLAP_QUIET, FLAP_WINDOW
from nexovue_scheduler.detection.notify import _LAST_FINDING, _OPENINGS_SINCE

# What this module may write; test_detection_events_live.py holds it inside the
# frozen vocabulary, so an insert cannot abort detection's transaction (AC24).
JOB_EVENT_KINDS: Final = (
    "finding_opened",
    "severity_raised",
    "signal_joined",
    "finding_closed",
    "flapping_started",
)

# One `flapping_started` per spell: a later opening finds this row and stays
# quiet. Bound by scope and service through the finding it was written on.
_SPELL_EVENT: Final = text(
    """
    SELECT count(*) FROM finding_events
    JOIN findings ON findings.org_id = finding_events.org_id
       AND findings.id = finding_events.finding_id
    WHERE finding_events.org_id = :org_id AND findings.project_id = :project_id
      AND findings.scope = :scope AND findings.service_name IS NOT DISTINCT FROM :service_name
      AND finding_events.kind = 'flapping_started' AND finding_events.at >= :since
    """
)

# Whether this finding's opening wrote an event: the closing's gate (FR18).
_HAS_OPENING_EVENT: Final = text(
    """
    SELECT count(*) FROM finding_events
    WHERE org_id = :org_id AND finding_id = :finding_id AND kind = 'finding_opened'
    """
)

SpellState = Literal["no", "starts", "still"]


async def spell_state(
    connection: AsyncConnection, keys: dict[str, Any], *, at: datetime
) -> SpellState:
    """F21's flap rule over one scope, read-only (C7, D35).

    "still": the spell goes on, so this opening writes nothing. "starts": this is
    the fifth opening within the hour and the spell's one event is owed. The
    column-based half matches notify's guard; the event-based half is what makes
    a *muted* spell visible, whose findings never carry the column.
    """
    last = (await connection.execute(_LAST_FINDING, keys)).first()
    if (
        last is not None
        and last.flapping
        and (last.closed_at is None or at - last.closed_at < FLAP_QUIET)
    ):
        return "still"
    since = at - FLAP_WINDOW
    earlier = (await connection.execute(_OPENINGS_SINCE, keys | {"since": since})).scalar_one()
    if earlier < FLAP_OPENINGS - 1:
        return "no"
    spell_keys = {name: keys[name] for name in ("org_id", "project_id", "scope", "service_name")}
    already = (await connection.execute(_SPELL_EVENT, spell_keys | {"since": since})).scalar_one()
    return "still" if already else "starts"


async def _write(connection: AsyncConnection, kind: str, **fields: Any) -> None:
    await connection.execute(
        text(RECORD_FINDING_EVENT_SQL),
        finding_event_parameters(kind, actor_kind="system", **fields),
    )


async def _opening_wrote_an_event(
    connection: AsyncConnection, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> bool:
    counted = await connection.execute(
        _HAS_OPENING_EVENT, {"org_id": org_id, "finding_id": finding_id}
    )
    return bool(counted.scalar_one())


async def finding_opened(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    finding_id: uuid.UUID,
    scope: str,
    service_name: str | None,
    severity: str,
    at: datetime,
) -> None:
    """One `finding_opened`, or the spell's one `flapping_started`, or nothing."""
    keys = {
        "org_id": org_id,
        "project_id": project_id,
        "finding_id": finding_id,
        "scope": scope,
        "service_name": service_name,
    }
    spell = await spell_state(connection, keys, at=at)
    if spell == "still":
        return
    if spell == "starts":
        await _write(connection, "flapping_started", org_id=org_id, finding_id=finding_id, at=at)
        return
    await _write(
        connection, "finding_opened", org_id=org_id, finding_id=finding_id, severity=severity, at=at
    )


async def severity_raised(
    connection: AsyncConnection, *, org_id: uuid.UUID, finding_id: uuid.UUID, at: datetime
) -> None:
    if not await _opening_wrote_an_event(connection, org_id=org_id, finding_id=finding_id):
        return
    await _write(
        connection,
        "severity_raised",
        org_id=org_id,
        finding_id=finding_id,
        severity="critical",
        at=at,
    )


async def signal_joined(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    signal_count: int,
    at: datetime,
) -> None:
    """F6's meaning (D29): a new signal or endpoint joined one open finding."""
    if not await _opening_wrote_an_event(connection, org_id=org_id, finding_id=finding_id):
        return
    await _write(
        connection,
        "signal_joined",
        org_id=org_id,
        finding_id=finding_id,
        signal_count=signal_count,
        at=at,
    )


async def finding_closed(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    reason: str,
    at: datetime,
) -> None:
    """How it closed, adoption included — only if its opening wrote an event."""
    if not await _opening_wrote_an_event(connection, org_id=org_id, finding_id=finding_id):
        return
    await _write(
        connection,
        "finding_closed",
        org_id=org_id,
        finding_id=finding_id,
        closed_reason=reason,
        at=at,
    )
