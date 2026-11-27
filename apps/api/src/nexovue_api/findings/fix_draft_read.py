"""The draft pull request, read in a person's shape (spec FR10c, FR22, FR24, FR26, FR27;
design, *State by role*, `approved`, `opened`, *Draft button states*; plan T7.8).

A read calls no GitHub and moves no state. **While write access is off in code**
(`write_gate.py`, ADR-097), a written suggestion's draft action is hidden with the
`writes_off` sentence in its place, and nothing else of the draft is offered. With the go:

- **Open draft pull request** shows, or is hidden with the first refusal's sentence in its
  place (FR10c: `drafts_not_allowed`, `no_write_permission`, `no_draft_support`,
  `needs_likely_cause`, `public_not_allowed` by the stored visibility; the check run reads
  GitHub's live answer). `not_linked` leaves it enabled: the confirm opens on the link step.
- this person's own `draft` check: working, refused with its sentence, or passed, when the
  confirm shows FR24's facts and warnings by exact string, with the digest the click
  carries, for 15 minutes;
- a last write failure, and a draft not opened, with the leftover branch named.

`approved` reads "Opening the draft", then, past 5 minutes, that it did not finish; `opened`
reads the pull request's state, its link, and FR26's line on what holding CI means.

Step 8 (FR25, FR26, FR30): an open draft offers **Run CI** with FR25's confirmation, while
write access is on and no request is waiting; a request reads as asked for, then as done (the
held line goes, since it is no longer true), or as its failure's sentence. After a merge the
requester reads the facts line's times: merged, a deploy F28 recorded with the merge's
commit after it, and the finding's close, with no causal word.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from nexovue_platform.fixes.draft import ci_done, draft_refusal, public_refused
from nexovue_platform.fixes.records import (
    CHECK_ANSWERS_WITHIN,
    CHECK_FOR_DRAFT,
    CHECK_PASSED,
    CHECK_RUNNING,
    CI_REQUEST_STUCK_AFTER,
    DID_NOT_FINISH,
    DRAFT_CHECK_WITHIN,
    PUBLIC_NOT_ALLOWED,
    PULL_REQUEST_MERGED,
    PULL_REQUEST_NOT_OPENED,
    PULL_REQUEST_OPEN,
    WRITES_OFF,
    fix_branch,
)
from nexovue_platform.fixes.sentences import (
    CHECK_REASON_SENTENCES,
    CHECK_TIMED_OUT,
    CI_DID_NOT_FINISH,
    CI_FAILURE_SENTENCES,
    CI_HELD_ON_PAGE,
    CI_PUSHED,
    CI_REQUESTED,
    CONFIRM_AUTHOR,
    CONFIRM_CASE,
    CONFIRM_CI_ON_OPEN,
    CONFIRM_DRAFT,
    CONFIRM_FACTS,
    CONFIRM_HOLDS,
    CONFIRM_LOGIN,
    CONFIRM_REACH,
    DELETE_BRANCH,
    DRAFT_READY_HEADING,
    DRAFT_REFUSAL_SENTENCES,
    DRAFT_RUNNING_HEADING,
    LINK_SENTENCE,
    NOT_AVAILABLE_HEADING,
    NOTE_LABEL,
    NOTE_PUBLIC_CONFIRM,
    OPEN_DRAFT,
    OPENED_HEADING,
    OPENING_HEADING,
    OPENING_REQUESTER,
    OPENING_SLOW,
    OPENING_VIEWER,
    PATCH_RUNNING,
    PULL_REQUEST_SENTENCES,
    RUN_CI,
    RUN_CI_CONFIRM,
    RUN_CI_CONFIRM_ACTION,
    WRITE_FAILURE_SENTENCES,
)
from nexovue_platform.fixes.write_gate import WriteGo, write_access_on
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    Connection,
    ConnectionRepository,
    DeployEvent,
    FindingFix,
    FindingFixCheck,
    FixRepositorySettings,
    GitHubUserLink,
)
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.findings.fix_source_schemas import (
    DraftConfirm,
    DraftView,
    MergedFacts,
    PatchView,
)

# The design's guess: past this, "Opening the draft" says it did not finish.
OPENING_SLOW_AFTER: Final = timedelta(minutes=5)
# A branch may be deleted once its pull request is closed, or was never opened (FR23).
_DELETABLE: Final = frozenset({"closed", PULL_REQUEST_NOT_OPENED})


@dataclass(frozen=True, slots=True)
class DraftContext:
    """What the offer needs beyond the row: the gate as the route passes it, whether the
    likely cause is this service's own signed deploy (D58), and the repository's key."""

    go: WriteGo | None
    signed_deploy_cause: bool
    repository_key: str | None


@dataclass(frozen=True, slots=True)
class _Repository:
    private: bool | None
    public_allowed: bool
    drafts_allowed: bool
    ci_on_open: bool
    draft_refused_at: datetime | None
    granted: dict[str, str] | None


async def offer(
    db: AsyncSession,
    row: FindingFix,
    *,
    user_id: uuid.UUID,
    context: DraftContext,
    file_count: int,
    line_count: int,
    now: datetime,
) -> DraftView:
    """A written suggestion's draft action, for a requester who may read its code."""
    if not write_access_on(context.go):
        return DraftView(refusal=DRAFT_REFUSAL_SENTENCES[WRITES_OFF])
    failure = _failure(row)
    repository = await _repository(db, row)
    refused = draft_refusal(
        drafts_allowed=repository.drafts_allowed,
        granted=repository.granted,
        draft_refused_at=repository.draft_refused_at,
        signed_deploy_cause=context.signed_deploy_cause,
        now=now,
    )
    if refused is None and public_refused(
        private=repository.private is not False, public_allowed=repository.public_allowed
    ):
        refused = PUBLIC_NOT_ALLOWED
    if refused is not None:
        return DraftView(refusal=DRAFT_REFUSAL_SENTENCES[refused], failure=failure)
    linked = await db.scalar(
        select(GitHubUserLink.user_id).where(
            GitHubUserLink.org_id == row.org_id, GitHubUserLink.user_id == user_id
        )
    )
    check: FindingFixCheck | None = await db.scalar(
        select(FindingFixCheck).where(
            FindingFixCheck.org_id == row.org_id,
            FindingFixCheck.fix_id == row.id,
            FindingFixCheck.requested_by == user_id,
            FindingFixCheck.purpose == CHECK_FOR_DRAFT,
        )
    )
    shown = None if check is None else _check_view(check, now=now)
    running = shown is not None and shown.status == CHECK_RUNNING
    confirm = (
        None
        if check is None
        else _confirm(
            row,
            check,
            repository,
            context=context,
            file_count=file_count,
            line_count=line_count,
            now=now,
        )
    )
    return DraftView(
        action=None if running or confirm is not None else OPEN_DRAFT,
        needs_link=linked is None,
        link_sentence=LINK_SENTENCE if linked is None else None,
        check=shown,
        confirm=confirm,
        failure=failure,
        pull_request_state=row.pull_request_state,
        pull_request_sentence=_pull_request_sentence(row, may_request=True),
        delete_branch_action=_delete_action(row),
    )


def opening(row: FindingFix, *, may_request: bool, now: datetime) -> tuple[str, str]:
    """`approved`: "Opening the draft", then, past 5 minutes, that it may not finish."""
    if not may_request:
        return OPENING_HEADING, OPENING_VIEWER
    if row.state_changed_at + OPENING_SLOW_AFTER <= now:
        return OPENING_HEADING, OPENING_SLOW.format(branch=fix_branch(row.id))
    return OPENING_HEADING, OPENING_REQUESTER


async def opened(
    db: AsyncSession, row: FindingFix, *, may_request: bool, go: WriteGo | None, now: datetime
) -> tuple[str, str | None, DraftView]:
    """`opened`: the pull request's state and link; FR26's line while CI is held; for a
    requester, Run CI and what it did (FR25), and after a merge the facts line (FR30)."""
    sentence = _pull_request_sentence(row, may_request=may_request)
    repository = await _repository(db, row)
    is_open = row.pull_request_state == PULL_REQUEST_OPEN
    held = is_open and not repository.ci_on_open and not _ci_done(row)
    if not may_request:
        view = DraftView(
            pull_request_state=row.pull_request_state,
            pull_request_url=row.pull_request_url,
            pull_request_sentence=sentence,
            ci_note=CI_HELD_ON_PAGE if held else None,
        )
        # The draft part says it, with its link; the section's own line would repeat it.
        return OPENED_HEADING, None, view
    failure = _ci_failure(row)
    requested_at = row.ci_requested_at
    unanswered = requested_at is not None and failure is None and not _ci_done(row)
    lapsed = (
        unanswered and requested_at is not None and requested_at + CI_REQUEST_STUCK_AFTER <= now
    )
    waiting = unanswered and not lapsed
    offered = is_open and not waiting and write_access_on(go)
    view = DraftView(
        pull_request_state=row.pull_request_state,
        pull_request_url=row.pull_request_url,
        pull_request_sentence=sentence,
        ci_note=CI_HELD_ON_PAGE if held else None,
        delete_branch_action=_delete_action(row),
        failure=failure,
        ci_action=RUN_CI if offered else None,
        ci_confirm=RUN_CI_CONFIRM if offered else None,
        ci_confirm_action=RUN_CI_CONFIRM_ACTION if offered else None,
        ci_status=_ci_status(row, waiting=waiting, lapsed=lapsed),
        merged_facts=await _merged_facts(db, row),
    )
    return OPENED_HEADING, None, view


def _ci_done(row: FindingFix) -> bool:
    return ci_done(
        requested_at=row.ci_requested_at,
        commit_requested_at=row.ci_commit_requested_at,
        pushed_at=row.ci_pushed_at,
    )


def _ci_status(row: FindingFix, *, waiting: bool, lapsed: bool) -> str | None:
    if _ci_done(row):
        return CI_PUSHED
    if waiting:
        return CI_REQUESTED
    return CI_DID_NOT_FINISH if lapsed else None


def _ci_failure(row: FindingFix) -> str | None:
    code = row.last_write_failure
    return None if code is None else CI_FAILURE_SENTENCES.get(code)


async def _merged_facts(db: AsyncSession, row: FindingFix) -> MergedFacts | None:
    """FR30: GitHub's merge time; the first deploy F28 recorded with the merge's commit at
    or after it, in this project; the finding's close. Stored facts only, no GitHub call."""
    if row.pull_request_state != PULL_REQUEST_MERGED or row.pull_request_state_at is None:
        return None
    merged_at = row.pull_request_state_at
    deployed_at = None
    if row.pull_request_merge_commit_sha is not None:
        deployed_at = await db.scalar(
            select(func.min(DeployEvent.deployed_at)).where(
                DeployEvent.org_id == row.org_id,
                DeployEvent.project_id == row.project_id,
                DeployEvent.commit_sha == row.pull_request_merge_commit_sha,
                DeployEvent.deployed_at >= merged_at,
            )
        )
    closed_at = await db.scalar(
        select(FindingRow.closed_at).where(
            FindingRow.org_id == row.org_id, FindingRow.id == row.finding_id
        )
    )
    return MergedFacts(merged_at=merged_at, deployed_at=deployed_at, finding_closed_at=closed_at)


def _pull_request_sentence(row: FindingFix, *, may_request: bool) -> str | None:
    if row.pull_request_state is None:
        return None
    words = PULL_REQUEST_SENTENCES.get(row.pull_request_state)
    if words is None:
        return None
    chosen = words.requester if may_request else words.viewer
    if chosen is None:
        return None
    return chosen.format(link=row.pull_request_url or "GitHub", branch=fix_branch(row.id))


def _delete_action(row: FindingFix) -> str | None:
    if row.pull_request_branch is None or row.pull_request_state not in _DELETABLE:
        return None
    return DELETE_BRANCH


def _failure(row: FindingFix) -> str | None:
    """Why the last draft was not opened, naming the branch where one may be left."""
    code = row.last_write_failure
    if code is None:
        return None
    sentence = WRITE_FAILURE_SENTENCES.get(code)
    if sentence is None:
        return None
    return sentence.format(branch=fix_branch(row.id)) if code == DID_NOT_FINISH else sentence


async def _repository(db: AsyncSession, row: FindingFix) -> _Repository:
    found = (
        await db.execute(
            select(ConnectionRepository, Connection.granted_permissions, FixRepositorySettings)
            .join(
                Connection,
                (Connection.org_id == ConnectionRepository.org_id)
                & (Connection.id == ConnectionRepository.connection_id),
            )
            .outerjoin(
                FixRepositorySettings,
                (FixRepositorySettings.org_id == ConnectionRepository.org_id)
                & (FixRepositorySettings.connection_repository_id == ConnectionRepository.id),
            )
            .where(
                ConnectionRepository.org_id == row.org_id,
                ConnectionRepository.id == row.connection_repository_id,
            )
        )
    ).one_or_none()
    if found is None:
        return _Repository(None, False, False, False, None, None)
    place, granted, settings = found
    return _Repository(
        private=place.is_private,
        public_allowed=place.public_allowed_at is not None,
        drafts_allowed=settings is not None and settings.drafts_allowed,
        ci_on_open=settings is not None and settings.ci_on_open,
        draft_refused_at=None if settings is None else settings.draft_refused_at,
        granted=granted,
    )


def _check_view(check: FindingFixCheck, *, now: datetime) -> PatchView:
    """This person's own `draft` check in words (design, *State by role*, check rows)."""
    if check.status == CHECK_RUNNING:
        late = check.requested_at + CHECK_ANSWERS_WITHIN <= now
        return PatchView(
            status="timed_out" if late else CHECK_RUNNING,
            heading=DRAFT_RUNNING_HEADING,
            sentence=CHECK_TIMED_OUT if late else PATCH_RUNNING,
            downloadable=False,
            expires_at=None,
        )
    if check.status in CHECK_PASSED:
        return PatchView(
            status=check.status,
            heading=DRAFT_READY_HEADING,
            sentence=None,
            downloadable=False,
            expires_at=check.expires_at,
        )
    return PatchView(
        status=check.status,
        heading=NOT_AVAILABLE_HEADING,
        sentence=None if check.reason is None else CHECK_REASON_SENTENCES.get(check.reason),
        downloadable=False,
        expires_at=None,
    )


def _confirm(
    row: FindingFix,
    check: FindingFixCheck,
    repository: _Repository,
    *,
    context: DraftContext,
    file_count: int,
    line_count: int,
    now: datetime,
) -> DraftConfirm | None:
    """FR24's confirm, from this person's own passed check, for its 15 minutes."""
    if (
        check.status not in CHECK_PASSED
        or check.finished_at is None
        or check.digest is None
        or check.finished_at + DRAFT_CHECK_WITHIN <= now
    ):
        return None
    branch = fix_branch(row.id)
    key = context.repository_key or "—"
    public = repository.private is False
    warnings = [CONFIRM_CASE, CONFIRM_AUTHOR]
    if not public:
        warnings.append(CONFIRM_LOGIN)
    warnings.append(CONFIRM_REACH)
    return DraftConfirm(
        repository=key,
        branch=branch,
        file_count=file_count,
        line_count=line_count,
        facts=CONFIRM_FACTS.format(
            repository=key,
            branch=branch,
            files=file_count,
            files_word="file" if file_count == 1 else "files",
            lines=line_count,
            lines_word="line" if line_count == 1 else "lines",
        ),
        ci_mode=CONFIRM_CI_ON_OPEN if repository.ci_on_open else CONFIRM_HOLDS,
        warnings=warnings,
        digest=check.digest,
        public=public,
        note_label=NOTE_LABEL,
        note_public_confirm=NOTE_PUBLIC_CONFIRM if public else None,
        action=CONFIRM_DRAFT,
        expires_at=check.finished_at + DRAFT_CHECK_WITHIN,
    )
