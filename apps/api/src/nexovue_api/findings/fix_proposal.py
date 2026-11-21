"""A written suggestion, read in a person's shape (spec FR16 to FR21, AC10, AC11, AC19, AC55
to AC57; design, *The flow*, step 6, *Section parts*, *State by role*; plan T4.7).

**Nothing stored is believed** (D18). Every read rebuilds the hunks from the display facts
(D53), runs again the rules that need no file and FR16's prose checks, and restores the
model's stand-ins as segments through the one restorer F3 reads with. A row whose
`checks_version` is not today's reads "Out of date", with no alert; a row the rules refuse
at the same version shows no code. A read calls no GitHub and no ClickHouse, raises no
alert and moves no state.

**Who sees code:** a person who may ask (`REQUEST_FIX`), never a viewer or a support view
(AC10, AC11), and under the owner's requirement (D20) only with their own passed `read`
check, unexpired. A person's first read of code inserts one `finding_fix_code_views` row,
and on insert audits `fix.code_viewed` (AC57's view half).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes.checks import Flag, line_flags
from nexovue_platform.fixes.consent import folder_covered
from nexovue_platform.fixes.diff import Hunk, patch_hunks
from nexovue_platform.fixes.policy import CHECKS_VERSION, OwnerLists
from nexovue_platform.fixes.prose import why_segments
from nexovue_platform.fixes.records import (
    BACKUPS_KEPT_FOR,
    CHECK_ANSWERS_WITHIN,
    CHECK_FOR_PATCH,
    CHECK_FOR_READ,
    CHECK_PASSED,
    CHECK_RUNNING,
    FIX_CODE_VIEWED,
)
from nexovue_platform.fixes.sentences import (
    CHECK_MY_ACCESS,
    CHECK_REASON_SENTENCES,
    CHECK_RUNNING_HEADING,
    CHECK_TIMED_OUT,
    CODE_DELETED_HEADING,
    CODE_DELETED_SENTENCE,
    CODE_DELETED_WHY,
    CONTEXT_TOO_LONG,
    EXPLANATION_LABEL,
    FACTS_FROM_COMMIT,
    FACTS_FROM_HEAD,
    FLAG_LABELS,
    MOVED_LEAD,
    MOVED_ON,
    NOT_AVAILABLE_HEADING,
    NOT_CHECKED,
    NOT_COVERED,
    OUT_OF_DATE_HEADING,
    OWNER_LIMITS_CODE,
    PATCH_BUILT_AGAINST,
    PATCH_READY_HEADING,
    PATCH_RUNNING,
    PATCH_RUNNING_HEADING,
    PREPARE_PATCH,
    PROPOSED_LEAD,
    PROPOSED_VIEWER,
    REASON_SENTENCES,
    SECTION_HEADING,
    SOURCE_READY_LABELS,
    WHOLE_REPOSITORY,
)
from nexovue_platform.fixes.stored import (
    StoredProposal,
    StoredRefusal,
    numbered_lines,
    read_stored,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import (
    FindingFix,
    FindingFixCheck,
    FindingFixCodeView,
    FixSourceConsent,
)
from nexovue_api.findings import explanation_queries
from nexovue_api.findings.fix_source_schemas import (
    CheckView,
    DiffHunk,
    DiffLine,
    LeftOutFile,
    ListedFile,
    PatchView,
    Proposal,
    ProposalExplanation,
    ProposalFile,
    ProposalFlag,
)
from nexovue_api.findings.schemas import ExplanationSegment

# A file opens by default only when the whole suggestion is this short (a guess, design).
OPEN_BY_DEFAULT_LINES: Final = 15
_SHORT_COMMIT: Final = 7


@dataclass(frozen=True, slots=True)
class ProposalRead:
    """What a `proposed` or `stale` row shows this person: the heading and sentence, and
    either the suggestion, a reason it is withheld from them, or a viewer's counts."""

    heading: str
    sentence: str | None
    proposal: Proposal | None = None
    code_withheld: str | None = None
    check_action: str | None = None
    check: CheckView | None = None
    file_count: int | None = None
    line_count: int | None = None
    patch_action: str | None = None
    patch: PatchView | None = None


def code_deleted(row: FindingFix) -> ProposalRead:
    """BR9: the record stays and says when its code went, why, and how long backups may
    still hold it (D29). The same for every role."""
    deleted_at = row.code_deleted_at
    assert deleted_at is not None
    return ProposalRead(
        CODE_DELETED_HEADING,
        CODE_DELETED_SENTENCE.format(
            when=deleted_at.date().isoformat(),
            why=CODE_DELETED_WHY.get(row.code_deleted_reason or "", "it ended."),
            until=(deleted_at + BACKUPS_KEPT_FOR).date().isoformat(),
        ),
    )


async def read_stale(
    db: AsyncSession,
    row: FindingFix,
    *,
    user_id: uuid.UUID,
    may_request: bool,
    repository_key: str | None,
    files_read: tuple[list[ListedFile], list[LeftOutFile]],
    now: datetime,
) -> ProposalRead:
    """Out of date (FR21): the old diff, labelled, what moved, and no action but Ask
    again, which the offer carries. A viewer reads "Out of date"."""
    reason = REASON_SENTENCES.get(row.reason or "", REASON_SENTENCES["rules_changed"])
    sentence = reason.requester if may_request else reason.viewer
    shown = await read_proposed(
        db,
        row,
        user_id=user_id,
        may_request=may_request,
        repository_key=repository_key,
        files_read=files_read,
        now=now,
        actions=False,
    )
    return ProposalRead(
        OUT_OF_DATE_HEADING,
        sentence,
        proposal=shown.proposal,
        file_count=shown.file_count,
        line_count=shown.line_count,
    )


async def read_proposed(
    db: AsyncSession,
    row: FindingFix,
    *,
    user_id: uuid.UUID,
    may_request: bool,
    repository_key: str | None,
    files_read: tuple[list[ListedFile], list[LeftOutFile]],
    now: datetime,
    actions: bool = True,
) -> ProposalRead:
    """A written row, checked again, in this person's shape. `actions` is False for an
    out-of-date row, which shows its old diff and nothing to act on."""
    if row.checks_version != CHECKS_VERSION:
        sentence = REASON_SENTENCES["rules_changed"]
        return ProposalRead(
            OUT_OF_DATE_HEADING, sentence.requester if may_request else sentence.viewer
        )
    explanation = (
        None
        if row.explanation_version is None
        else await explanation_queries.version(
            db, org_id=row.org_id, finding_id=row.finding_id, number=row.explanation_version
        )
    )
    book = None if explanation is None else AliasBook(explanation.aliases)
    consents = await _consents(db, row)
    checked = read_stored(
        row.edits,
        row.files_read,
        folder=row.folder,
        owner=_owner_lists(consents),
        allowed_evidence=_allowed_evidence(
            None if explanation is None else explanation.model_input, row.files_read
        ),
        aliases=book,
    )
    if isinstance(checked, StoredRefusal):
        refused = REASON_SENTENCES["checks_refused_at_click"]
        return ProposalRead(
            NOT_AVAILABLE_HEADING, refused.requester if may_request else refused.viewer
        )
    file_count, line_count = len(checked.files), checked.changed_lines
    if not may_request:
        return ProposalRead(
            SECTION_HEADING, PROPOSED_VIEWER, file_count=file_count, line_count=line_count
        )
    check = await _own_read_check(db, row, user_id=user_id, now=now)
    if _requires_github_access(consents) and not (check is not None and check.passed):
        return ProposalRead(
            SECTION_HEADING,
            _lead(row, checked),
            code_withheld=OWNER_LIMITS_CODE,
            check_action=CHECK_MY_ACCESS,
            check=check,
            file_count=file_count,
            line_count=line_count,
        )
    await _record_first_view(db, row, user_id=user_id)
    patch_check = await _own_patch_check(db, row, user_id=user_id) if actions else None
    rebuilt = _rebuilt(checked, patch_check, now=now)
    proposal = _proposal(
        row, checked, book, repository_key=repository_key, files_read=files_read, rebuilt=rebuilt
    )
    patch = None if patch_check is None else _patch_view(patch_check, rebuilt, now=now)
    running = patch is not None and patch.status == CHECK_RUNNING
    return ProposalRead(
        SECTION_HEADING,
        None,
        proposal=proposal,
        check=check,
        file_count=file_count,
        line_count=line_count,
        patch_action=PREPARE_PATCH if actions and not running else None,
        patch=patch,
    )


async def _consents(db: AsyncSession, row: FindingFix) -> list[FixSourceConsent]:
    if row.connection_repository_id is None:
        return []
    found = await db.scalars(
        select(FixSourceConsent).where(
            FixSourceConsent.org_id == row.org_id,
            FixSourceConsent.connection_repository_id == row.connection_repository_id,
            FixSourceConsent.withdrawn_at.is_(None),
        )
    )
    return [each for each in found if folder_covered(each.folder_scope, row.folder)]


def _owner_lists(consents: Sequence[FixSourceConsent]) -> OwnerLists:
    """K2 at read with every unwithdrawn consent's extra paths: the strictest reading."""
    extra = tuple(path for each in consents for path in each.extra_excluded_paths or [])
    return OwnerLists(extra_excluded_paths=extra)


def _requires_github_access(consents: Sequence[FixSourceConsent]) -> bool:
    """D20: any unwithdrawn consent covering the folder that asks for it, even one that
    ended by itself, since quoted code is still stored."""
    return any(each.require_github_access for each in consents)


def _allowed_evidence(
    model_input: Mapping[str, Any] | None, files_read: Mapping[str, Any] | None
) -> set[str]:
    """U3 at read: the explanation's fact ids, else the files' numbers, as at the call."""
    if model_input is not None:
        facts = model_input.get("facts") or []
        return {str(fact["id"]) for fact in facts if isinstance(fact, Mapping) and "id" in fact}
    return {
        str(each.get("ref"))
        for each in (files_read or {}).get("files") or []
        if isinstance(each, Mapping)
    }


async def _own_read_check(
    db: AsyncSession, row: FindingFix, *, user_id: uuid.UUID, now: datetime
) -> CheckView | None:
    """This person's own `read` check (or a `patch` check, from Step 5), never another's."""
    checks = list(
        await db.scalars(
            select(FindingFixCheck).where(
                FindingFixCheck.org_id == row.org_id,
                FindingFixCheck.fix_id == row.id,
                FindingFixCheck.requested_by == user_id,
            )
        )
    )
    passed = [each for each in checks if each.status in CHECK_PASSED and each.expires_at > now]
    chosen = (
        passed[0]
        if passed
        else next((each for each in checks if each.purpose == CHECK_FOR_READ), None)
    )
    return None if chosen is None else check_view(chosen, now=now)


async def _own_patch_check(
    db: AsyncSession, row: FindingFix, *, user_id: uuid.UUID
) -> FindingFixCheck | None:
    """This person's own `patch` check, never another's (AC40)."""
    found: FindingFixCheck | None = await db.scalar(
        select(FindingFixCheck).where(
            FindingFixCheck.org_id == row.org_id,
            FindingFixCheck.fix_id == row.id,
            FindingFixCheck.requested_by == user_id,
            FindingFixCheck.purpose == CHECK_FOR_PATCH,
        )
    )
    return found


@dataclass(frozen=True, slots=True)
class _Rebuilt:
    """A passed patch check's hunks by path, its flags, and which files moved (FR21)."""

    hunks: dict[str, tuple[Hunk, ...]]
    flags: tuple[Flag, ...]
    moved_refs: frozenset[str]
    head_sha: str
    expires_at: datetime


def _rebuilt(
    checked: StoredProposal, check: FindingFixCheck | None, *, now: datetime
) -> _Rebuilt | None:
    """The diff replaced in place by the check run's, read back from the very patch the
    person downloads, so the page and the file never differ (AC19). Flags are worked out
    again from its lines; new letters from what its hunks show, which can only add flags."""
    if (
        check is None
        or check.status not in CHECK_PASSED
        or check.expires_at <= now
        or check.patch is None
        or check.head_sha is None
    ):
        return None
    paths = sorted((check.blob_ids or {}).keys())
    try:
        read = patch_hunks(check.patch)
    except ValueError:
        return None
    if len(read) != len(paths):
        return None
    by_path = {path: tuple(hunks) for path, hunks in zip(paths, read, strict=True)}
    flags: list[Flag] = []
    for each in checked.files:
        hunks = by_path.get(each.path)
        if hunks is None:
            return None
        added, removed = numbered_lines(hunks)
        flags += line_flags(
            each.ref, each.path, added, removed, new_letter_lines=_letters_not_shown(hunks)
        )
    return _Rebuilt(
        hunks=by_path,
        flags=tuple(flags),
        moved_refs=frozenset(str(ref) for ref in check.moved_refs or []),
        head_sha=check.head_sha,
        expires_at=check.expires_at,
    )


def _letters_not_shown(hunks: Sequence[Hunk]) -> set[int]:
    """Added lines holding a non-ASCII letter found on no context or removed line shown."""
    seen = {
        character
        for hunk in hunks
        for marker, line in hunk.lines
        if marker != "+"
        for character in line
    }
    found: set[int] = set()
    for hunk in hunks:
        number = hunk.new_start
        for marker, line in hunk.lines:
            if marker == "-":
                continue
            if marker == "+" and any(
                not c.isascii() and c.isalpha() and c not in seen for c in line
            ):
                found.add(number)
            number += 1
    return found


def _patch_view(
    check: FindingFixCheck, rebuilt: _Rebuilt | None, *, now: datetime
) -> PatchView | None:
    """This person's patch check in words (design, *State by role*, check rows)."""
    if check.status == CHECK_RUNNING:
        late = check.requested_at + CHECK_ANSWERS_WITHIN <= now
        return PatchView(
            status="timed_out" if late else CHECK_RUNNING,
            heading=PATCH_RUNNING_HEADING,
            sentence=CHECK_TIMED_OUT if late else PATCH_RUNNING,
            downloadable=False,
            expires_at=None,
        )
    if check.status in CHECK_PASSED:
        if rebuilt is None:
            return None
        return PatchView(
            status=check.status,
            heading=PATCH_READY_HEADING,
            sentence=PATCH_BUILT_AGAINST.format(
                commit=rebuilt.head_sha[:_SHORT_COMMIT],
                until=rebuilt.expires_at.strftime("%H:%M UTC"),
            ),
            downloadable=True,
            expires_at=rebuilt.expires_at,
        )
    return PatchView(
        status=check.status,
        heading=NOT_AVAILABLE_HEADING,
        sentence=None if check.reason is None else CHECK_REASON_SENTENCES.get(check.reason),
        downloadable=False,
        expires_at=None,
    )


def check_view(check: FindingFixCheck, *, now: datetime) -> CheckView:
    """A check row in words. With no answer in 3 minutes it reads as `check_timed_out`."""
    passed = check.status in CHECK_PASSED and check.expires_at > now
    if check.status == CHECK_RUNNING:
        late = check.requested_at + CHECK_ANSWERS_WITHIN <= now
        return CheckView(
            id=check.id,
            purpose=check.purpose,
            status="timed_out" if late else CHECK_RUNNING,
            heading=CHECK_RUNNING_HEADING,
            sentence=CHECK_TIMED_OUT if late else None,
            passed=False,
            expires_at=None,
        )
    sentence = None if check.reason is None else CHECK_REASON_SENTENCES.get(check.reason)
    return CheckView(
        id=check.id,
        purpose=check.purpose,
        status=check.status,
        heading=SECTION_HEADING if passed else NOT_AVAILABLE_HEADING,
        sentence=sentence,
        passed=passed,
        expires_at=check.expires_at if passed else None,
    )


async def _record_first_view(db: AsyncSession, row: FindingFix, *, user_id: uuid.UUID) -> None:
    """One row per person per suggestion; the audit entry only when it is new (AC57)."""
    inserted = await db.execute(
        insert(FindingFixCodeView)
        .values(fix_id=row.id, user_id=user_id, org_id=row.org_id)
        .on_conflict_do_nothing(index_elements=["fix_id", "user_id"])
        .returning(FindingFixCodeView.fix_id)
    )
    if inserted.first() is None:
        return
    audit.record_fix_event(
        db,
        org_id=row.org_id,
        actor_user_id=user_id,
        action=FIX_CODE_VIEWED,
        target_kind=audit.FINDING_TARGET,
        target_id=row.finding_id,
        detail={"fix_id": str(row.id)},
    )
    await db.commit()


def _lead(row: FindingFix, checked: StoredProposal) -> str:
    files, lines = len(checked.files), checked.changed_lines
    return PROPOSED_LEAD.format(
        files=files,
        files_word="file" if files == 1 else "files",
        lines=lines,
        lines_word="line" if lines == 1 else "lines",
        folder=row.folder or WHOLE_REPOSITORY,
    )


def _proposal(
    row: FindingFix,
    checked: StoredProposal,
    book: AliasBook | None,
    *,
    repository_key: str | None,
    files_read: tuple[list[ListedFile], list[LeftOutFile]],
    rebuilt: _Rebuilt | None,
) -> Proposal:
    commit = str((row.files_read or {}).get("commit") or "")
    listed, left_out = files_read
    urls = {file.path: file.github_url for file in listed}
    whys: dict[str, list[ProposalExplanation]] = {}
    for why in checked.whys:
        segments = (
            [ExplanationSegment(text=why.text)]
            if book is None
            else [ExplanationSegment(**segment) for segment in why_segments(why.text, book)]
        )
        whys.setdefault(why.ref, []).append(ProposalExplanation(segments=segments))
    open_all = checked.changed_lines <= OPEN_BY_DEFAULT_LINES
    flags = checked.flags if rebuilt is None else rebuilt.flags
    flags_by_line = _flags_by_line(flags)
    files = []
    for each in checked.files:
        hunks = each.hunks if rebuilt is None else rebuilt.hunks[each.path]
        moved = rebuilt is not None and each.ref in rebuilt.moved_refs
        files.append(
            ProposalFile(
                ref=each.ref,
                path=each.path,
                github_url=urls.get(each.path),
                added=each.added,
                removed=each.removed,
                hunks=None
                if hunks is None
                else [_hunk(hunk, flags_by_line.get(each.ref, {})) for hunk in hunks],
                context_note=CONTEXT_TOO_LONG if hunks is None else None,
                open=open_all,
                explanations=whys.get(each.ref, []),
                moved_note=MOVED_ON if moved else None,
            )
        )
    return Proposal(
        lead=_lead(row, checked),
        starts_from=None if row.basis is None else SOURCE_READY_LABELS.get(row.basis),
        facts_note=FACTS_FROM_COMMIT.format(commit=commit[:_SHORT_COMMIT] or "—")
        if rebuilt is None
        else FACTS_FROM_HEAD.format(commit=rebuilt.head_sha[:_SHORT_COMMIT]),
        flags=[
            ProposalFlag(
                kind=flag.kind,
                label=FLAG_LABELS[flag.kind],
                path=flag.path,
                line=flag.line,
                side=flag.side,
            )
            for flag in flags
        ],
        files=files,
        explanation_label=EXPLANATION_LABEL,
        not_checked=[NOT_CHECKED, *(NOT_COVERED.format(path=path) for path in checked.uncovered)],
        files_read=listed,
        left_out=left_out,
        written_at=row.finished_at,
        commit=commit[:_SHORT_COMMIT],
        tokens=row.tokens_used,
        rebuilt_note=MOVED_LEAD if rebuilt is not None and rebuilt.moved_refs else None,
    )


def _flags_by_line(flags: Sequence[Flag]) -> dict[str, dict[tuple[str, int], list[str]]]:
    found: dict[str, dict[tuple[str, int], list[str]]] = {}
    for flag in flags:
        if flag.line is None:
            continue
        found.setdefault(flag.ref, {}).setdefault((flag.side, flag.line), []).append(
            FLAG_LABELS[flag.kind]
        )
    return found


def _hunk(hunk: Hunk, flags: Mapping[tuple[str, int], list[str]]) -> DiffHunk:
    lines: list[DiffLine] = []
    old, new = hunk.old_start, hunk.new_start
    for marker, text in hunk.lines:
        bare = text.removesuffix("\n").removesuffix("\r")
        if marker == "+":
            lines.append(
                DiffLine(
                    marker="+", text=bare, old=None, new=new, flags=flags.get(("new", new), [])
                )
            )
            new += 1
        elif marker == "-":
            lines.append(
                DiffLine(
                    marker="-", text=bare, old=old, new=None, flags=flags.get(("old", old), [])
                )
            )
            old += 1
        else:
            lines.append(DiffLine(marker=" ", text=bare, old=old, new=new, flags=[]))
            old, new = old + 1, new + 1
    return DiffHunk(header=hunk.header, lines=lines)
