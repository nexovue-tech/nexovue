"""A case's tracker issues, from the API's side (F22 plan T4.3; design, *Creating without
duplicates*, *API*).

**Nothing here calls a tracker** (AC32, BR15). A person's click writes the link row,
`creating`, with the facts the preview showed frozen on it (AC83), and adds a sync job;
`scheduler-connections` writes the issue. Every act that moves a case asks the synced
issue to follow through `wanted_state` (`want_for_resolve`, `want_for_reopen`), which
the job reconciles.

**Only the click route makes a `creating` link** (AC88). Rules held here: one synced
issue per case (the database's index, a double click is a 409); a link starts a case
with nobody assigned and no acknowledgement (D70, AC99), and is the first act beyond
taking (FR49), so it stops the untouched end; a public repository nobody allowed is
refused at the click, as the job refuses it again before every create (BR12, AC51).

Each operation takes the root's lock first, as every case act does (design, *Locks*),
and commits before its view is built (AC102).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nexovue_platform.connections import (
    CREATING,
    FAILING,
    MAX_CREATING_PER_CONNECTION,
    SYNCED,
    TRACKER_KINDS,
    WORKING,
    IssueFacts,
    issue_facts,
    issue_text,
    link_url,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import Connection, ConnectionRepository, FindingLink, User
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.errors.exceptions import ConflictError, NotFoundError
from nexovue_api.findings import cases, link_sync
from nexovue_api.findings.schemas import CaseLink, IssuePreview

NO_SUCH_PLACE: Final = "No such repository on this organization's connection."
NOT_CONNECTED: Final = "GitHub is not connected, so no issue can be created. The case still works."
NOT_READY: Final = (
    "The GitHub connection needs attention, so no issue can be created now. The case still works."
)
PLACE_UNREACHABLE: Final = (
    "nexovue cannot reach this repository now. Choose another, or ask an admin."
)
PUBLIC_REFUSED: Final = (
    "This repository is public. nexovue does not create issues in a public repository "
    "unless an admin allows it."
)
ALREADY_SYNCED: Final = "This case already has a synced issue."
TOO_MANY_CREATING: Final = (
    f"{MAX_CREATING_PER_CONNECTION} issues are already being created on this connection. "
    "Try again in a moment."
)
CASE_RESOLVED: Final = "This case is resolved. Reopen it to create an issue for it."
NO_SIGNAL: Final = "This finding has no signal to describe, so no issue can be written for it."
NO_ADDRESS: Final = (
    "This install's own address is not set (APP_BASE_URL), so an issue could not link back "
    "to the case. An operator can set it."
)
NO_SUCH_LINK: Final = "No such issue on this case."
RETRY_CANNOT_HELP: Final = "Trying again cannot help this issue now."
STILL_CREATING: Final = "This issue is still being created. Remove it once it has been."

# Statuses a person's retry can move: a failure that may pass, or one a person fixed.
_RETRYABLE: Final = ("waiting", "needs_attention", CREATING)
# A link needing attention for these reasons waits on a person elsewhere, not a retry.
_RETRY_CANNOT_HELP: Final = ("moved_out_of_reach", "reopen_blocked")


# ------------------------------------------------------------------ reading


async def links_of(
    session: AsyncSession, *, org_id: uuid.UUID, root_id: uuid.UUID
) -> list[CaseLink]:
    """The case's links, synced first, with the address nexovue builds (D81)."""
    rows = (
        await session.execute(
            select(FindingLink, Connection.kind, ConnectionRepository.key)
            .outerjoin(
                Connection,
                (Connection.org_id == FindingLink.org_id)
                & (Connection.id == FindingLink.connection_id),
            )
            .outerjoin(
                ConnectionRepository,
                (ConnectionRepository.org_id == FindingLink.org_id)
                & (ConnectionRepository.id == FindingLink.place_id),
            )
            .where(FindingLink.org_id == org_id, FindingLink.finding_id == root_id)
            .order_by(FindingLink.role.desc(), FindingLink.created_at)
        )
    ).all()
    return [_view(link, kind, place_key) for link, kind, place_key in rows]


def _view(link: FindingLink, kind: str | None, place_key: str | None) -> CaseLink:
    url = None
    if link.external_key is not None and kind in TRACKER_KINDS:
        try:
            url = link_url(kind, None, link.external_key)
        except ValueError:
            url = None
    return CaseLink(
        id=link.id,
        kind=kind,
        role=link.role,
        sync_status=link.sync_status,
        external_key=link.external_key,
        url=url,
        place_key=place_key,
        seen_state=link.seen_state,
        wanted_state=link.wanted_state,
        comment_owed=link.comment_wanted is not None,
        tracker_closed_at=link.tracker_closed_at,
        last_synced_at=link.last_synced_at,
        created_at=link.created_at,
        last_failure=link.last_failure,
        retry_helps=_retry_helps(link),
    )


def _retry_helps(link: FindingLink) -> bool:
    return (
        link.sync_status in _RETRYABLE
        and link.last_failure not in _RETRY_CANNOT_HELP
        and (link.sync_status != CREATING or link.last_failure is not None)
    )


# --------------------------------------------------------------- the place


@dataclass(frozen=True, slots=True)
class _Place:
    connection: Connection
    place: ConnectionRepository


async def _place(session: AsyncSession, *, org_id: uuid.UUID, place_id: uuid.UUID) -> _Place:
    """A repository of this organization's live tracker connection; else the same 404."""
    found = (
        await session.execute(
            select(Connection, ConnectionRepository)
            .join(
                ConnectionRepository,
                (ConnectionRepository.org_id == Connection.org_id)
                & (ConnectionRepository.connection_id == Connection.id),
            )
            .where(
                Connection.org_id == org_id,
                ConnectionRepository.org_id == org_id,
                ConnectionRepository.id == place_id,
                Connection.state != "disconnected",
                Connection.kind.in_(TRACKER_KINDS),
            )
        )
    ).one_or_none()
    if found is None:
        raise NotFoundError(NO_SUCH_PLACE)
    return _Place(connection=found[0], place=found[1])


def _refusal(place: _Place) -> str | None:
    """Why the click would be refused here, in the person's words; None if it would not."""
    if place.connection.state not in (WORKING, FAILING):
        return NOT_READY
    if not place.place.is_reachable:
        return PLACE_UNREACHABLE
    if not place.place.is_private and place.place.public_allowed_at is None:
        return PUBLIC_REFUSED
    return None


def _facts_of(root: FindingRow, *, base_url: str) -> dict[str, object]:
    """The facts an issue may carry, from the case's root (D48): its leading signal's
    numbers, its severity, when it started, and the service in strict form."""
    signals = root.signals or []
    if not signals:
        raise ConflictError(NO_SIGNAL)
    leading = signals[0]
    try:
        facts = _facts(root, leading, base_url=base_url)
    except ValueError as unbuildable:
        raise ConflictError(NO_ADDRESS) from unbuildable
    return facts.to_json()


def _facts(root: FindingRow, leading: dict[str, object], *, base_url: str) -> IssueFacts:
    current, normal = leading.get("current"), leading.get("normal")
    return issue_facts(
        finding_id=root.id,
        signal=str(leading.get("signal")),
        current=float(current) if isinstance(current, int | float) else None,
        normal=float(normal) if isinstance(normal, int | float) else None,
        severity=root.severity,
        since=root.started_at,
        service_name=root.service_name,
        base_url=base_url,
    )


# ----------------------------------------------------------------- preview


async def preview(
    session: AsyncSession,
    *,
    actor: User,
    finding_id: uuid.UUID,
    place_id: uuid.UUID,
    base_url: str,
) -> IssuePreview:
    """FR35: exactly what will be written. No network: the text is built here."""
    row = await cases.load_finding(session, org_id=actor.org_id, finding_id=finding_id)
    root = (
        row
        if row.root_finding_id is None
        else await cases.load_finding(session, org_id=actor.org_id, finding_id=row.root_finding_id)
    )
    place = await _place(session, org_id=actor.org_id, place_id=place_id)
    text_ = issue_text(IssueFacts.from_json(_facts_of(root, base_url=base_url)))
    refusal = _refusal(place)
    return IssuePreview(
        place_id=place.place.id,
        place_key=place.place.key,
        title=text_.title,
        body=text_.markdown,
        public=not place.place.is_private,
        refusal=refusal,
    )


# ------------------------------------------------------------------ create


async def create(
    session: AsyncSession,
    *,
    actor: User,
    finding_id: uuid.UUID,
    place_id: uuid.UUID,
    base_url: str,
    now: datetime,
) -> tuple[FindingRow, uuid.UUID]:
    """The click (FR35, AC88): the link `creating`, the facts frozen, a job added."""
    root = await cases.root_for_act(session, org_id=actor.org_id, finding_id=finding_id)
    if root.case_state == "resolved":
        raise ConflictError(CASE_RESOLVED)
    place = await _place(session, org_id=actor.org_id, place_id=place_id)
    refusal = _refusal(place)
    if refusal is not None:
        raise ConflictError(refusal)
    frozen = _facts_of(root, base_url=base_url)
    creating = await session.scalar(
        select(func.count())
        .select_from(FindingLink)
        .where(
            FindingLink.org_id == actor.org_id,
            FindingLink.connection_id == place.connection.id,
            FindingLink.sync_status == CREATING,
        )
    )
    if (creating or 0) >= MAX_CREATING_PER_CONNECTION:
        raise ConflictError(TOO_MANY_CREATING)
    if root.case_state is None:
        root = await cases.start_case_without_assignee(session, root, actor, at=now)
    link = FindingLink(
        org_id=actor.org_id,
        finding_id=root.id,
        connection_id=place.connection.id,
        place_id=place.place.id,
        role=SYNCED,
        sync_status=CREATING,
        frozen_facts=frozen,
        next_sync_at=now,
    )
    try:
        async with session.begin_nested():
            session.add(link)
    except IntegrityError as taken:
        # A double click, or another person's: one synced issue per case.
        raise ConflictError(ALREADY_SYNCED) from taken
    cases.note_act_beyond_taking(root, actor, at=now)
    await cases.record_event(session, "link_added", root, at=now, actor=actor, link_id=link.id)
    await audit.record_case_action(
        session,
        org_id=root.org_id,
        actor_user_id=actor.id,
        action=audit.LINK_ISSUE_REQUESTED,
        finding_id=root.id,
        detail={"link_id": str(link.id), "place_id": str(place.place.id)},
    )
    await link_sync.ask_sync(
        session, org_id=root.org_id, connection_id=place.connection.id, wake=True
    )
    await session.commit()
    return root, link.id


# ------------------------------------------------------- retry and remove


async def _locked_link(
    session: AsyncSession, *, org_id: uuid.UUID, root_id: uuid.UUID, link_id: uuid.UUID
) -> FindingLink:
    link: FindingLink | None = await session.scalar(
        select(FindingLink)
        .where(
            FindingLink.org_id == org_id,
            FindingLink.finding_id == root_id,
            FindingLink.id == link_id,
        )
        .with_for_update()
    )
    if link is None:
        raise NotFoundError(NO_SUCH_LINK)
    return link


async def retry(
    session: AsyncSession, *, actor: User, finding_id: uuid.UUID, link_id: uuid.UUID, now: datetime
) -> FindingRow:
    """FR44: read and act again now, where that can help. Not audited: it repeats a
    recorded act and changes no permission or data (design, *Audit*)."""
    root = await cases.root_for_act(session, org_id=actor.org_id, finding_id=finding_id)
    link = await _locked_link(session, org_id=actor.org_id, root_id=root.id, link_id=link_id)
    if not _retry_helps(link) or link.connection_id is None:
        raise ConflictError(RETRY_CANNOT_HELP)
    link.next_sync_at = now
    await link_sync.ask_sync(
        session, org_id=root.org_id, connection_id=link.connection_id, wake=True
    )
    await session.commit()
    return root


async def remove(
    session: AsyncSession, *, actor: User, finding_id: uuid.UUID, link_id: uuid.UUID, now: datetime
) -> FindingRow:
    """Removes the link here; the issue stays in the tracker. A link being created is
    kept until it is, or the issue the job is writing would be left unknown."""
    root = await cases.root_for_act(session, org_id=actor.org_id, finding_id=finding_id)
    link = await _locked_link(session, org_id=actor.org_id, root_id=root.id, link_id=link_id)
    if link.sync_status == CREATING:
        raise ConflictError(STILL_CREATING)
    await session.delete(link)
    await session.flush()
    await cases.record_event(session, "link_removed", root, at=now, actor=actor, link_id=link_id)
    await audit.record_case_action(
        session,
        org_id=root.org_id,
        actor_user_id=actor.id,
        action=audit.LINK_REMOVED,
        finding_id=root.id,
        detail={"link_id": str(link_id)},
    )
    await session.commit()
    return root
