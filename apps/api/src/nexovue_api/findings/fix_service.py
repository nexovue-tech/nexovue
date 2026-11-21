"""Rung 3 on a finding: Ask, the file list, removals and typed paths, Send and Cancel, and
the brief's paths (spec FR5c, FR9, FR11 to FR13a; design, *The flow*, steps 1, 2 and 4;
plan T3.9, T3.11, T3.16, T4.14). **Nothing goes to a provider from here:** Send moves the
row to `proposing` and adds the AI role's job, with ids only.

- **Ask** decides FR9 again under the finding's lock, reads this service's own stacks
  (ClickHouse, through the tenant chokepoint) or takes typed paths, and in one transaction
  inserts the row in `gathering`, audits `fix.requested`, and enqueues the pack job with
  ids only (AC15). A group no longer in the window, or no stack for this service, stores a
  `refused` row with its reason (FR11a).
- **Files** turns the list on screen into candidates again (kept files, typed paths, and
  removals), and runs the pack job afresh, so only `scheduler-connections` ever writes
  `files_read`.
- **Send** is bound to the list by its digest: a list that changed answers 409 with the
  new list (AC3b). With the top file left out, it needs the person's confirmation (AC12f).
  It checks AI is on, consent covers the current code location, and the limit; then in
  one transaction `proposing`, the hint on the pack, `fix.sent`, and the job (T4.14).
- **Cancel** is a person's choice, shown neutrally (AC46).

Who sees what: a requester (`REQUEST_FIX`) reads the list; a viewer and a support view read
a state and its sentence, never a path (AC10, AC11). Every statement binds `org_id`, and a
suggestion must belong to the path's finding in the same statement.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Final, cast, get_args
from urllib.parse import quote

from nexovue_platform.fixes.prose import prose_refusal
from nexovue_platform.fixes.records import (
    BASIS_LIKELY_CAUSE,
    BASIS_NAMED_FILES,
    BASIS_STACK_ONLY,
    BRIEF_PATHS_RESULT_KIND,
    BRIEF_PATHS_TTL_SECONDS,
    CANCELLED,
    FAILED,
    FIX_CANCELLED,
    FIX_REQUESTED,
    FIX_SENT,
    FROM_MODEL,
    FROM_PERSON,
    FROM_STACK,
    GATHERED,
    GATHERED_LAPSES_AFTER,
    GATHERING,
    KIND_SOURCE,
    MAX_TYPED_PATHS,
    PROPOSED,
    PROPOSING,
    REFUSED,
    TRIES_PER_FINDING,
    build_source_pack_request,
    match_brief_paths_request,
    propose_fix_request,
    proposing_lifetime,
)
from nexovue_platform.fixes.sentences import (
    ASKED_FOR_MORE,
    ASKED_ROUND,
    BEING_PREPARED_VIEWER,
    COMMIT_UNKNOWN,
    COULD_NOT_FINISH_HEADING,
    ELIGIBILITY_SENTENCES,
    FILE_WHY,
    GATHERED_FORGEABLE,
    GATHERED_HEADING,
    GATHERED_SEND,
    GATHERED_SEND_HOST,
    GATHERING_HEADING,
    GATHERING_REQUESTER,
    LEFT_OUT_WHY,
    LINK_SENTENCE,
    MODEL_WORDS_LABEL,
    NAME_FILES_LEAD,
    NEUTRAL_REASONS,
    NO_SUGGESTION_HEADING,
    PROPOSING_HEADING,
    PROPOSING_REQUESTER,
    REASON_SENTENCES,
    SECTION_HEADING_UNAVAILABLE,
    SOURCE_ACTION,
    SOURCE_READY_LABELS,
    SUPPORT_VIEW_NO_CODE,
    TOP_FILE_LEFT_OUT,
    TYPED_MISS,
    TYPED_REFUSED,
    WHOLE_REPOSITORY,
)
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import (
    AIConnection,
    ConnectionRepository,
    DeployEvent,
    FindingFix,
    FindingFixSourcePack,
    GitHubUserLink,
    JobResult,
)
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.findings import fix_access, fix_paths, fix_proposal, fix_queries, fix_state
from nexovue_api.findings.fix_general import Viewer
from nexovue_api.findings.fix_schemas import FixSectionLine
from nexovue_api.findings.fix_source_schemas import (
    AskedFile,
    AskedRefusal,
    AskedRound,
    BriefFiles,
    BriefFilesStarted,
    BriefPath,
    EarlierSource,
    FindingSourceFix,
    LeftOutFile,
    ListedFile,
    SourceFiles,
    SourceFilesRequest,
    SourceFix,
    SourceFixRequest,
    SourceOffer,
    SourceSendRequest,
    SourceState,
    TypedMiss,
)
from nexovue_api.findings.fix_state import FixState
from nexovue_api.findings.queries import find_finding, lock_finding
from nexovue_api.settings import Settings

SOURCE_STATES: Final = frozenset(get_args(SourceState))

NOT_FOUND: Final = "No such finding."
NO_SUCH_FIX: Final = "No such suggestion for this finding."
NOT_OFFERED: Final = "A suggested fix cannot be asked for on this finding now."
LIST_CHANGED: Final = "The file list changed. Check the new list, then send again."
NOT_GATHERED: Final = "This suggestion is not waiting for its file list to be sent."
NOT_IN_FLIGHT: Final = "This suggestion is no longer being prepared."
NEEDS_PATHS: Final = "Name up to 5 files, as paths inside the folder."
CONFIRM_TOP_FILE_CODE: Final = "confirm_top_left_out"
LIST_CHANGED_CODE: Final = "list_changed"
ALREADY_RUNNING_CODE: Final = "already_running"
NOT_LINKED_CODE: Final = "not_linked"
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")
_SHA40: Final = re.compile(r"^[0-9a-f]{40}$")


# --------------------------------------------------------------------- the read


async def read_source(
    db: AsyncSession,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    settings: Settings,
    now: datetime,
) -> FindingSourceFix:
    """`GET /findings/{id}/fixes/source`. Postgres only: no GitHub, no ClickHouse."""
    finding = await find_finding(db, org_id=identity.org_id, finding_id=finding_id)
    if finding is None:
        raise NotFoundError(NOT_FOUND)
    state = await fix_state.gather(db, finding, settings=settings, now=now)
    viewer = Viewer.of(identity)
    rows = await fix_queries.recent_fixes(
        db, org_id=identity.org_id, finding_id=finding_id, kind=KIND_SOURCE
    )
    latest = rows[0] if rows else None
    return FindingSourceFix(
        finding_id=finding.id,
        line=_line(state, viewer),
        offer=await _offer(db, state, viewer, identity),
        latest=None
        if latest is None
        else await _view(db, latest, viewer, state, user_id=identity.user.id),
        earlier=[
            EarlierSource(id=row.id, state=_state_of(row), requested_at=row.requested_at)
            for row in rows[1:]
        ],
        support_view_note=None if identity.impersonation is None else SUPPORT_VIEW_NO_CODE,
    )


def _line(state: FixState, viewer: Viewer) -> FixSectionLine | None:
    """Rung 3's own line, only where rung 2b's read says nothing: `cause_is_elsewhere`
    with files to name is said there; here, a reason that stops a ready rung 3."""
    reason = state.source.reason
    if reason is None or reason not in ("limit_reached", "already_running"):
        return None
    sentence = viewer.sentence(ELIGIBILITY_SENTENCES[reason])
    if sentence is None or reason == "already_running":
        return None
    return FixSectionLine(
        heading=SECTION_HEADING_UNAVAILABLE,
        sentence=sentence.format(date=state.now.date().isoformat(), dependency="", host=""),
    )


async def _offer(
    db: AsyncSession, state: FixState, viewer: Viewer, identity: Identity
) -> SourceOffer | None:
    result = state.source.result
    if result is None or not viewer.may_request:
        return None
    folder = state.access.folder
    needs_link = await _needs_link(db, state, identity)
    basis = _basis(state, result)
    return SourceOffer(
        action=SOURCE_ACTION,
        result=result,
        label=SOURCE_READY_LABELS[basis],
        name_files_lead=None
        if result == "ready"
        else NAME_FILES_LEAD.format(folder=folder or WHOLE_REPOSITORY),
        folder=folder,
        tries_left=max(0, TRIES_PER_FINDING - len(state.tries_used)),
        needs_link=needs_link,
        link_sentence=LINK_SENTENCE if needs_link else None,
    )


async def _needs_link(db: AsyncSession, state: FixState, identity: Identity) -> bool:
    """D20: the owner requires a linked GitHub account, and this person has none."""
    consent = state.access.consent
    if consent is None or not consent.require_github_access:
        return False
    linked = await db.scalar(
        select(GitHubUserLink.user_id).where(
            GitHubUserLink.org_id == identity.org_id, GitHubUserLink.user_id == identity.user.id
        )
    )
    return linked is None


def _basis(state: FixState, result: str) -> str:
    if result == "ready_without_stack":
        return BASIS_NAMED_FILES
    return BASIS_LIKELY_CAUSE if state.released_rule is not None else BASIS_STACK_ONLY


def _state_of(row: FindingFix) -> SourceState:
    """A state outside rung 3's list reads as `failed` (A48's rule for stored words)."""
    return cast(SourceState, row.state) if row.state in SOURCE_STATES else FAILED


async def _view(
    db: AsyncSession, row: FindingFix, viewer: Viewer, state: FixState, *, user_id: uuid.UUID
) -> SourceFix:
    reason = row.reason
    neutral = reason in NEUTRAL_REASONS
    files = None
    sentence: str | None
    shown: fix_proposal.ProposalRead | None = None
    if row.state == GATHERING:
        heading = GATHERING_HEADING
        sentence = GATHERING_REQUESTER if viewer.may_request else BEING_PREPARED_VIEWER
    elif row.state == PROPOSING:
        heading = PROPOSING_HEADING
        sentence = PROPOSING_REQUESTER if viewer.may_request else BEING_PREPARED_VIEWER
    elif row.state == GATHERED:
        heading = GATHERED_HEADING if viewer.may_request else GATHERING_HEADING
        sentence = None if viewer.may_request else BEING_PREPARED_VIEWER
        files = await _files(db, row, state) if viewer.may_request else None
    elif row.state == PROPOSED:
        key = await _repository_key(db, row)
        shown = await fix_proposal.read_proposed(
            db,
            row,
            user_id=user_id,
            may_request=viewer.may_request,
            repository_key=key,
            files_read=_listing(row, key) if viewer.may_request else ([], []),
            now=state.now,
        )
        heading, sentence = shown.heading, shown.sentence
    else:
        heading = (
            NO_SUGGESTION_HEADING if neutral or row.state == REFUSED else COULD_NOT_FINISH_HEADING
        )
        sentence = None if reason is None else viewer.sentence(REASON_SENTENCES[reason])
        if sentence is not None:
            sentence = sentence.format(
                folder=row.folder or WHOLE_REPOSITORY,
                repository=await _repository_key(db, row) or "",
            )
    return SourceFix(
        id=row.id,
        state=_state_of(row),
        heading=heading,
        sentence=sentence,
        reason=reason,
        neutral=neutral,
        files=files,
        requested_at=row.requested_at,
        state_changed_at=row.state_changed_at,
        proposal=None if shown is None else shown.proposal,
        code_withheld=None if shown is None else shown.code_withheld,
        check_action=None if shown is None else shown.check_action,
        check=None if shown is None else shown.check,
        file_count=None if shown is None else shown.file_count,
        line_count=None if shown is None else shown.line_count,
    )


async def _repository_key(db: AsyncSession, row: FindingFix) -> str | None:
    if row.connection_repository_id is None:
        return None
    key: str | None = await db.scalar(
        select(ConnectionRepository.key).where(
            ConnectionRepository.org_id == row.org_id,
            ConnectionRepository.id == row.connection_repository_id,
        )
    )
    return key


def _listing(row: FindingFix, key: str | None) -> tuple[list[ListedFile], list[LeftOutFile]]:
    """The files read and those left out, from the listing only `scheduler-connections`
    writes, each linked to GitHub at the commit read (FR13a)."""
    listing = row.files_read or {}
    commit = str(listing.get("commit") or "")
    entries = [each for each in listing.get("files") or [] if isinstance(each, dict)]
    paths = {str(each.get("ref")): str(each.get("path")) for each in entries}
    files = [
        ListedFile(
            ref=str(each.get("ref")),
            path=str(each.get("path")),
            role=str(each.get("role")),
            why=_why(each.get("why"), paths),
            bytes=int(each.get("bytes") or 0),
            lines_sent=each.get("lines_sent"),
            moved_on_default=bool(each.get("moved_on_default")),
            github_url=_github_url(key, commit, str(each.get("path"))),
        )
        for each in entries
    ]
    left_out = [
        LeftOutFile(
            path=str(each.get("path")),
            why=LEFT_OUT_WHY.get(str(each.get("reason")), LEFT_OUT_WHY["not_source"]),
            bytes=each.get("bytes"),
        )
        for each in listing.get("left_out") or []
        if isinstance(each, dict)
    ]
    return files, left_out


async def _files(db: AsyncSession, row: FindingFix, state: FixState) -> SourceFiles | None:
    listing = row.files_read or {}
    key = await _repository_key(db, row)
    commit = str(listing.get("commit") or "")
    folder = row.folder or WHOLE_REPOSITORY
    host = (
        "the AI provider"
        if state.connection is None
        else fix_access.host_of(state.connection.base_url)
    )
    files, left_out = _listing(row, key)
    return SourceFiles(
        asked_round=_asked_round(row, files, listing),
        files=files,
        left_out=left_out,
        unmatched=int(listing.get("unmatched") or 0) + int(listing.get("ambiguous") or 0),
        typed_missing=[
            TypedMiss(path=str(path), sentence=TYPED_MISS.format(path=path, folder=folder))
            for path in listing.get("typed_missing") or []
        ],
        commit=commit,
        commit_known=bool(listing.get("commit_known")),
        commit_sentence=None if listing.get("commit_known") else COMMIT_UNKNOWN,
        top_left_out=bool(listing.get("top_left_out")),
        digest=row.files_digest or "",
        bytes_sent=sum(file.bytes for file in files),
        provider_host=host,
        round=row.round,
        lapses_at=row.state_changed_at + GATHERED_LAPSES_AFTER,
        forgeable=GATHERED_FORGEABLE,
        send_host=GATHERED_SEND_HOST.format(host=host),
        send=GATHERED_SEND,
    )


def _why(why: Any, paths: dict[str, str]) -> str:
    """A file's reason in Nexovue's words, from stored codes, numbers and the listing's own
    paths only: an asked-for file names the listed file and line that asked (FR15b)."""
    if not isinstance(why, dict):
        return FILE_WHY["named"]
    kind, line, path = why.get("kind"), why.get("line"), why.get("path")
    if kind == "stack":
        return (
            FILE_WHY["stack"].format(line=line)
            if isinstance(line, int)
            else FILE_WHY["stack_no_line"]
        )
    if kind in ("imported_by", "test_for") and isinstance(path, str):
        return FILE_WHY[kind].format(path=path)
    if kind == "asked":
        because = paths.get(str(why.get("ref")))
        if because is not None and isinstance(line, int):
            return FILE_WHY["asked"].format(path=because, line=line)
        return FILE_WHY["asked_no_line"]
    return FILE_WHY["named"]


def _asked_round(
    row: FindingFix, files: list[ListedFile], listing: dict[str, Any]
) -> AskedRound | None:
    """Round 2 or 3, after the model asked (FR15b, AC88's screen half): the files it
    asked for, each with the listed line that asked and its words under their label,
    checked again here (D18); names refused with why; names matching nothing, a count."""
    if row.candidates_from != FROM_MODEL or row.round < 2:
        return None
    entries = {
        str(each.get("path")): each for each in listing.get("files") or [] if isinstance(each, dict)
    }
    asked = [
        AskedFile(
            path=file.path,
            because=file.why,
            model_words=_model_words((entries.get(file.path) or {}).get("why")),
        )
        for file in files
        if file.role == "asked"
    ]
    return AskedRound(
        lead=ASKED_FOR_MORE.format(count=len(asked)),
        round_sentence=ASKED_ROUND.format(round=row.round),
        model_words_label=MODEL_WORDS_LABEL,
        asked=asked,
        refused=[
            AskedRefusal(
                path=str(each.get("path")),
                why=LEFT_OUT_WHY.get(str(each.get("reason")), LEFT_OUT_WHY["not_source"]),
            )
            for each in listing.get("left_out") or []
            if isinstance(each, dict) and each.get("asked")
        ],
        unmatched=int(listing.get("unmatched") or 0),
    )


def _model_words(why: Any) -> str | None:
    """The model's reason for asking, shown only if it passes FR16's prose checks again."""
    reason = why.get("reason") if isinstance(why, dict) else None
    if not isinstance(reason, str) or prose_refusal(reason, None) is not None:
        return None
    return reason


def _github_url(key: str | None, commit: str, path: str) -> str | None:
    if key is None or not _SHA40.match(commit):
        return None
    return f"https://github.com/{key}/blob/{commit}/{quote(path, safe='/')}"


# --------------------------------------------------------------------- the click


def typed_paths(paths: list[str]) -> list[str]:
    """A person's paths (D4): relative to the folder, no `..`, no control character. A
    refused path is named, so the screen keeps the row and says why (AC46)."""
    kept: list[str] = []
    for raw in paths:
        path = raw.strip()
        segments = path.split("/")
        if (
            not path
            or path.startswith("/")
            or _CONTROL.search(path)
            or len(path) > 200
            or any(segment in ("", ".", "..") for segment in segments)
        ):
            raise InvalidRequestError(TYPED_REFUSED.format(path=raw[:200]), reason="typed_path")
        if path not in kept:
            kept.append(path)
    return kept[:MAX_TYPED_PATHS]


async def ask(
    db: AsyncSession,
    repository: TenantScopedRepository,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    request: SourceFixRequest,
    settings: Settings,
    now: datetime,
) -> FindingSourceFix:
    """`POST /findings/{id}/fixes` (design, *The flow*, steps 1 and 2)."""
    org_id = identity.org_id
    finding = await lock_finding(db, org_id=org_id, finding_id=finding_id)
    if finding is None:
        raise NotFoundError(NOT_FOUND)
    state = await fix_state.gather(db, finding, settings=settings, now=now)
    result = state.source.result
    if result is None or state.access.location is None or state.access.repository is None:
        raise ConflictError(NOT_OFFERED, code=state.source.reason or "not_offered")
    if await _needs_link(db, state, identity):
        raise ConflictError(LINK_SENTENCE, code=NOT_LINKED_CODE)
    paths = typed_paths(request.paths)
    if result == "ready_without_stack" and not paths:
        raise InvalidRequestError(NEEDS_PATHS, reason="typed_path")
    row = FindingFix(
        id=uuid.uuid4(),
        org_id=org_id,
        project_id=finding.project_id,
        finding_id=finding.id,
        kind=KIND_SOURCE,
        explanation_version=None if state.explanation is None else state.explanation.version,
        state=GATHERING,
        state_changed_at=now,
        connection_repository_id=state.access.repository.id,
        folder=state.access.location.folder,
        basis=BASIS_NAMED_FILES if paths else _basis(state, result),
        candidates_from=FROM_PERSON if paths else FROM_STACK,
        read_commit=await _running_commit(db, finding),
        requested_by=identity.user.id,
        requested_at=now,
        notify_requester=request.notify,
    )
    if paths:
        row.path_candidates = fix_paths.typed_candidates(paths)
    else:
        found = await fix_paths.stack_candidates(repository, finding, state.explanation)
        if isinstance(found, str):
            row.state, row.reason, row.finished_at = REFUSED, found, now
        else:
            row.path_candidates = [candidate.stored() for candidate in found]
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(NOT_OFFERED, code=ALREADY_RUNNING_CODE) from None
    audit.record_fix_event(
        db,
        org_id=org_id,
        actor_user_id=identity.user.id,
        action=FIX_REQUESTED,
        target_kind=audit.FINDING_TARGET,
        target_id=finding.id,
        detail={
            "fix_id": str(row.id),
            "basis": row.basis,
            "repository_id": str(row.connection_repository_id),
            "named_by_person": bool(paths),
        },
    )
    if row.state == GATHERING:
        await fix_queries.queue_suggestion(
            db, build_source_pack_request(fix_id=row.id, org_id=org_id)
        )
    await db.commit()
    return await read_source(
        db, identity=identity, finding_id=finding_id, settings=settings, now=now
    )


async def _running_commit(db: AsyncSession, finding: Any) -> str | None:
    """F28's commit for the service's last deploy before the finding, if it names one.
    The pack job reads it only once GitHub shows it is an ancestor (A17, FR14)."""
    if finding.service_name is None:
        return None
    commit: str | None = await db.scalar(
        select(DeployEvent.commit_sha)
        .where(
            DeployEvent.org_id == finding.org_id,
            DeployEvent.project_id == finding.project_id,
            DeployEvent.service_name == finding.service_name,
            DeployEvent.deployed_at <= finding.started_at,
            DeployEvent.commit_sha.is_not(None),
        )
        .order_by(DeployEvent.deployed_at.desc())
        .limit(1)
    )
    return commit if commit is not None and _SHA40.match(commit) else None


async def _locked_fix(
    db: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID, fix_id: uuid.UUID
) -> FindingFix:
    row: FindingFix | None = await db.scalar(
        select(FindingFix)
        .where(
            FindingFix.org_id == org_id,
            FindingFix.finding_id == finding_id,
            FindingFix.id == fix_id,
            FindingFix.kind == KIND_SOURCE,
        )
        .with_for_update()
    )
    if row is None:
        raise NotFoundError(NO_SUCH_FIX)
    return row


async def change_files(
    db: AsyncSession,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    request: SourceFilesRequest,
    settings: Settings,
    now: datetime,
) -> FindingSourceFix:
    """FR13a: back to `gathering`, the pack job choosing again from the list kept, the
    removals and the typed paths."""
    row = await _locked_fix(db, org_id=identity.org_id, finding_id=finding_id, fix_id=fix_id)
    if row.state != GATHERED:
        raise ConflictError(NOT_GATHERED, code="not_gathered")
    added = typed_paths(request.add)
    prefix = f"{row.folder}/" if row.folder else ""
    removed = {path.removeprefix(prefix) for path in request.remove}
    listed = [each for each in (row.files_read or {}).get("files") or [] if isinstance(each, dict)]
    candidates: list[dict[str, Any]] = [
        {
            "path": str(each["path"]).removeprefix(prefix),
            "line": None,
            "from": "kept",
            "role": each.get("role"),
            "why": each.get("why"),
        }
        for each in listed
        if str(each.get("path")).removeprefix(prefix) not in removed
    ]
    candidates += [{"path": path, "line": None, "from": "removed"} for path in sorted(removed)]
    candidates += fix_paths.typed_candidates(added)
    row.state, row.state_changed_at = GATHERING, now
    row.path_candidates = candidates
    row.candidates_from = FROM_PERSON if added else row.candidates_from
    await fix_queries.queue_suggestion(
        db, build_source_pack_request(fix_id=row.id, org_id=identity.org_id)
    )
    await db.commit()
    return await read_source(
        db, identity=identity, finding_id=finding_id, settings=settings, now=now
    )


async def send(
    db: AsyncSession,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    request: SourceSendRequest,
    settings: Settings,
    now: datetime,
) -> FindingSourceFix:
    """FR13a: the digest must match the list as it stands, else 409 with the new list
    (AC3b); then the files go to the AI role's job (design, step 4)."""
    row = await _locked_fix(db, org_id=identity.org_id, finding_id=finding_id, fix_id=fix_id)
    if row.state != GATHERED:
        raise ConflictError(NOT_GATHERED, code="not_gathered")
    if request.files_digest != row.files_digest:
        # Nothing was changed, so the new list is read in the same transaction: a rollback
        # would expire the caller's identity mid-request.
        fresh = await read_source(
            db, identity=identity, finding_id=finding_id, settings=settings, now=now
        )
        raise ConflictError(LIST_CHANGED, code=LIST_CHANGED_CODE, fix=fresh.model_dump(mode="json"))
    if (row.files_read or {}).get("top_left_out") and not request.confirm_top_left_out:
        raise ConflictError(TOP_FILE_LEFT_OUT, code=CONFIRM_TOP_FILE_CODE)
    finding = await find_finding(db, org_id=identity.org_id, finding_id=finding_id)
    assert finding is not None
    state = await fix_state.gather(db, finding, settings=settings, now=now)
    connection = state.connection
    if connection is None:
        raise ConflictError(REASON_SENTENCES["ai_off"].requester, code="ai_off")
    if state.access.consent is None or not fix_access_covers(state, row):
        raise ConflictError(
            REASON_SENTENCES["consent_ended_in_flight"].requester, code="consent_ended_in_flight"
        )
    # D32: a later round belongs to the same try, so only the first spends one (FR15c).
    out_of_tries = row.round == 1 and len(state.tries_used) >= TRIES_PER_FINDING
    if state.tokens_out or out_of_tries:
        raise ConflictError(
            ELIGIBILITY_SENTENCES["limit_reached"].requester.format(
                date=state.now.date().isoformat()
            ),
            code="limit_reached",
        )
    await _send_round(db, row, identity=identity, connection=connection, hint=request.hint, now=now)
    await db.commit()
    return await read_source(
        db, identity=identity, finding_id=finding_id, settings=settings, now=now
    )


async def _send_round(
    db: AsyncSession,
    row: FindingFix,
    *,
    identity: Identity,
    connection: AIConnection,
    hint: str | None,
    now: datetime,
) -> None:
    """FR13a, design step 4: `proposing`, the hint on the pack, the pack kept for the job,
    `fix.sent` naming exactly what goes and where, and the job, in one transaction. Each
    round's Send does this again (T4.14)."""
    row.state, row.state_changed_at = PROPOSING, now
    row.sent_by, row.sent_at = identity.user.id, row.sent_at or now
    row.retry_code = None
    lifetime = proposing_lifetime(connection.call_seconds)
    kept = await db.scalar(
        update(FindingFixSourcePack)
        .where(FindingFixSourcePack.org_id == row.org_id, FindingFixSourcePack.fix_id == row.id)
        .values(hint=(hint or "").strip() or None, expires_at=now + lifetime)
        .returning(FindingFixSourcePack.fix_id)
    )
    if kept is None:
        raise ConflictError(NOT_GATHERED, code="not_gathered")
    listed = [each for each in (row.files_read or {}).get("files") or [] if isinstance(each, dict)]
    audit.record_fix_event(
        db,
        org_id=row.org_id,
        actor_user_id=identity.user.id,
        action=FIX_SENT,
        target_kind=audit.FINDING_TARGET,
        target_id=row.finding_id,
        detail={
            "fix_id": str(row.id),
            "round": row.round,
            "provider_host": fix_access.host_of(connection.base_url),
            "files": [
                {"path": str(each.get("path")), "blob_sha": str(each.get("blob_sha"))}
                for each in listed
            ],
            "hint": bool((hint or "").strip()),
        },
    )
    await fix_queries.queue_suggestion(
        db,
        propose_fix_request(
            fix_id=row.id,
            org_id=row.org_id,
            sent_by=identity.user.id,
            call_seconds=connection.call_seconds,
        ),
    )


def fix_access_covers(state: FixState, row: FindingFix) -> bool:
    """FR8 at Send: the live consent covers the current code location, which still names
    the row's repository and folder (a remap makes the row out of date, not sendable)."""
    location = state.access.location
    return (
        location is not None
        and location.connection_repository_id == row.connection_repository_id
        and location.folder == row.folder
    )


async def cancel(
    db: AsyncSession,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    settings: Settings,
    now: datetime,
) -> FindingSourceFix:
    """A person's choice (AC46): `failed`, `cancelled`, shown neutrally; the candidates and
    the pack go in the same transaction."""
    org_id = identity.org_id
    row = await _locked_fix(db, org_id=org_id, finding_id=finding_id, fix_id=fix_id)
    if row.state not in (GATHERING, GATHERED):
        raise ConflictError(NOT_IN_FLIGHT, code="not_in_flight")
    row.state, row.reason = FAILED, CANCELLED
    row.state_changed_at = row.finished_at = now
    row.path_candidates = row.requested_paths = None
    await db.execute(
        delete(FindingFixSourcePack).where(
            FindingFixSourcePack.org_id == org_id, FindingFixSourcePack.fix_id == row.id
        )
    )
    audit.record_fix_event(
        db,
        org_id=org_id,
        actor_user_id=identity.user.id,
        action=FIX_CANCELLED,
        target_kind=audit.FINDING_TARGET,
        target_id=finding_id,
        detail={"fix_id": str(row.id)},
    )
    await db.commit()
    return await read_source(
        db, identity=identity, finding_id=finding_id, settings=settings, now=now
    )


# ------------------------------------------------------------ the brief's paths


async def brief_files(
    db: AsyncSession,
    repository: TenantScopedRepository,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    settings: Settings,
    now: datetime,
) -> BriefFilesStarted:
    """FR5c: the stack's candidates wait in the request's own `job_results` row (a job row
    carries ids only, AC15), and a tree-only job answers them. Needs the Fix App to read
    the repository; no consent, since nothing leaves."""
    org_id = identity.org_id
    finding = await find_finding(db, org_id=org_id, finding_id=finding_id)
    if finding is None:
        raise NotFoundError(NOT_FOUND)
    state = await fix_state.gather(db, finding, settings=settings, now=now)
    if not state.access.read_permission:
        raise ConflictError(
            ELIGIBILITY_SENTENCES["no_read_permission"].requester, code="no_read_permission"
        )
    found = await fix_paths.stack_candidates(repository, finding, state.explanation)
    candidates = [] if isinstance(found, str) else [c.stored() for c in found]
    request_id = uuid.uuid4()
    db.add(
        JobResult(
            request_id=request_id,
            org_id=org_id,
            kind=BRIEF_PATHS_RESULT_KIND,
            payload={"candidates": candidates, "requested_by": str(identity.user.id)},
            expires_at=now + timedelta(seconds=BRIEF_PATHS_TTL_SECONDS),
        )
    )
    await fix_queries.queue_suggestion(
        db, match_brief_paths_request(request_id=request_id, finding_id=finding_id, org_id=org_id)
    )
    await db.commit()
    return BriefFilesStarted(request_id=request_id)


async def brief_files_result(
    db: AsyncSession, *, identity: Identity, finding_id: uuid.UUID, request_id: uuid.UUID
) -> BriefFiles:
    payload = await db.scalar(
        select(JobResult.payload).where(
            JobResult.org_id == identity.org_id,
            JobResult.request_id == request_id,
            JobResult.kind == BRIEF_PATHS_RESULT_KIND,
        )
    )
    if not isinstance(payload, dict) or "candidates" in payload:
        return BriefFiles(pending=True, paths=[])
    return BriefFiles(
        pending=False,
        paths=[
            BriefPath(path=str(each.get("path")), line=each.get("line"))
            for each in payload.get("paths") or []
            if isinstance(each, dict)
        ],
    )
