"""`cases_sweep`, every 5 minutes on `light` (F22 plan T2.6; design, *Keeping and
ending*).

A case is ended by a person, a tracker, or silence. This sweep is silence, and
the repair of races the design accepts rather than prevents:

1. **clears an assignee who left or became a viewer** while their case was locked,
   which the routes' `SKIP LOCKED` left behind (AC10);
2. **heals joins** (AC17): an open finding that missed joining an active case, and
   one left on a resolved root, with the job's own 7-day condition;
3. **ends idle cases** at 90 days with no person's act, as `no_activity` (BR13);
4. **ends untouched cases** (FR49): taken and nothing more, with the root and every
   joined finding closed for 72 hours, as `recovered_untouched`;
5. **starts the idle warning** at 76 days, owing one `idle_warning` email (FR26,
   D90), and asking the synced issue for its one idle comment, which the sync job
   writes (AC97): the sweep itself makes no tracker call and holds no tracker key;
6. **sends what is owed** (`case_emails.py`): case emails, and each connection's
   failure and expiry notices (FR33), from `scheduler-light`, which holds the mail
   settings.

It runs with no connection and no tracker, which is why it is not in
`connections`. It holds no person, so its endings write `audit_log` with no actor,
as the job's adoption does.

**It never waits on a lock.** Each step's first read crosses organizations by
nature, unlocked; then each case is its own transaction, its rows locked
`FOR NO KEY UPDATE SKIP LOCKED` root first, as a person's acts lock. A row the job
or a person holds is left for the next sweep, and a process that never waits can
never be half of a deadlock (NFR2). Every follow-on statement binds the row's own
`org_id`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.cases import (
    CASE_IDLE_LIMIT,
    CASE_IDLE_WARNING_AHEAD,
    CASE_JOIN_WINDOW,
    CASE_UNTOUCHED_AFTER,
    RECORD_EMAIL_OWED_SQL,
    email_owed_parameters,
)
from nexovue_platform.email import build_email_sender
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler import case_emails
from nexovue_scheduler.case_endings import end_case, record_event
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

CASES_SWEEP_TASK: Final = "nexovue_scheduler.cases.sweep"

# How many cases one step takes in one sweep; the next sweep takes the rest.
STEP_BATCH: Final = 500


@dataclass(slots=True)
class SweepOutcome:
    """How many cases each step changed in one sweep."""

    unassigned: int = 0
    joined: int = 0
    left: int = 0
    ended_idle: int = 0
    ended_untouched: int = 0
    warned: int = 0
    emailed: int = 0
    connections_told: int = 0


# ------------------------------------------------- the first reads, across tenants

# An active case whose assignee left or became a viewer.
_CLEARABLE_ASSIGNEES: Final = text(
    """
    SELECT finding.org_id, finding.id FROM findings AS finding
    JOIN users AS person ON person.org_id = finding.org_id AND person.id = finding.assignee_id
    WHERE finding.case_state = 'investigating'
      AND (person.removed_at IS NOT NULL OR person.role = 'viewer')
    LIMIT :batch
    """
)
# An open finding on no case, beside its scope's active case acted on lately.
_MISSED_JOINS: Final = text(
    """
    SELECT stray.org_id, stray.id, active.id AS root_id FROM findings AS stray
    JOIN findings AS active
      ON active.org_id = stray.org_id AND active.project_id = stray.project_id
     AND active.scope = stray.scope
     AND active.service_name IS NOT DISTINCT FROM stray.service_name
     AND active.case_state = 'investigating' AND active.case_activity_at > :join_since
    WHERE stray.state = 'open' AND stray.root_finding_id IS NULL AND stray.case_state IS NULL
    LIMIT :batch
    """
)
# An open finding still joined to a root whose case resolved.
_LEFT_ON_RESOLVED: Final = text(
    """
    SELECT joined.org_id, joined.id, root.id AS root_id FROM findings AS joined
    JOIN findings AS root ON root.org_id = joined.org_id AND root.id = joined.root_finding_id
    WHERE joined.state = 'open' AND root.case_state = 'resolved'
    LIMIT :batch
    """
)
_IDLE_CASES: Final = text(
    """
    SELECT org_id, id FROM findings
    WHERE case_state = 'investigating' AND case_activity_at < :idle_before
    LIMIT :batch
    """
)
# The root and every joined finding closed before `closed_before`, nothing beyond taking,
# and no link: a case with a tracker issue never ends by silence (design, step 3; AC90).
_UNTOUCHED_CASES: Final = text(
    """
    SELECT root.org_id, root.id FROM findings AS root
    WHERE root.case_state = 'investigating' AND root.case_worked_at IS NULL
      AND root.state = 'closed' AND root.closed_at < :closed_before
      AND NOT EXISTS (
          SELECT 1 FROM findings AS joined
          WHERE joined.org_id = root.org_id AND joined.root_finding_id = root.id
            AND (joined.state = 'open' OR joined.closed_at >= :closed_before))
      AND NOT EXISTS (
          SELECT 1 FROM finding_links AS link
          WHERE link.org_id = root.org_id AND link.finding_id = root.id)
    LIMIT :batch
    """
)
# Past the warning's start and not yet warned in this idle spell: a person's act
# after the last warning moves `case_activity_at` past it, starting a new spell.
_DUE_WARNINGS: Final = text(
    """
    SELECT org_id, id FROM findings
    WHERE case_state = 'investigating' AND case_activity_at < :warn_before
      AND (case_warned_at IS NULL OR case_warned_at < case_activity_at)
    LIMIT :batch
    """
)

# ------------------------------------------- per case, bound to the row's tenant

# Each lock re-checks its step's condition, so a case a person changed between the
# first read and this transaction is left alone.
_LOCK_CLEARABLE: Final = text(
    """
    SELECT finding.assignee_id FROM findings AS finding
    JOIN users AS person ON person.org_id = finding.org_id AND person.id = finding.assignee_id
    WHERE finding.org_id = :org_id AND finding.id = :id AND finding.case_state = 'investigating'
      AND (person.removed_at IS NOT NULL OR person.role = 'viewer')
    FOR NO KEY UPDATE OF finding SKIP LOCKED
    """
)
_CLEAR_ASSIGNEE: Final = text(
    """
    UPDATE findings SET assignee_id = NULL, assignee_acted_at = NULL, updated_at = now()
    WHERE org_id = :org_id AND id = :id
    """
)
_LOCK_ACTIVE_ROOT: Final = text(
    """
    SELECT id FROM findings
    WHERE org_id = :org_id AND id = :id AND case_state = 'investigating'
      AND case_activity_at > :join_since
    FOR NO KEY UPDATE SKIP LOCKED
    """
)
_LOCK_STRAY: Final = text(
    """
    SELECT id FROM findings
    WHERE org_id = :org_id AND id = :id AND state = 'open'
      AND root_finding_id IS NULL AND case_state IS NULL
    FOR NO KEY UPDATE SKIP LOCKED
    """
)
_LOCK_RESOLVED_ROOT: Final = text(
    """
    SELECT id FROM findings
    WHERE org_id = :org_id AND id = :id AND case_state = 'resolved'
    FOR NO KEY UPDATE SKIP LOCKED
    """
)
_LOCK_STRANDED: Final = text(
    """
    SELECT id FROM findings
    WHERE org_id = :org_id AND id = :id AND state = 'open' AND root_finding_id = :root_id
    FOR NO KEY UPDATE SKIP LOCKED
    """
)
_SET_ROOT: Final = text(
    """
    UPDATE findings SET root_finding_id = :root_id, updated_at = now()
    WHERE org_id = :org_id AND id = :id
    """
)
_LOCK_IDLE: Final = text(
    """
    SELECT id FROM findings
    WHERE org_id = :org_id AND id = :id AND case_state = 'investigating'
      AND case_activity_at < :idle_before
    FOR NO KEY UPDATE SKIP LOCKED
    """
)
_LOCK_UNTOUCHED: Final = text(
    """
    SELECT root.id FROM findings AS root
    WHERE root.org_id = :org_id AND root.id = :id
      AND root.case_state = 'investigating' AND root.case_worked_at IS NULL
      AND root.state = 'closed' AND root.closed_at < :closed_before
      AND NOT EXISTS (
          SELECT 1 FROM findings AS joined
          WHERE joined.org_id = :org_id AND joined.root_finding_id = root.id
            AND (joined.state = 'open' OR joined.closed_at >= :closed_before))
      AND NOT EXISTS (
          SELECT 1 FROM finding_links AS link
          WHERE link.org_id = :org_id AND link.finding_id = root.id)
    FOR NO KEY UPDATE OF root SKIP LOCKED
    """
)
_LOCK_DUE_WARNING: Final = text(
    """
    SELECT id FROM findings
    WHERE org_id = :org_id AND id = :id AND case_state = 'investigating'
      AND case_activity_at < :warn_before
      AND (case_warned_at IS NULL OR case_warned_at < case_activity_at)
    FOR NO KEY UPDATE SKIP LOCKED
    """
)
_MARK_WARNED: Final = text(
    """
    UPDATE findings SET case_warned_at = :at, updated_at = now()
    WHERE org_id = :org_id AND id = :id
    """
)
# FR26, BR10: the synced issue's one idle-warning comment in its case's life, written by
# the sync job; marked here so a later idle spell never asks again (AC96, AC97).
_ASK_IDLE_COMMENT: Final = text(
    """
    UPDATE finding_links
    SET comment_wanted = 'idle_warning', idle_warning_at = :at, next_sync_at = :at
    WHERE org_id = :org_id AND finding_id = :id AND role = 'synced'
      AND sync_status NOT IN ('stopped', 'refused', 'creating', 'not_followed')
      AND idle_warning_at IS NULL AND comment_wanted IS NULL AND wanted_state IS NULL
    """
)


# ------------------------------------------------------------------ the writes


async def _event(
    connection: AsyncConnection,
    kind: str,
    *,
    org_id: Any,
    finding_id: Any,
    at: datetime,
    **fields: Any,
) -> None:
    """One timeline event, nexovue's own, through the shared writer (AC24)."""
    await record_event(connection, kind, org_id=org_id, finding_id=finding_id, at=at, **fields)


async def _locked(connection: AsyncConnection, statement: TextClause, **values: Any) -> Any:
    """The locked row, or None when it changed or another transaction holds it."""
    return (await connection.execute(statement, values)).first()


# ------------------------------------------------------------------- the steps


async def _candidates(engine: AsyncEngine, statement: TextClause, **values: Any) -> list[Any]:
    async with engine.connect() as connection:
        return list(await connection.execute(statement, {"batch": STEP_BATCH, **values}))


async def _clear_assignees(engine: AsyncEngine, *, now: datetime) -> int:
    cleared = 0
    for candidate in await _candidates(engine, _CLEARABLE_ASSIGNEES):
        async with engine.begin() as connection:
            row = await _locked(
                connection, _LOCK_CLEARABLE, org_id=candidate.org_id, id=candidate.id
            )
            if row is None:
                continue
            await connection.execute(
                _CLEAR_ASSIGNEE, {"org_id": candidate.org_id, "id": candidate.id}
            )
            await _event(
                connection,
                "unassigned",
                org_id=candidate.org_id,
                finding_id=candidate.id,
                at=now,
                assignee_id=row.assignee_id,
            )
            cleared += 1
    return cleared


async def _heal_missed_joins(engine: AsyncEngine, *, now: datetime) -> int:
    """AC17's first half, with the job's own condition (`project_job._OPEN`)."""
    join_since = now - CASE_JOIN_WINDOW
    joined = 0
    for candidate in await _candidates(engine, _MISSED_JOINS, join_since=join_since):
        async with engine.begin() as connection:
            root = await _locked(
                connection,
                _LOCK_ACTIVE_ROOT,
                org_id=candidate.org_id,
                id=candidate.root_id,
                join_since=join_since,
            )
            if (
                root is None
                or await _locked(connection, _LOCK_STRAY, org_id=candidate.org_id, id=candidate.id)
                is None
            ):
                continue
            await connection.execute(
                _SET_ROOT,
                {"org_id": candidate.org_id, "id": candidate.id, "root_id": candidate.root_id},
            )
            await _event(
                connection,
                "finding_joined_case",
                org_id=candidate.org_id,
                finding_id=candidate.root_id,
                at=now,
                joined_finding_id=candidate.id,
            )
            joined += 1
    return joined


async def _heal_left_on_resolved(engine: AsyncEngine, *, now: datetime) -> int:
    """AC17's second half: the detach a resolve's snapshot could not see."""
    left = 0
    for candidate in await _candidates(engine, _LEFT_ON_RESOLVED):
        async with engine.begin() as connection:
            root = await _locked(
                connection, _LOCK_RESOLVED_ROOT, org_id=candidate.org_id, id=candidate.root_id
            )
            if (
                root is None
                or await _locked(
                    connection,
                    _LOCK_STRANDED,
                    org_id=candidate.org_id,
                    id=candidate.id,
                    root_id=candidate.root_id,
                )
                is None
            ):
                continue
            await connection.execute(
                _SET_ROOT, {"org_id": candidate.org_id, "id": candidate.id, "root_id": None}
            )
            await _event(
                connection,
                "finding_left_case",
                org_id=candidate.org_id,
                finding_id=candidate.root_id,
                at=now,
                left_finding_id=candidate.id,
            )
            left += 1
    return left


async def _end_where(
    engine: AsyncEngine,
    *,
    first: TextClause,
    lock: TextClause,
    resolution: str,
    now: datetime,
    **condition: Any,
) -> int:
    ended = 0
    for candidate in await _candidates(engine, first, **condition):
        async with engine.begin() as connection:
            if (
                await _locked(
                    connection, lock, org_id=candidate.org_id, id=candidate.id, **condition
                )
                is None
            ):
                continue
            if await end_case(
                connection,
                org_id=candidate.org_id,
                root_id=candidate.id,
                resolution=resolution,
                at=now,
            ):
                ended += 1
    return ended


async def _start_warnings(engine: AsyncEngine, *, now: datetime) -> int:
    warn_before = now - (CASE_IDLE_LIMIT - CASE_IDLE_WARNING_AHEAD)
    warned = 0
    for candidate in await _candidates(engine, _DUE_WARNINGS, warn_before=warn_before):
        async with engine.begin() as connection:
            if (
                await _locked(
                    connection,
                    _LOCK_DUE_WARNING,
                    org_id=candidate.org_id,
                    id=candidate.id,
                    warn_before=warn_before,
                )
                is None
            ):
                continue
            await connection.execute(
                _MARK_WARNED, {"org_id": candidate.org_id, "id": candidate.id, "at": now}
            )
            await connection.execute(
                text(RECORD_EMAIL_OWED_SQL),
                email_owed_parameters(
                    org_id=candidate.org_id, finding_id=candidate.id, kind="idle_warning", at=now
                ),
            )
            await connection.execute(
                _ASK_IDLE_COMMENT, {"org_id": candidate.org_id, "id": candidate.id, "at": now}
            )
            warned += 1
    return warned


async def sweep(
    engine: AsyncEngine,
    *,
    now: datetime,
    email_sender: case_emails.Sender | None = None,
    app_base_url: str = "",
) -> SweepOutcome:
    """One pass of every step, in an order where each sees the last's work: the
    heals before the endings, so an ending detaches what a heal just joined, and
    the endings before the warning, so a case past 90 days ends unwarned; and the
    emails last, so what this pass owed goes out with it."""
    outcome = SweepOutcome()
    outcome.unassigned = await _clear_assignees(engine, now=now)
    outcome.left = await _heal_left_on_resolved(engine, now=now)
    outcome.joined = await _heal_missed_joins(engine, now=now)
    outcome.ended_idle = await _end_where(
        engine,
        first=_IDLE_CASES,
        lock=_LOCK_IDLE,
        resolution="no_activity",
        now=now,
        idle_before=now - CASE_IDLE_LIMIT,
    )
    outcome.ended_untouched = await _end_where(
        engine,
        first=_UNTOUCHED_CASES,
        lock=_LOCK_UNTOUCHED,
        resolution="recovered_untouched",
        now=now,
        closed_before=now - CASE_UNTOUCHED_AFTER,
    )
    outcome.warned = await _start_warnings(engine, now=now)
    outcome.emailed = await case_emails.send_owed_case_emails(
        engine, sender=email_sender, app_base_url=app_base_url
    )
    outcome.connections_told = await case_emails.tell_connection_admins(
        engine, sender=email_sender, app_base_url=app_base_url, now=now
    )
    return outcome


_sender: case_emails.Sender | None = None
_sender_built = False


def _email_sender() -> case_emails.Sender | None:
    """Built once per process; with no mail configured, owed emails wait."""
    global _sender, _sender_built
    if not _sender_built:
        configured = get_settings().email
        _sender = build_email_sender(configured) if configured else None
        _sender_built = True
    return _sender


@task(CASES_SWEEP_TASK)
def sweep_cases() -> None:
    outcome = with_engine(
        lambda engine: sweep(
            engine,
            now=datetime.now(UTC),
            email_sender=_email_sender(),
            app_base_url=get_settings().app_base_url,
        )
    )
    changed = {name: count for name, count in asdict(outcome).items() if count}
    if changed:
        logger.info("cases swept", **changed)
