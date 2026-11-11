"""A case's tracker issues, from the API's side (F22 plan T4.3, T6.1; design, *Creating
without duplicates*, *API*).

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

**A pasted address is parsed, never fetched** (FR40, AC54): it is attached only inside a
repository the organization's connection reaches, as a *reference*, which the job reads
hourly and which never changes the case (FR41). **Promoting** one to the synced issue is
a second, explicit choice, and a job: the public check and first read are
`scheduler-connections`' (AC88).

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
    IN_SYNC,
    MAX_CREATING_PER_CONNECTION,
    MAX_REFERENCES_PER_CASE,
    PROMOTING,
    REFERENCE,
    STOPPED,
    SYNCED,
    TRACKER_KINDS,
    WORKING,
    IssueFacts,
    issue_facts,
    issue_text,
    link_url,
    parse_issue_address,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import Connection, ConnectionRepository, FindingLink, User
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
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
NOT_AN_ISSUE_ADDRESS: Final = (
    "This is not a GitHub issue address. Paste one like https://github.com/acme/api/issues/412."
)
OUTSIDE_CONNECTED: Final = (
    "This issue is not in a repository the GitHub connection reaches, so it cannot be attached."
)
ALREADY_ATTACHED: Final = "This issue is already on this case."
TOO_MANY_REFERENCES: Final = (
    f"A case holds at most {MAX_REFERENCES_PER_CASE} attached issues. Remove one first."
)
ONLY_A_REFERENCE: Final = "Only an attached issue can be synced."
PROMOTE_RESOLVED: Final = "This case is resolved. Reopen it to sync an issue with it."
NOT_READ_YET: Final = (
    "nexovue has not read this issue yet, so it cannot be synced. Try again in a minute."
)
SYNCED_ELSEWHERE: Final = "This issue is already synced with another case."
# The partial unique index that keeps an issue synced on one case only (0061).
_SYNCED_ONCE_INDEX: Final = "uq_finding_links_synced_once"

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
            select(
                FindingLink,
                Connection.kind,
                ConnectionRepository.key,
                ConnectionRepository.is_private,
            )
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
    return [
        _view(link, kind, place_key, place_public=is_private is False)
        for link, kind, place_key, is_private in rows
    ]


def _view(
    link: FindingLink, kind: str | None, place_key: str | None, *, place_public: bool
) -> CaseLink:
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
        place_public=place_public,
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


# ----------------------------------------------------- references (T6.1)


async def _pasted_place(
    session: AsyncSession, *, org_id: uuid.UUID, url: str
) -> tuple[_Place, str]:
    """FR40, AC54: the pasted address parsed to a key inside a repository the live
    connection reaches, or refused; nothing is fetched, here or anywhere."""
    pasted = parse_issue_address(url)
    if pasted is None:
        raise InvalidRequestError(NOT_AN_ISSUE_ADDRESS)
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
                Connection.state != "disconnected",
                Connection.kind == pasted.kind,
                ConnectionRepository.is_reachable,
                # GitHub's names ignore case; the key takes the place's own spelling.
                func.lower(ConnectionRepository.key) == pasted.place_key.lower(),
            )
        )
    ).first()
    if found is None:
        raise ConflictError(OUTSIDE_CONNECTED)
    place = _Place(connection=found[0], place=found[1])
    return place, pasted.key_in(place.place.key)


async def attach_reference(
    session: AsyncSession, *, actor: User, finding_id: uuid.UUID, url: str, now: datetime
) -> tuple[FindingRow, uuid.UUID]:
    """FR40, FR41: any issue in a connected repository, shown on the case and read hourly,
    never changing it. Like a note, it is allowed on a resolved case and does not reopen
    it; on a finding with no case, it starts one with nobody assigned (D70)."""
    root = await cases.root_for_act(session, org_id=actor.org_id, finding_id=finding_id)
    place, external_key = await _pasted_place(session, org_id=actor.org_id, url=url)
    on_case = (
        await session.execute(
            select(FindingLink.role, FindingLink.connection_id, FindingLink.external_key).where(
                FindingLink.org_id == actor.org_id, FindingLink.finding_id == root.id
            )
        )
    ).all()
    if any(
        connection_id == place.connection.id and key == external_key
        for _, connection_id, key in on_case
    ):
        raise ConflictError(ALREADY_ATTACHED)
    if sum(1 for role, _, _ in on_case if role == REFERENCE) >= MAX_REFERENCES_PER_CASE:
        raise ConflictError(TOO_MANY_REFERENCES)
    if root.case_state is None:
        root = await cases.start_case_without_assignee(session, root, actor, at=now)
    link = FindingLink(
        org_id=actor.org_id,
        finding_id=root.id,
        connection_id=place.connection.id,
        place_id=place.place.id,
        role=REFERENCE,
        sync_status=IN_SYNC,
        external_key=external_key,
        next_sync_at=now,
    )
    session.add(link)
    await session.flush()
    cases.note_act_beyond_taking(root, actor, at=now)
    await cases.record_event(session, "link_added", root, at=now, actor=actor, link_id=link.id)
    await audit.record_case_action(
        session,
        org_id=root.org_id,
        actor_user_id=actor.id,
        action=audit.LINK_ADDED,
        finding_id=root.id,
        detail={"link_id": str(link.id), "place_id": str(place.place.id)},
    )
    await link_sync.ask_sync(
        session, org_id=root.org_id, connection_id=place.connection.id, wake=True
    )
    await session.commit()
    return root, link.id


async def promote(
    session: AsyncSession, *, actor: User, finding_id: uuid.UUID, link_id: uuid.UUID, now: datetime
) -> FindingRow:
    """FR41, AC88: a reference becomes the case's synced issue by a second choice the
    console warns about first. It turns `promoting`; the job runs the public check and
    reads the issue, and ends it `in_sync` or `refused`. Only an issue nexovue has read
    is promoted, so the database's one-case-per-issue index decides here, not in the job."""
    root = await cases.root_for_act(session, org_id=actor.org_id, finding_id=finding_id)
    link = await _locked_link(session, org_id=actor.org_id, root_id=root.id, link_id=link_id)
    if link.role != REFERENCE:
        raise ConflictError(ONLY_A_REFERENCE)
    if root.case_state == "resolved":
        raise ConflictError(PROMOTE_RESOLVED)
    if link.external_id is None or link.sync_status != IN_SYNC:
        raise ConflictError(NOT_READ_YET)
    if link.place_id is None:
        raise ConflictError(PLACE_UNREACHABLE)
    place = await _place(session, org_id=actor.org_id, place_id=link.place_id)
    refusal = _refusal(place)
    if refusal is not None:
        raise ConflictError(refusal)
    synced = await session.scalar(
        select(FindingLink.finding_id).where(
            FindingLink.org_id == actor.org_id,
            FindingLink.role == SYNCED,
            FindingLink.sync_status != STOPPED,
            (FindingLink.finding_id == root.id)
            | (
                (FindingLink.connection_id == link.connection_id)
                & (FindingLink.external_id == link.external_id)
            ),
        )
    )
    if synced is not None:
        raise ConflictError(ALREADY_SYNCED if synced == root.id else SYNCED_ELSEWHERE)
    link.role = SYNCED
    link.sync_status = PROMOTING
    link.next_sync_at = now
    link.failures = 0
    link.last_failure = None
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as taken:
        # Another person promoted or created at the same moment; the indexes chose.
        raise ConflictError(
            SYNCED_ELSEWHERE if _SYNCED_ONCE_INDEX in str(taken.orig) else ALREADY_SYNCED
        ) from taken
    cases.note_act_beyond_taking(root, actor, at=now)
    await cases.record_event(session, "link_promoted", root, at=now, actor=actor, link_id=link.id)
    await audit.record_case_action(
        session,
        org_id=root.org_id,
        actor_user_id=actor.id,
        action=audit.LINK_PROMOTED,
        finding_id=root.id,
        detail={"link_id": str(link.id)},
    )
    await link_sync.ask_sync(
        session, org_id=root.org_id, connection_id=place.connection.id, wake=True
    )
    await session.commit()
    return root
