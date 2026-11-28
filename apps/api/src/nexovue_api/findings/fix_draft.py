"""`POST .../draft-pull-request`, `POST .../delete-branch` and `POST .../run-ci` (spec FR22,
FR23, FR24a, FR25, FR28, BR8, AC23, AC25, AC28, AC32a, AC47, AC62, AC66, AC73; design, *The
draft click*, *Run CI*; plan T7.4, T7.10, T8.1).

**Off in code first.** Each route asks `write_access_on(go)` before anything else, where the
route passes `write_gate.WRITE_ACCESS_GO` (None until the founder's recorded go, ADR-097;
foundation item 33): while it is False the click answers 409 `writes_off` with its sentence,
reads nothing, claims nothing and adds no job.

**Run CI**, with the go: an `opened` draft whose pull request is still open; the request
(`ci_requested_by`, `ci_requested_at`) stored, any earlier Run CI failure cleared,
`fix.ci_requested` committed with it, and the job added with ids only. The job checks the
person's write access on GitHub and that the branch is still Nexovue's (FR25).

With the go, the click, in one transaction:

1. `MayOpenFixPullRequest` (the router): a person, never a token or a support view.
2. This person's own `draft` check, passed within 15 minutes, whose digest the request
   carries (`not_your_check`, `check_expired`, `changed_since_check` otherwise).
3. The requester's note, under FR16's prose checks; in a public repository kept only when
   they confirmed it (FR24a, AC62).
4. The case's root locked as detection locks it, then the suggestion claimed: `approved`,
   the person, the time, the digest and the note, only while it is `proposed` with its code.
   **The one-draft index refusing it answers `draft_open`** (BR8, AC25).
5. `fix.pull_request_attempted`, committed with the claim (AC32a); a case started on the
   finding if none runs (FR28, D42); the draft job added, ids only.

**Nothing here calls GitHub:** `scheduler-connections` does.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from nexovue_platform.fixes.pull_request_text import NoteRefused, checked_note
from nexovue_platform.fixes.records import (
    APPROVED,
    CHANGED_SINCE_CHECK,
    CHECK_FOR_DRAFT,
    CHECK_PASSED,
    DRAFT_CHECK_WITHIN,
    FIX_CI_REQUESTED,
    FIX_PULL_REQUEST_ATTEMPTED,
    KIND_SOURCE,
    OPENED,
    PROPOSED,
    PULL_REQUEST_NOT_OPEN,
    PULL_REQUEST_NOT_OPENED,
    PULL_REQUEST_OPEN,
    WRITES_OFF,
    delete_fix_branch_request,
    open_fix_pull_request_request,
    run_fix_ci_request,
)
from nexovue_platform.fixes.sentences import (
    CHECK_EXPIRED,
    CI_FAILURE_SENTENCES,
    DRAFT_REFUSAL_SENTENCES,
    ELIGIBILITY_SENTENCES,
    GENERAL_HAS_NO_CODE,
    NO_WRITTEN_SUGGESTION,
    NOT_YOUR_CHECK,
    NOTE_NOT_CONFIRMED,
    NOTE_REFUSED,
    WRITE_FAILURE_SENTENCES,
)
from nexovue_platform.fixes.write_gate import WriteGo, write_access_on
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.postgres.models import ConnectionRepository, FindingFix, FindingFixCheck
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.findings import cases, fix_queries
from nexovue_api.findings.fix_source_schemas import (
    CiRequested,
    DeleteBranchStarted,
    DraftRequest,
    DraftStarted,
)

NO_SUCH_FIX: Final = "No such suggestion for this finding."
NOT_YOUR_CHECK_CODE: Final = "not_your_check"
CHECK_EXPIRED_CODE: Final = "check_expired"
DRAFT_OPEN_CODE: Final = "draft_open"
NOT_PROPOSED_CODE: Final = "not_proposed"
NOTE_REFUSED_CODE: Final = "note_refused"
NOTE_NOT_CONFIRMED_CODE: Final = "note_not_confirmed"
NO_BRANCH_TO_DELETE: Final = (
    "There is no branch of this suggestion to delete: its pull request must be closed, or "
    "never opened."
)
NO_BRANCH_CODE: Final = "no_branch_to_delete"
# The one-draft index (migration 0083): its refusal means another draft holds the finding.
_ONE_DRAFT_INDEX: Final = "uq_finding_fixes_one_draft"
# A branch may be deleted once its pull request is closed or was never opened (FR23).
_DELETABLE_STATES: Final = frozenset({"closed", PULL_REQUEST_NOT_OPENED})


def _refuse_while_off(go: WriteGo | None) -> None:
    if not write_access_on(go):
        raise ConflictError(DRAFT_REFUSAL_SENTENCES[WRITES_OFF], code=WRITES_OFF)


async def open_draft(
    db: AsyncSession,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    request: DraftRequest,
    go: WriteGo | None,
    now: datetime,
) -> DraftStarted:
    """The click. `go` is `write_gate.WRITE_ACCESS_GO` from the route."""
    _refuse_while_off(go)
    org_id, user_id = identity.org_id, identity.user.id
    row = await _source_row(db, org_id=org_id, finding_id=finding_id, fix_id=fix_id)
    if row.state != PROPOSED or row.code_deleted_at is not None:
        raise ConflictError(NO_WRITTEN_SUGGESTION, code=NOT_PROPOSED_CODE)
    await _own_passed_check(db, row, user_id=user_id, digest=request.digest, now=now)
    note = await _note(db, row, request)
    root = await cases.root_for_act(db, org_id=org_id, finding_id=finding_id)
    try:
        async with db.begin_nested():
            claimed = await db.execute(
                update(FindingFix)
                .where(
                    FindingFix.org_id == org_id,
                    FindingFix.id == fix_id,
                    FindingFix.state == PROPOSED,
                    FindingFix.code_deleted_at.is_(None),
                )
                .values(
                    state=APPROVED,
                    state_changed_at=now,
                    approved_by=user_id,
                    approved_at=now,
                    approved_digest=request.digest,
                    requester_note=note,
                    requester_note_confirmed=note is not None and request.note_confirmed,
                    last_write_failure=None,
                )
                .returning(FindingFix.id)
                .execution_options(synchronize_session=False)
            )
            if claimed.first() is None:
                raise ConflictError(NO_WRITTEN_SUGGESTION, code=NOT_PROPOSED_CODE)
    except IntegrityError as refused:
        if _ONE_DRAFT_INDEX not in str(refused.orig):
            raise
        holder = await fix_queries.holding_draft(db, org_id=org_id, finding_id=finding_id)
        link = (holder.pull_request_url if holder is not None else None) or "GitHub"
        raise ConflictError(
            ELIGIBILITY_SENTENCES["draft_open"].requester.format(link=link),
            code=DRAFT_OPEN_CODE,
        ) from refused
    audit.record_fix_event(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        action=FIX_PULL_REQUEST_ATTEMPTED,
        target_kind=audit.FINDING_TARGET,
        target_id=finding_id,
        detail={
            "fix_id": str(fix_id),
            "repository_id": str(row.connection_repository_id),
            "with_note": note is not None,
        },
    )
    if root.case_state is None:
        await cases.start_case_without_assignee(db, root, identity.user, at=now)
    await fix_queries.queue_suggestion(
        db, open_fix_pull_request_request(fix_id=fix_id, org_id=org_id)
    )
    await db.commit()
    return DraftStarted(fix_id=fix_id, state=APPROVED)


async def _source_row(
    db: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID, fix_id: uuid.UUID
) -> FindingFix:
    row = await fix_queries.fix_of(db, org_id=org_id, finding_id=finding_id, fix_id=fix_id)
    if row is None:
        raise NotFoundError(NO_SUCH_FIX)
    if row.kind != KIND_SOURCE:
        raise ConflictError(GENERAL_HAS_NO_CODE, code="general_has_no_code")
    return row


async def _own_passed_check(
    db: AsyncSession, row: FindingFix, *, user_id: uuid.UUID, digest: str, now: datetime
) -> None:
    """FR22, AC47: this person's own `draft` check, passed within 15 minutes, whose digest
    is the one the click carries. Another person's check is never used."""
    check: FindingFixCheck | None = await db.scalar(
        select(FindingFixCheck).where(
            FindingFixCheck.org_id == row.org_id,
            FindingFixCheck.fix_id == row.id,
            FindingFixCheck.requested_by == user_id,
            FindingFixCheck.purpose == CHECK_FOR_DRAFT,
        )
    )
    if check is None or check.status not in CHECK_PASSED or check.finished_at is None:
        raise ConflictError(NOT_YOUR_CHECK, code=NOT_YOUR_CHECK_CODE)
    if check.finished_at + DRAFT_CHECK_WITHIN <= now:
        raise ConflictError(CHECK_EXPIRED, code=CHECK_EXPIRED_CODE)
    if check.digest != digest:
        raise ConflictError(WRITE_FAILURE_SENTENCES[CHANGED_SINCE_CHECK], code=CHANGED_SINCE_CHECK)


async def _note(db: AsyncSession, row: FindingFix, request: DraftRequest) -> str | None:
    """FR24a: one plain line; in a public repository, kept only when confirmed (AC62)."""
    try:
        note = checked_note(request.note)
    except NoteRefused as refused:
        raise InvalidRequestError(NOTE_REFUSED, code=NOTE_REFUSED_CODE) from refused
    if note is None:
        return None
    private = await db.scalar(
        select(ConnectionRepository.is_private).where(
            ConnectionRepository.org_id == row.org_id,
            ConnectionRepository.id == row.connection_repository_id,
        )
    )
    if private is not True and not request.note_confirmed:
        raise InvalidRequestError(NOTE_NOT_CONFIRMED, code=NOTE_NOT_CONFIRMED_CODE)
    return note


async def delete_branch(
    db: AsyncSession,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    go: WriteGo | None,
) -> DeleteBranchStarted:
    """FR23, AC66: a job deletes `nexovue/suggestion-<id>` once its pull request is closed
    or was never opened, after checking its head and the person's write access."""
    _refuse_while_off(go)
    org_id = identity.org_id
    row = await _source_row(db, org_id=org_id, finding_id=finding_id, fix_id=fix_id)
    if (
        row.pull_request_branch is None
        or row.pull_request_branch_deleted_at is not None
        or row.pull_request_state not in _DELETABLE_STATES
    ):
        raise ConflictError(NO_BRANCH_TO_DELETE, code=NO_BRANCH_CODE)
    await fix_queries.queue_suggestion(
        db,
        delete_fix_branch_request(fix_id=fix_id, org_id=org_id, requested_by=identity.user.id),
    )
    await db.commit()
    return DeleteBranchStarted(fix_id=fix_id)


async def request_ci(
    db: AsyncSession,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    go: WriteGo | None,
    now: datetime,
) -> CiRequested:
    """FR25, AC28: one request, stored before its job is added. A second click while one
    runs replaces the request; the job serves whichever the row holds, once."""
    _refuse_while_off(go)
    org_id, user_id = identity.org_id, identity.user.id
    row = await _source_row(db, org_id=org_id, finding_id=finding_id, fix_id=fix_id)
    requested = await db.execute(
        update(FindingFix)
        .where(
            FindingFix.org_id == org_id,
            FindingFix.id == fix_id,
            FindingFix.state == OPENED,
            FindingFix.pull_request_state == PULL_REQUEST_OPEN,
        )
        .values(ci_requested_by=user_id, ci_requested_at=now, last_write_failure=None)
        .returning(FindingFix.id)
        .execution_options(synchronize_session=False)
    )
    if requested.first() is None:
        raise ConflictError(CI_FAILURE_SENTENCES[PULL_REQUEST_NOT_OPEN], code=PULL_REQUEST_NOT_OPEN)
    audit.record_fix_event(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        action=FIX_CI_REQUESTED,
        target_kind=audit.FINDING_TARGET,
        target_id=finding_id,
        detail={"fix_id": str(fix_id), "repository_id": str(row.connection_repository_id)},
    )
    await fix_queries.queue_suggestion(db, run_fix_ci_request(fix_id=fix_id, org_id=org_id))
    await db.commit()
    return CiRequested(fix_id=fix_id)
