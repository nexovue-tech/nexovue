"""What holds every project's minutes, as the coordinator reads and records it
(design revision 2.1, item 1; `settle.py` has the rules).

Every statement here is across every organization on purpose. A refusal minute
and a guard describe nexovue's intake, not a tenant, and the guard counts
organizations. Nothing read here is shown to a tenant: a hold reaches a project
only as *data delayed*, and an event only an operator.

`detection_events` is the operator's record. It gets one row when the guard
engages, whose `through` grows while the intake stays fallen, and one row for each
hold released at its 30-minute limit. Its `from` is the hold's first minute, so a
sweep that sees the same hold again writes nothing new.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler import metrics
from nexovue_scheduler.detection import settle
from nexovue_scheduler.detection.project_job import IntakeTrouble
from nexovue_scheduler.detection.settle import Hold, Intake, Settled, Span

logger = get_logger(__name__)

GUARD_ENGAGED: Final = settle.GUARD_EVENT
HOLD_EXPIRED: Final = settle.EXPIRED_EVENT
EVENT_KINDS: Final = (GUARD_ENGAGED, HOLD_EXPIRED)
# Exported at zero from the start, so a monitor sees the first event as an increase.
for _kind in EVENT_KINDS:
    metrics.DETECTION_INTAKE_EVENTS.labels(_kind)

_REFUSED: Final = text("SELECT DISTINCT minute FROM intake_refusals WHERE minute >= :since")
# One vote per organization, however many of its projects lost traffic.
_VOTES: Final = text(
    """
    SELECT org_id, min(traffic_lost_since) AS onset FROM detection_project_runs
    WHERE traffic_lost_since >= :since
    GROUP BY org_id
    """
)
_GUARDS: Final = text(
    """
    SELECT detail ->> 'from' AS first, detail ->> 'through' AS last FROM detection_events
    WHERE kind = :kind AND CAST(detail ->> 'through' AS timestamptz) >= :since
    """
)
# Two sweeps a minute apart must not both write one hold's row.
_LOCK: Final = text("SELECT pg_advisory_xact_lock(hashtextextended('detection:holds', 0))")
_FIRST_SEEN: Final = text(
    """
    INSERT INTO detection_events (kind, detail)
    SELECT CAST(:kind AS text), CAST(:detail AS jsonb)
    WHERE NOT EXISTS (
        SELECT 1 FROM detection_events
        WHERE kind = CAST(:kind AS text) AND detail ->> 'from' = CAST(:first AS text)
    )
    """
)
_GROWN: Final = text(
    """
    UPDATE detection_events
    SET detail = jsonb_set(detail, '{through}', to_jsonb(CAST(:last AS text)))
    WHERE kind = :kind AND detail ->> 'from' = :first
      AND CAST(detail ->> 'through' AS timestamptz) < CAST(CAST(:last AS text) AS timestamptz)
    """
)


@dataclass(frozen=True, slots=True)
class Decision:
    """The minute the coordinator hands out, and what the project jobs must know
    about nexovue's intake while judging up to it."""

    minute: datetime | None
    trouble: IntakeTrouble
    holding: tuple[Hold, ...] = ()


async def decide(engine: AsyncEngine, found: Settled, intake: Intake | None) -> Decision:
    """The settled minute once holds are applied. `intake` is None when Kafka could
    not say what the receiver accepted: then no guard can engage."""
    now = found.now
    since = now - settle.HOLD_LOOKBACK
    fell = settle.fallen(intake) if intake is not None else []
    async with engine.begin() as connection:
        await connection.execute(_LOCK)
        refused = [row.minute for row in await connection.execute(_REFUSED, {"since": since})]
        votes = {
            str(row.org_id): row.onset for row in await connection.execute(_VOTES, {"since": since})
        }
        live = settle.guards(votes, fell)
        for guard in live:
            voters = sum(guard.span.first <= onset <= guard.span.last for onset in votes.values())
            await _record_guard(connection, guard, voters=voters)
        guards = _merged([*await _recorded_guards(connection, since), *live])
        held = settle.apply_holds(found, [*settle.refusal_holds(refused), *guards], now=now)
        for hold in held.expired:
            await _record_expired(connection, hold)
    in_force = tuple(guard.span for guard in guards if now - guard.span.first <= settle.HOLD_LIMIT)
    return Decision(
        minute=held.minute,
        trouble=IntakeTrouble(fell=tuple(settle.spans(fell)), guarded=in_force),
        holding=held.holding,
    )


async def _recorded_guards(connection: AsyncConnection, since: datetime) -> list[Hold]:
    rows = await connection.execute(_GUARDS, {"kind": GUARD_ENGAGED, "since": since})
    return [
        Hold(
            Span(datetime.fromisoformat(row.first), datetime.fromisoformat(row.last)),
            settle.GUARDED,
        )
        for row in rows
    ]


def _merged(guards: Sequence[Hold]) -> list[Hold]:
    """One hold per first minute: the recorded row and this sweep's view of it."""
    by_first: dict[datetime, Hold] = {}
    for guard in guards:
        known = by_first.get(guard.span.first)
        if known is None or guard.span.last > known.span.last:
            by_first[guard.span.first] = guard
    return sorted(by_first.values(), key=lambda guard: guard.span.first)


async def _record_guard(connection: AsyncConnection, guard: Hold, *, voters: int) -> None:
    first, last = guard.span.first.isoformat(), guard.span.last.isoformat()
    detail = {"from": first, "through": last, "organizations": voters}
    if await _first_seen(connection, GUARD_ENGAGED, first, detail):
        logger.error(
            "detection is holding every project: organizations lost traffic as nexovue's "
            "intake fell",
            held_from=first,
            organizations=voters,
        )
        return
    await connection.execute(_GROWN, {"kind": GUARD_ENGAGED, "first": first, "last": last})


async def _record_expired(connection: AsyncConnection, hold: Hold) -> None:
    first = hold.span.first.isoformat()
    detail = {"from": first, "through": hold.span.last.isoformat(), "reason": hold.reason}
    if await _first_seen(connection, HOLD_EXPIRED, first, detail):
        logger.error(
            "a hold on every project's minutes reached its limit; judging them anyway",
            held_from=first,
            reason=hold.reason,
        )


async def _first_seen(
    connection: AsyncConnection, kind: str, first: str, detail: Mapping[str, object]
) -> bool:
    written = await connection.execute(
        _FIRST_SEEN, {"kind": kind, "first": first, "detail": json.dumps(detail)}
    )
    if written.rowcount:
        metrics.DETECTION_INTAKE_EVENTS.labels(kind).inc()
    return bool(written.rowcount)
