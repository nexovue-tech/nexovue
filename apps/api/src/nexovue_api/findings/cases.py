"""A person works a case on a finding (F22): take, assign, resolve, reopen, notes.

The rules this module holds, from the design's *Operations on a case* and *Locks*:

- **The root is the case.** Every act on a joined finding changes the root
  (`_root_of`), and the root is locked first, then the named finding when it
  must be written too.
- **Locks are `FOR NO KEY UPDATE`** (D78): no act here changes a key column, and
  a plain `FOR UPDATE` would block the foreign key check a new joined finding's
  insert takes on its root, which can deadlock against a detection batch.
- **Each operation commits before its response is built** (AC102), so no
  ClickHouse read ever runs under a finding's lock. The caller builds the view
  after the call returns.
- **Transitions are the shared statements** in `nexovue_platform.cases` (D77),
  so a person's resolve and the scheduler's later ones write identical rows.
- **Every event goes through `finding_event_parameters()`** — no free text.
- **No operation here writes a notification row** (AC65): a case is not a
  second alarm.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from nexovue_platform.cases import (
    DETACH_OPEN_JOINED_SQL,
    JOIN_OPEN_SQL,
    NOTES_PER_CASE,
    RECORD_FINDING_EVENT_SQL,
    REOPEN_CASE_SQL,
    RESOLVE_CASE_SQL,
    detach_open_joined_parameters,
    finding_event_parameters,
    join_open_parameters,
    reopen_case_parameters,
    resolve_case_parameters,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.permissions import Action, may
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.data.postgres.models import FindingNote, User
from nexovue_api.errors.exceptions import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
)
from nexovue_api.findings import queries
from nexovue_api.findings.schemas import EmailOutcome

NOT_FOUND: Final = "No such finding."
CHANGED_MEANWHILE: Final = "This case changed since you opened it."
ALREADY_RESOLVED: Final = "This case is already resolved. Reopen it to keep working."
NOT_RESOLVED: Final = "This case is not resolved, so there is nothing to reopen."
ANOTHER_ACTIVE_CASE: Final = "Another case is running on this service."
CANNOT_BE_ASSIGNED: Final = (
    "Only a working member of this organization can be assigned: not a viewer, "
    "a pending invitee or someone who left."
)
CASE_IS_FULL: Final = f"This case already holds {NOTES_PER_CASE} notes, its limit."
NO_SUCH_NOTE: Final = "No such note on this case."
NOT_YOUR_NOTE: Final = "Only the note's author, an owner or an admin can delete it."

# FR5: at most one assignment email per recipient and case in 5 minutes, a guess.
ASSIGNMENT_EMAIL_QUIET: Final = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class AssignmentEmail:
    """What the router needs to send the one email FR5 allows, after commit."""

    outcome: EmailOutcome
    # Set only when outcome is `will_try`.
    recipient_address: str | None = None
    service_name: str | None = None
    severity: str | None = None
    finding_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class CaseChange:
    """What an operation did: the root to build the view from, and the email plan."""

    root: FindingRow
    email: AssignmentEmail


NO_EMAIL: Final = AssignmentEmail(outcome="none")


# ------------------------------------------------------------------ plumbing


async def _event(
    session: AsyncSession, kind: str, row: FindingRow, *, at: datetime, **fields: Any
) -> uuid.UUID:
    """One timeline event on `row`, through the shared writer (AC24)."""
    written = await session.execute(
        text(RECORD_FINDING_EVENT_SQL),
        finding_event_parameters(kind, org_id=row.org_id, finding_id=row.id, at=at, **fields),
    )
    event_id: uuid.UUID = written.scalar_one()
    return event_id


def _person(actor: User) -> dict[str, Any]:
    return {"actor_kind": "person", "actor_id": actor.id}


async def _load(session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID) -> FindingRow:
    row = await queries.find_row(session, org_id=org_id, finding_id=finding_id)
    if row is None:
        raise NotFoundError(NOT_FOUND)
    return row


async def _lock(session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID) -> FindingRow:
    row = await queries.lock_finding(session, org_id=org_id, finding_id=finding_id)
    if row is None:
        raise NotFoundError(NOT_FOUND)
    return row


async def _root_of(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> FindingRow:
    """The locked case row for an act on `finding_id`: its root when it joined one.

    The root is read unlocked first and locked second, root before joined (the
    lock order detection relies on); the joined row is re-read after so a detach
    that raced this act is seen rather than overwritten.
    """
    row = await _load(session, org_id=org_id, finding_id=finding_id)
    while row.root_finding_id is not None:
        root = await _lock(session, org_id=org_id, finding_id=row.root_finding_id)
        row = await _load(session, org_id=org_id, finding_id=finding_id)
        if row.root_finding_id == root.id:
            return root
    return await _lock(session, org_id=org_id, finding_id=finding_id)


async def _named_assignee(session: AsyncSession, root: FindingRow) -> str:
    names = (
        {}
        if root.assignee_id is None
        else await queries.person_names(session, org_id=root.org_id, user_ids=[root.assignee_id])
    )
    name = names.get(root.assignee_id or uuid.uuid4(), "Someone")
    return name


def _acknowledge_if_unacknowledged(row: FindingRow, actor: User, at: datetime) -> bool:
    """FR2: taking or assigning an open, unacknowledged finding acknowledges it."""
    if row.state != "open" or row.acknowledged_at is not None:
        return False
    row.acknowledged_by, row.acknowledged_at = actor.id, at
    return True


async def _audit(
    session: AsyncSession,
    action: str,
    row: FindingRow,
    actor: User | None,
    detail: dict[str, Any] | None = None,
) -> None:
    await audit.record_case_action(
        session,
        org_id=row.org_id,
        actor_user_id=None if actor is None else actor.id,
        action=action,
        finding_id=row.id,
        detail=detail,
    )


# ------------------------------------------------------------ starting a case


async def _start_case(
    session: AsyncSession, row: FindingRow, actor: User, *, at: datetime, assign_actor: bool
) -> FindingRow:
    """Open a case on `row`, or join the service's one active case (BR7, FR11).

    The partial unique index is the arbiter: if it refuses, the service already
    has an active case, and the act joins it and answers with it (AC14).
    """
    # The savepoint opens before the row is touched: opening one later would
    # autoflush the dirty row outside it, and the index's refusal would escape.
    savepoint = await session.begin_nested()
    try:
        row.case_state = "investigating"
        row.case_opened_at = at
        row.case_activity_at = at
        if assign_actor:
            row.assignee_id = actor.id
            row.assignee_acted_at = at
        await session.flush()
    except IntegrityError:
        await savepoint.rollback()
        # The rollback expired the row's ORM state; reload it before the join
        # path reads it, or a lazy load would run outside the async bridge.
        await session.refresh(row)
        return await _join_existing_case(session, row, actor, at=at, assign_actor=assign_actor)
    await savepoint.commit()
    if row.state == "closed":
        # FR11: starting on a closed finding brings in the scope's open finding.
        await _join_open(session, row, actor, at=at)
    acknowledged = _acknowledge_if_unacknowledged(row, actor, at) if assign_actor else False
    await _event(session, "case_started", row, at=at, **_person(actor))
    if assign_actor:
        await _event(session, "assigned", row, at=at, assignee_id=actor.id, **_person(actor))
    if acknowledged:
        await _event(session, "acknowledged", row, at=at, **_person(actor))
    await _audit(session, audit.CASE_STARTED, row, actor)
    return row


async def _join_existing_case(
    session: AsyncSession, row: FindingRow, actor: User, *, at: datetime, assign_actor: bool
) -> FindingRow:
    """BR7 refused a second active case: this finding joins the one that holds the
    service, and the act lands on that root (FR11, AC14)."""
    root_row = await queries.active_case_on_scope(
        session,
        org_id=row.org_id,
        project_id=row.project_id,
        scope=row.scope,
        service_name=row.service_name,
    )
    if root_row is None:
        # The refusing case resolved in the same instant; one retry is enough,
        # because a service cannot gain a case except through this index.
        raise ConflictError(CHANGED_MEANWHILE)
    root = await _lock(session, org_id=row.org_id, finding_id=root_row.id)
    if root.case_state != "investigating":
        raise ConflictError(CHANGED_MEANWHILE)
    row.root_finding_id = root.id
    # A person joining a finding to the case is an act on that case: its idle clock
    # starts again (design, *Joining*; AC68), unlike a finding joining by itself.
    root.case_activity_at = at
    await session.flush()
    await _event(
        session, "finding_joined_case", root, at=at, joined_finding_id=row.id, **_person(actor)
    )
    if assign_actor and _acknowledge_if_unacknowledged(row, actor, at):
        await _event(session, "acknowledged", row, at=at, **_person(actor))
    if assign_actor and root.assignee_id is None:
        root.assignee_id = actor.id
        root.assignee_acted_at = at
        await _event(session, "assigned", root, at=at, assignee_id=actor.id, **_person(actor))
        await _audit(session, audit.CASE_ASSIGNED, root, actor, {"assignee_id": str(actor.id)})
    return root


async def _join_open(
    session: AsyncSession, root: FindingRow, actor: User | None, *, at: datetime
) -> None:
    """`JOIN_OPEN_SQL`: the scope's open finding, if any and free, joins `root`."""
    joined = await session.execute(
        text(JOIN_OPEN_SQL),
        join_open_parameters(
            org_id=root.org_id,
            project_id=root.project_id,
            scope=root.scope,
            service_name=root.service_name,
            root_finding_id=root.id,
        ),
    )
    for joined_id in joined.scalars():
        await _event(
            session,
            "finding_joined_case",
            root,
            at=at,
            joined_finding_id=joined_id,
            **({"actor_kind": "system"} if actor is None else _person(actor)),
        )


# ------------------------------------------------------------- the operations


async def set_case_state(
    session: AsyncSession,
    *,
    actor: User,
    finding_id: uuid.UUID,
    wanted: Literal["investigating", "resolved"],
    now: datetime,
) -> CaseChange:
    """`PATCH /case`'s state half: resolved resolves; investigating takes a finding
    with no case or an unassigned case, and reopens a resolved one (FR1, FR6)."""
    if wanted == "resolved":
        return await resolve(session, actor=actor, finding_id=finding_id, now=now)
    peeked = await _load(session, org_id=actor.org_id, finding_id=finding_id)
    if peeked.root_finding_id is not None:
        peeked = await _load(session, org_id=actor.org_id, finding_id=peeked.root_finding_id)
    if peeked.case_state == "resolved":
        return await reopen(session, actor=actor, finding_id=finding_id, now=now)
    return await take(session, actor=actor, finding_id=finding_id, now=now)


async def take(
    session: AsyncSession, *, actor: User, finding_id: uuid.UUID, now: datetime
) -> CaseChange:
    """**Take this** (FR1 to FR3): start and assign, on an open or closed finding.

    On a case someone else holds, 409 naming them (AC6); the page then offers
    *Take over*, which is an assignment carrying what the page showed (FR4).
    """
    root = await _root_of(session, org_id=actor.org_id, finding_id=finding_id)
    if root.case_state == "resolved":
        raise ConflictError(ALREADY_RESOLVED)
    if root.case_state == "investigating":
        if root.assignee_id is not None and root.assignee_id != actor.id:
            name = await _named_assignee(session, root)
            raise ConflictError(f"{CHANGED_MEANWHILE} {name} has it.")
        acknowledged = _acknowledge_if_unacknowledged(root, actor, now)
        if root.assignee_id is None:
            root.assignee_id = actor.id
            await _event(session, "assigned", root, at=now, assignee_id=actor.id, **_person(actor))
            await _audit(session, audit.CASE_ASSIGNED, root, actor, {"assignee_id": str(actor.id)})
        root.assignee_acted_at = now
        root.case_activity_at = now
        if acknowledged:
            await _event(session, "acknowledged", root, at=now, **_person(actor))
    else:
        root = await _start_case(session, root, actor, at=now, assign_actor=True)
    await session.commit()
    return CaseChange(root=root, email=NO_EMAIL)


async def assign(
    session: AsyncSession,
    *,
    actor: User,
    finding_id: uuid.UUID,
    assignee_id: uuid.UUID | None,
    expected_assignee_id: uuid.UUID | None,
    expectation_given: bool,
    email_available: bool,
    now: datetime,
) -> CaseChange:
    """FR4, FR5: assign an active case to a working member, or to nobody.

    The assignment carries what the page last showed and is refused with 409 if
    the assignee changed meanwhile. The 5-minute email limit is decided here,
    under the root's lock, from `assigned` events (D91) — nothing new is stored.
    """
    root = await _root_of(session, org_id=actor.org_id, finding_id=finding_id)
    if root.case_state == "resolved":
        raise ConflictError(ALREADY_RESOLVED)
    if expectation_given and root.assignee_id != expected_assignee_id:
        name = await _named_assignee(session, root)
        raise ConflictError(
            f"{CHANGED_MEANWHILE} {name} has it."
            if root.assignee_id is not None
            else f"{CHANGED_MEANWHILE} Nobody has it now."
        )

    previous = root.assignee_id
    starting = root.case_state is None
    if assignee_id is None and previous is None:
        raise InvalidRequestError(
            "There is no case to unassign: nothing has started."
            if starting
            else "Nobody is assigned, so there is nothing to clear."
        )
    assignee: User | None = None
    if assignee_id is not None:
        assignee = await queries.lock_working_member(
            session, org_id=actor.org_id, user_id=assignee_id
        )
        if (
            assignee is None
            or assignee.removed_at is not None
            or assignee.password_hash is None
            or assignee.role == "viewer"
        ):
            raise InvalidRequestError(CANNOT_BE_ASSIGNED)

    if starting:
        root = await _start_case(session, root, actor, at=now, assign_actor=False)
    acknowledged = (
        _acknowledge_if_unacknowledged(root, actor, now) if assignee_id is not None else False
    )

    root.assignee_id = assignee_id
    root.assignee_acted_at = None if assignee_id is None else now
    root.case_activity_at = now
    root.case_warned_at = None
    # FR49: the first take of an untouched case is not work; a handover or a
    # takeover of somebody's case is.
    if previous is not None or (assignee_id is not None and assignee_id != actor.id):
        root.case_worked_at = root.case_worked_at or now

    # Decided before the `assigned` event lands, or this assignment's own event
    # would count against its 5-minute limit (D91).
    email = await _assignment_email(
        session, root, actor, assignee, email_available=email_available, now=now
    )

    if assignee_id is None:
        assert previous is not None  # refused above, before anything moved
        await _event(session, "unassigned", root, at=now, assignee_id=previous, **_person(actor))
    else:
        await _event(session, "assigned", root, at=now, assignee_id=assignee_id, **_person(actor))
    if acknowledged:
        await _event(session, "acknowledged", root, at=now, **_person(actor))
    await _audit(
        session,
        audit.CASE_ASSIGNED,
        root,
        actor,
        {"assignee_id": None if assignee_id is None else str(assignee_id)},
    )
    await session.commit()
    return CaseChange(root=root, email=email)


async def _assignment_email(
    session: AsyncSession,
    root: FindingRow,
    actor: User,
    assignee: User | None,
    *,
    email_available: bool,
    now: datetime,
) -> AssignmentEmail:
    """FR5's decision, made under the root's lock so two assignments at the same
    moment cannot both pass the 5-minute limit (D91)."""
    if assignee is None or assignee.id == actor.id:
        return NO_EMAIL
    if not email_available:
        return AssignmentEmail(outcome="not_configured")
    recently_told = await queries.assignment_email_sent_since(
        session,
        org_id=root.org_id,
        root_id=root.id,
        assignee_id=assignee.id,
        since=now - ASSIGNMENT_EMAIL_QUIET,
    )
    if recently_told:
        return AssignmentEmail(outcome="rate_limited")
    return AssignmentEmail(
        outcome="will_try",
        recipient_address=assignee.email,
        service_name=root.service_name,
        severity=root.severity,
        finding_id=root.id,
    )


async def resolve(
    session: AsyncSession, *, actor: User, finding_id: uuid.UUID, now: datetime
) -> CaseChange:
    """FR6: resolve an active case. In the same transaction, any open joined
    finding leaves it (D44) and becomes a plain finding."""
    root = await _root_of(session, org_id=actor.org_id, finding_id=finding_id)
    if root.case_state != "investigating":
        raise ConflictError(
            ALREADY_RESOLVED if root.case_state == "resolved" else CHANGED_MEANWHILE
        )
    await _resolve_root(session, root, actor, resolution="resolved", at=now)
    await session.commit()
    return CaseChange(root=root, email=NO_EMAIL)


async def _resolve_root(
    session: AsyncSession, root: FindingRow, actor: User | None, *, resolution: str, at: datetime
) -> None:
    """The shared ending every resolver runs (AC103): resolve, detach, events, audit."""
    resolved = await session.execute(
        text(RESOLVE_CASE_SQL),
        resolve_case_parameters(
            org_id=root.org_id,
            finding_id=root.id,
            resolution=resolution,
            at=at,
            resolved_by=None if actor is None else actor.id,
        ),
    )
    if resolved.scalar_one_or_none() is None:
        raise ConflictError(CHANGED_MEANWHILE)
    detached = await session.execute(
        text(DETACH_OPEN_JOINED_SQL),
        detach_open_joined_parameters(org_id=root.org_id, root_finding_id=root.id),
    )
    actor_fields = {"actor_kind": "system"} if actor is None else _person(actor)
    for left_id in detached.scalars():
        await _event(
            session, "finding_left_case", root, at=at, left_finding_id=left_id, **actor_fields
        )
    await _event(session, "case_resolved", root, at=at, resolution=resolution, **actor_fields)
    await _audit(session, audit.CASE_RESOLVED, root, actor, {"resolution": resolution})
    await session.refresh(root)


async def reopen(
    session: AsyncSession, *, actor: User, finding_id: uuid.UUID, now: datetime
) -> CaseChange:
    """FR6: reopen a resolved case, running the join again (D44). If the scope's
    open finding holds its own active case, the unique index refuses, and that is
    BR7's 409 naming the other case (AC57)."""
    root = await _root_of(session, org_id=actor.org_id, finding_id=finding_id)
    if root.case_state != "resolved":
        raise ConflictError(NOT_RESOLVED)
    savepoint = await session.begin_nested()
    try:
        reopened = await session.execute(
            text(REOPEN_CASE_SQL),
            reopen_case_parameters(org_id=root.org_id, finding_id=root.id, at=now),
        )
        if reopened.scalar_one_or_none() is None:
            raise ConflictError(CHANGED_MEANWHILE)
        await savepoint.commit()
    except IntegrityError as refused:
        await savepoint.rollback()
        raise ConflictError(ANOTHER_ACTIVE_CASE) from refused
    await session.refresh(root)
    # A reopen is an act beyond taking (FR49).
    root.case_worked_at = root.case_worked_at or now
    await _join_open(session, root, actor, at=now)
    await _event(session, "case_reopened", root, at=now, **_person(actor))
    await _audit(session, audit.CASE_REOPENED, root, actor)
    await session.commit()
    return CaseChange(root=root, email=NO_EMAIL)


# ----------------------------------------------- the three existing actions


async def lock_with_root(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> tuple[FindingRow, FindingRow | None]:
    """The named finding locked, after its root when it joined one: the lock
    order every person act keeps (design, *Locks*)."""
    row = await _load(session, org_id=org_id, finding_id=finding_id)
    while row.root_finding_id is not None:
        root = await _lock(session, org_id=org_id, finding_id=row.root_finding_id)
        row = await _load(session, org_id=org_id, finding_id=finding_id)
        if row.root_finding_id == root.id:
            return await _lock(session, org_id=org_id, finding_id=finding_id), root
    return await _lock(session, org_id=org_id, finding_id=finding_id), None


async def on_acknowledged(
    session: AsyncSession,
    row: FindingRow,
    root: FindingRow | None,
    actor: User,
    *,
    now: datetime,
) -> None:
    """What acknowledging adds under F22 (FR3, D20, AC60): the event, and a case.

    On a standalone finding: no case starts one assigned to the actor; an
    unassigned case gains the actor; an assigned one keeps its assignee. On a
    joined finding the same, on its root, and only an unassigned root is taken.
    The caller holds both locks, root first, and commits.
    """
    await _event(session, "acknowledged", row, at=now, **_person(actor))
    if root is not None:
        if root.case_state == "investigating" and root.assignee_id is None:
            root.assignee_id = actor.id
            root.assignee_acted_at = now
            root.case_activity_at = now
            await _event(session, "assigned", root, at=now, assignee_id=actor.id, **_person(actor))
            await _audit(session, audit.CASE_ASSIGNED, root, actor, {"assignee_id": str(actor.id)})
        return
    if row.case_state is None:
        # Through the savepoint path, so a service whose active case lives on an
        # earlier finding joins it instead of tripping the index (AC14).
        await _start_case(session, row, actor, at=now, assign_actor=True)
    elif row.case_state == "investigating" and row.assignee_id is None:
        row.assignee_id = actor.id
        row.assignee_acted_at = now
        row.case_activity_at = now
        await _event(session, "assigned", row, at=now, assignee_id=actor.id, **_person(actor))
        await _audit(session, audit.CASE_ASSIGNED, row, actor, {"assignee_id": str(actor.id)})


async def on_never_a_problem(
    session: AsyncSession,
    row: FindingRow,
    root: FindingRow | None,
    actor: User,
    *,
    kind: Literal["false_alarm", "new_normal"],
    now: datetime,
) -> None:
    """BR6, FR7 (AC8): a false alarm or new normal on a root resolves its case with
    that reason; on a joined finding it adds the event and the case stands (D22).
    The caller holds the locks, root first, and commits."""
    await _event(session, kind, row, at=now, **_person(actor))
    if root is not None:
        return
    if row.case_state == "investigating":
        await _resolve_root(session, row, actor, resolution=kind, at=now)


# ----------------------------------------------------------------------- notes


async def add_note(
    session: AsyncSession, *, actor: User, finding_id: uuid.UUID, body: str, now: datetime
) -> tuple[FindingNote, FindingRow]:
    """FR14: a note, always on the root. A note starts a case with nobody assigned
    and no acknowledgement (D70, AC99); on a resolved case it changes nothing."""
    root = await _root_of(session, org_id=actor.org_id, finding_id=finding_id)
    if await queries.note_count(session, org_id=root.org_id, root_id=root.id) >= NOTES_PER_CASE:
        raise InvalidRequestError(CASE_IS_FULL)
    if root.case_state is None:
        root = await _start_case(session, root, actor, at=now, assign_actor=False)
    elif root.case_state == "investigating":
        root.case_activity_at = now
        root.case_warned_at = None
        if root.assignee_id == actor.id:
            root.assignee_acted_at = now
    note = FindingNote(org_id=root.org_id, finding_id=root.id, author_id=actor.id, body=body)
    session.add(note)
    await session.flush()
    if root.case_state == "investigating":
        # A note is the first act beyond taking (FR49).
        root.case_worked_at = root.case_worked_at or now
    await _event(session, "note_added", root, at=now, note_id=note.id, **_person(actor))
    await session.commit()
    return note, root


async def delete_note(
    session: AsyncSession,
    *,
    actor: User,
    finding_id: uuid.UUID,
    note_id: uuid.UUID,
    now: datetime,
) -> FindingRow:
    """FR15, BR16: the author — whatever their current role — or an owner or admin
    deletes a note. The text is gone in this transaction; the timeline keeps only
    that a note was deleted (AC19)."""
    root = await _root_of(session, org_id=actor.org_id, finding_id=finding_id)
    note = await queries.find_note(session, org_id=root.org_id, root_id=root.id, note_id=note_id)
    if note is None:
        raise NotFoundError(NO_SUCH_NOTE)
    if note.author_id != actor.id and not may(actor.role, Action.DELETE_ANY_CASE_NOTE):
        raise PermissionDeniedError(NOT_YOUR_NOTE)
    await session.delete(note)
    await _event(session, "note_deleted", root, at=now, note_id=note_id, **_person(actor))
    await _audit(session, audit.CASE_NOTE_DELETED, root, actor, {"note_id": str(note_id)})
    await session.commit()
    return root


# ------------------------------------------------------- clearing an assignee


async def clear_assignee(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID, now: datetime
) -> int:
    """FR8, AC10: removal, account deletion and a move to viewer each clear the
    person from every active case, with an event.

    `SKIP LOCKED`, so this can never deadlock against the detection job's batch;
    a case locked at that moment is cleared by the next sweep (T2.6). The event's
    actor is the system on all three routes, so they stay identical.
    """
    rows = await queries.lock_assigned_cases_skip_locked(session, org_id=org_id, user_id=user_id)
    for row in rows:
        row.assignee_id = None
        row.assignee_acted_at = None
        await _event(session, "unassigned", row, at=now, assignee_id=user_id, actor_kind="system")
    return len(rows)
