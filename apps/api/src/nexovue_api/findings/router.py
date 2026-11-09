"""Findings (F6, ADR-022). The reads are on the read surface (ADR-017); the three
actions are a person's (FR19 to FR21, FR23).

`project_id` is a value the caller chose, so it is proved to belong to the
caller's organization, and to a token's scope, before anything is read.
`/findings/{finding_id}` takes no project, so the scope is compared against the
row it loads, as `get_incident` does. Acknowledge and false alarm are every
working role's, as operating alerts is; a new normal moves what detection judges
by, and is an admin's (D11).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Body, Query, status
from nexovue_platform.connections import strict_name
from nexovue_platform.email import EmailError, EmailSender, Message

from nexovue_api.auth.dependencies import CurrentIdentity, CurrentReader, TenantRepository
from nexovue_api.auth.policy import (
    MayConfigureDetection,
    MayOperateAlerts,
    MayOperateCases,
    MayRequestExplanation,
)
from nexovue_api.deps import AppSettings, DbSession, EmailAvailable, OptionalEmail
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.findings import cases, explanations, links, service
from nexovue_api.findings.schemas import (
    CaseChanged,
    CaseChangeRequest,
    CaseFilter,
    CaseNote,
    CreateLinkRequest,
    FalseAlarmRequest,
    FeedState,
    FindingChanged,
    FindingDetail,
    FindingExplanation,
    FindingForMember,
    FindingListResponse,
    FindingStats,
    IssuePreview,
    LinkChanged,
    NoteRequest,
    NoteWritten,
    ReadAgainAccepted,
    TimelineResponse,
)
from nexovue_api.projects.service import require_readable_project

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/findings", tags=["findings"])

ProjectId = Annotated[uuid.UUID, Query(description="Project to read findings from")]


@router.get("", summary="Findings still wrong or still worked first, then the rest; at most 200")
async def list_findings(
    reader: CurrentReader,
    db: DbSession,
    repository: TenantRepository,
    settings: AppSettings,
    project_id: Annotated[
        uuid.UUID | None,
        Query(
            description="Project to read findings from. Required, except when a person "
            "asks for a case filter, which reads every project of the organization"
        ),
    ] = None,
    state: Annotated[FeedState, Query(description="`open` includes acknowledged")] = "all",
    muted: Annotated[bool, Query(description="Read the muted findings instead")] = False,
    case: Annotated[
        CaseFilter | None,
        Query(description="A case filter: roots with a case, muted included (F22 FR22)"),
    ] = None,
) -> FindingListResponse:
    now = datetime.now(UTC)
    for_person = not reader.is_token
    if case is not None:
        # A person's filter reads the organization, so a named project is not used;
        # a token's is proved readable first, the same 404 as `get_finding` (AC110).
        token_project = (
            None
            if for_person or project_id is None
            else await require_readable_project(db, reader=reader, project_id=project_id)
        )
        return await service.list_cases(
            db,
            org_id=reader.org_id,
            which=case,
            viewer_id=None if reader.identity is None else reader.identity.user.id,
            token_project_id=None if token_project is None else token_project.id,
            for_person=for_person,
            now=now,
            show_held_causes=settings.show_held_causes,
        )
    if project_id is None:
        raise InvalidRequestError(service.NAME_A_PROJECT)
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    return await service.list_findings(
        db,
        repository,
        org_id=reader.org_id,
        project_id=project.id,
        state=state,
        muted=muted,
        for_person=for_person,
        now=now,
        show_held_causes=settings.show_held_causes,
    )


# Before `/{finding_id}`, which would otherwise claim the path and refuse it as a
# malformed id.
@router.get("/stats", summary="Findings opened and false alarms over the last days")
async def finding_stats(
    reader: CurrentReader,
    db: DbSession,
    project_id: ProjectId,
    days: Annotated[
        int, Query(ge=1, le=service.MAX_STATS_DAYS, description="The window, ending now")
    ] = service.DEFAULT_STATS_DAYS,
) -> FindingStats:
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    return await service.finding_stats(
        db, org_id=reader.org_id, project_id=project.id, days=days, now=datetime.now(UTC)
    )


@router.get("/{finding_id}", summary="One finding, with its readings while they last")
async def get_finding(
    reader: CurrentReader,
    db: DbSession,
    repository: TenantRepository,
    email_available: EmailAvailable,
    finding_id: uuid.UUID,
) -> FindingForMember | FindingDetail:
    """A signed-in person also sees who acknowledged it, the case's people, the
    false alarm's reason, and what a new normal would be."""
    return await service.get_finding(
        db,
        repository,
        org_id=reader.org_id,
        finding_id=finding_id,
        project_scope=reader.project_scope,
        for_member=not reader.is_token,
        now=datetime.now(UTC),
        email_available=email_available,
    )


@router.get(
    "/{finding_id}/explanation",
    summary="The facts that line up with a finding, and its likely cause where one is released",
)
async def read_explanation(
    reader: CurrentReader,
    db: DbSession,
    settings: AppSettings,
    finding_id: uuid.UUID,
    version: Annotated[
        int | None, Query(ge=1, description="An earlier version; the latest by default")
    ] = None,
) -> FindingExplanation:
    """F3: Postgres only, so a page can poll it. A token reads facts and never words."""
    return await explanations.get_explanation(
        db,
        org_id=reader.org_id,
        finding_id=finding_id,
        project_scope=reader.project_scope,
        number=version,
        show_held_causes=settings.show_held_causes,
        now=datetime.now(UTC),
    )


@router.post(
    "/{finding_id}/explanation/read-again",
    summary="Read the finding's facts again now; at most once a minute",
)
async def read_explanation_again(
    identity: MayRequestExplanation, db: DbSession, finding_id: uuid.UUID
) -> ReadAgainAccepted:
    """FR41: works with AI on or off and costs no tokens. Too soon is 409 with
    `next_read_at`, which the page shows beside the button."""
    return await explanations.read_again(
        db, org_id=identity.org_id, finding_id=finding_id, now=datetime.now(UTC)
    )


@router.get("/{finding_id}/timeline", summary="A case's timeline, newest first")
async def read_timeline(
    reader: CurrentReader,
    db: DbSession,
    finding_id: uuid.UUID,
    before: Annotated[str | None, Query(description="`next_before` from the previous page")] = None,
) -> TimelineResponse:
    """A person reads everything; an API token reads kinds and times alone (FR20)."""
    return await service.read_timeline(
        db,
        org_id=reader.org_id,
        finding_id=finding_id,
        project_scope=reader.project_scope,
        real_person=reader.is_real_person,
        token=reader.is_token,
        before=before,
    )


@router.patch("/{finding_id}/case", summary="Take, assign, resolve or reopen a case")
async def change_case(
    identity: MayOperateCases,
    db: DbSession,
    repository: TenantRepository,
    email: OptionalEmail,
    settings: AppSettings,
    finding_id: uuid.UUID,
    request: CaseChangeRequest,
) -> CaseChanged:
    """One route for a person's case acts (FR1 to FR6). An act on a joined finding
    changes the root, and the response is the root's page."""
    now = datetime.now(UTC)
    fields = request.model_fields_set
    if "assignee_id" in fields:
        change = await cases.assign(
            db,
            actor=identity.user,
            finding_id=finding_id,
            assignee_id=request.assignee_id,
            expected_assignee_id=request.expected_assignee_id,
            expectation_given="expected_assignee_id" in fields,
            email_available=email is not None,
            now=now,
        )
    elif request.case_state is not None:
        change = await cases.set_case_state(
            db,
            actor=identity.user,
            finding_id=finding_id,
            wanted=request.case_state,
            now=now,
            leave_issue_open=request.leave_issue_open,
        )
    else:
        raise InvalidRequestError("Say what to change: a case_state, or an assignee_id.")
    # After the commit, so a failed send cannot fail the assignment (FR5).
    if change.email.outcome == "will_try" and email is not None:
        await _send_assignment_email(email, change.email, app_base_url=settings.app_base_url)
    view = await service.member_view(
        db,
        repository,
        org_id=identity.org_id,
        finding_id=change.root.id,
        now=now,
        email_available=email is not None,
    )
    return CaseChanged(finding=view, email_outcome=change.email.outcome)


async def _send_assignment_email(
    email: EmailSender, plan: cases.AssignmentEmail, *, app_base_url: str
) -> None:
    """FR5's one email: the fact, the service in its strict form, the severity and
    a link — never a finding title or a note (D28)."""
    assert plan.recipient_address is not None and plan.finding_id is not None
    try:
        await email.send(
            to=plan.recipient_address,
            message=Message.CASE_ASSIGNED,
            data={
                "service": strict_name(plan.service_name),
                "severity": plan.severity,
                "link": f"{app_base_url.rstrip('/')}/findings/{plan.finding_id}",
            },
            idempotency_key=f"case-assigned:{plan.finding_id}:{plan.recipient_address}",
        )
    except EmailError as failure:
        logger.error("assignment mail refused; the assignment stands", error=str(failure))


@router.post("/{finding_id}/notes", summary="Write a note on the case")
async def add_case_note(
    identity: MayOperateCases,
    db: DbSession,
    repository: TenantRepository,
    email: OptionalEmail,
    finding_id: uuid.UUID,
    request: NoteRequest,
) -> NoteWritten:
    """A note lands on the root, and starts a case with nobody assigned (D70)."""
    now = datetime.now(UTC)
    note, root = await cases.add_note(
        db, actor=identity.user, finding_id=finding_id, body=request.body, now=now
    )
    view = await service.member_view(
        db,
        repository,
        org_id=identity.org_id,
        finding_id=root.id,
        now=now,
        email_available=email is not None,
    )
    return NoteWritten(
        note=CaseNote(
            id=note.id,
            author_id=identity.user.id,
            author_name=identity.user.name or identity.user.email,
            body=note.body,
            created_at=note.created_at,
        ),
        finding=view,
    )


@router.delete("/{finding_id}/notes/{note_id}", summary="Delete a note; the text is gone")
async def delete_case_note(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    email: OptionalEmail,
    finding_id: uuid.UUID,
    note_id: uuid.UUID,
) -> FindingForMember:
    """The author — whatever their current role, which is why this takes
    `CurrentIdentity` and the service decides (AC25) — or an owner or admin."""
    now = datetime.now(UTC)
    root = await cases.delete_note(
        db, actor=identity.user, finding_id=finding_id, note_id=note_id, now=now
    )
    return await service.member_view(
        db,
        repository,
        org_id=identity.org_id,
        finding_id=root.id,
        now=now,
        email_available=email is not None,
    )


@router.post("/{finding_id}/acknowledge", summary="Say you are handling an open finding")
async def acknowledge_finding(
    identity: MayOperateAlerts, db: DbSession, repository: TenantRepository, finding_id: uuid.UUID
) -> FindingChanged:
    return await service.acknowledge(
        db, repository, actor=identity.user, finding_id=finding_id, now=datetime.now(UTC)
    )


@router.post(
    "/{finding_id}/false-alarm",
    summary="Close a finding as a false alarm, or mark one that recovered",
)
async def mark_false_alarm(
    identity: MayOperateAlerts,
    db: DbSession,
    repository: TenantRepository,
    finding_id: uuid.UUID,
    # Optional, as its one field is: the reason is the person's to give or not.
    request: Annotated[FalseAlarmRequest | None, Body()] = None,
) -> FindingChanged:
    return await service.mark_false_alarm(
        db,
        repository,
        actor=identity.user,
        finding_id=finding_id,
        reason=None if request is None else request.reason,
        now=datetime.now(UTC),
    )


@router.post("/{finding_id}/new-normal", summary="Make an open finding's level the new normal")
async def accept_new_normal(
    identity: MayConfigureDetection,
    db: DbSession,
    repository: TenantRepository,
    finding_id: uuid.UUID,
) -> FindingChanged:
    return await service.accept_new_normal(
        db, repository, actor=identity.user, finding_id=finding_id, now=datetime.now(UTC)
    )


# ------------------------------------------------------- tracker issues (T4.3)


@router.get(
    "/{finding_id}/links/preview",
    summary="Exactly what a new issue would say, before it is created",
)
async def preview_issue(
    identity: MayOperateCases,
    db: DbSession,
    settings: AppSettings,
    finding_id: uuid.UUID,
    place_id: Annotated[uuid.UUID, Query(description="The repository it would go to")],
) -> IssuePreview:
    """FR35: built here, from the case's facts; nothing is sent anywhere."""
    return await links.preview(
        db,
        actor=identity.user,
        finding_id=finding_id,
        place_id=place_id,
        base_url=settings.app_base_url,
    )


@router.post(
    "/{finding_id}/links",
    status_code=status.HTTP_201_CREATED,
    summary="Create a tracker issue for the case; sync writes it",
)
async def create_issue(
    identity: MayOperateCases,
    db: DbSession,
    repository: TenantRepository,
    email: OptionalEmail,
    settings: AppSettings,
    finding_id: uuid.UUID,
    request: CreateLinkRequest,
) -> LinkChanged:
    """The link is `creating` and a job is added; the result arrives without the person
    waiting on the page (FR35)."""
    now = datetime.now(UTC)
    root, link_id = await links.create(
        db,
        actor=identity.user,
        finding_id=finding_id,
        place_id=request.place_id,
        base_url=settings.app_base_url,
        now=now,
    )
    view = await service.member_view(
        db,
        repository,
        org_id=identity.org_id,
        finding_id=root.id,
        now=now,
        email_available=email is not None,
    )
    return LinkChanged(finding=view, link_id=link_id)


@router.post(
    "/{finding_id}/links/{link_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Read and act on a waiting issue again now",
)
async def retry_issue(
    identity: MayOperateCases,
    db: DbSession,
    repository: TenantRepository,
    email: OptionalEmail,
    finding_id: uuid.UUID,
    link_id: uuid.UUID,
) -> LinkChanged:
    now = datetime.now(UTC)
    root = await links.retry(
        db, actor=identity.user, finding_id=finding_id, link_id=link_id, now=now
    )
    view = await service.member_view(
        db,
        repository,
        org_id=identity.org_id,
        finding_id=root.id,
        now=now,
        email_available=email is not None,
    )
    return LinkChanged(finding=view, link_id=link_id)


@router.delete("/{finding_id}/links/{link_id}", summary="Remove an issue here; it stays there")
async def remove_issue(
    identity: MayOperateCases,
    db: DbSession,
    repository: TenantRepository,
    email: OptionalEmail,
    finding_id: uuid.UUID,
    link_id: uuid.UUID,
) -> FindingForMember:
    now = datetime.now(UTC)
    root = await links.remove(
        db, actor=identity.user, finding_id=finding_id, link_id=link_id, now=now
    )
    return await service.member_view(
        db,
        repository,
        org_id=identity.org_id,
        finding_id=root.id,
        now=now,
        email_available=email is not None,
    )
