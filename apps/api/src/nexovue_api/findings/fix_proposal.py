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
from nexovue_platform.fixes.checks import Flag
from nexovue_platform.fixes.consent import folder_covered
from nexovue_platform.fixes.diff import Hunk
from nexovue_platform.fixes.policy import CHECKS_VERSION, OwnerLists
from nexovue_platform.fixes.prose import why_segments
from nexovue_platform.fixes.records import (
    CHECK_ANSWERS_WITHIN,
    CHECK_FOR_READ,
    CHECK_RUNNING,
    CHECK_UNCHANGED,
    FIX_CODE_VIEWED,
)
from nexovue_platform.fixes.sentences import (
    CHECK_MY_ACCESS,
    CHECK_REASON_SENTENCES,
    CHECK_RUNNING_HEADING,
    CHECK_TIMED_OUT,
    CONTEXT_TOO_LONG,
    EXPLANATION_LABEL,
    FACTS_FROM_COMMIT,
    FLAG_LABELS,
    NOT_AVAILABLE_HEADING,
    NOT_CHECKED,
    NOT_COVERED,
    OUT_OF_DATE_HEADING,
    OWNER_LIMITS_CODE,
    PROPOSED_LEAD,
    PROPOSED_VIEWER,
    REASON_SENTENCES,
    SECTION_HEADING,
    SOURCE_READY_LABELS,
    WHOLE_REPOSITORY,
)
from nexovue_platform.fixes.stored import StoredProposal, StoredRefusal, read_stored
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
    """What a `proposed` row shows this person: the heading and sentence, and either the
    suggestion, a reason it is withheld from them, or a viewer's counts."""

    heading: str
    sentence: str | None
    proposal: Proposal | None = None
    code_withheld: str | None = None
    check_action: str | None = None
    check: CheckView | None = None
    file_count: int | None = None
    line_count: int | None = None


async def read_proposed(
    db: AsyncSession,
    row: FindingFix,
    *,
    user_id: uuid.UUID,
    may_request: bool,
    repository_key: str | None,
    files_read: tuple[list[ListedFile], list[LeftOutFile]],
    now: datetime,
) -> ProposalRead:
    """A `proposed` row, checked again, in this person's shape."""
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
    proposal = _proposal(row, checked, book, repository_key=repository_key, files_read=files_read)
    return ProposalRead(
        SECTION_HEADING,
        None,
        proposal=proposal,
        check=check,
        file_count=file_count,
        line_count=line_count,
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
    passed = [each for each in checks if each.status == CHECK_UNCHANGED and each.expires_at > now]
    chosen = (
        passed[0]
        if passed
        else next((each for each in checks if each.purpose == CHECK_FOR_READ), None)
    )
    return None if chosen is None else check_view(chosen, now=now)


def check_view(check: FindingFixCheck, *, now: datetime) -> CheckView:
    """A check row in words. With no answer in 3 minutes it reads as `check_timed_out`."""
    passed = check.status == CHECK_UNCHANGED and check.expires_at > now
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
    flags_by_line = _flags_by_line(checked.flags)
    files = [
        ProposalFile(
            ref=each.ref,
            path=each.path,
            github_url=urls.get(each.path),
            added=each.added,
            removed=each.removed,
            hunks=None
            if each.hunks is None
            else [_hunk(hunk, flags_by_line.get(each.ref, {})) for hunk in each.hunks],
            context_note=CONTEXT_TOO_LONG if each.hunks is None else None,
            open=open_all,
            explanations=whys.get(each.ref, []),
        )
        for each in checked.files
    ]
    return Proposal(
        lead=_lead(row, checked),
        starts_from=None if row.basis is None else SOURCE_READY_LABELS.get(row.basis),
        facts_note=FACTS_FROM_COMMIT.format(commit=commit[:_SHORT_COMMIT] or "—"),
        flags=[
            ProposalFlag(
                kind=flag.kind,
                label=FLAG_LABELS[flag.kind],
                path=flag.path,
                line=flag.line,
                side=flag.side,
            )
            for flag in checked.flags
        ],
        files=files,
        explanation_label=EXPLANATION_LABEL,
        not_checked=[NOT_CHECKED, *(NOT_COVERED.format(path=path) for path in checked.uncovered)],
        files_read=listed,
        left_out=left_out,
        written_at=row.finished_at,
        commit=commit[:_SHORT_COMMIT],
        tokens=row.tokens_used,
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
