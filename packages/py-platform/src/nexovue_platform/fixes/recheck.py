"""A written suggestion checked again against the repository as it is now, for a patch (spec
FR20, FR20a, FR21, AC19, AC24, AC55; design, *The check run*, steps 4 and 5; plan T5.1).

The check run hands this the stored edits, the listing only `scheduler-connections` wrote,
each changed file as it is at the default branch's head, and, for a file that moved, the
file as it was read (its base blob). Nothing stored is believed (D18):

1. The rules that need no file, and FR16's prose checks, run again on what is stored
   (`read_stored`). They read nothing from the repository, so with the same rules and the
   same owner's list a refusal can only mean the stored row changed: **refused again**.
2. A changed file gone from the head, unreadable, or past the client's 1 MiB is **out of
   date** (`edit_no_longer_applies`).
3. **Display facts against the base blob** (D53): each edit's old text is placed in the
   file as it was read, and the facts Nexovue's own code computes there must equal the
   stored ones. The base is the file the suggestion was written from, so a difference is
   **refused again**, never out of date.
4. K1 to K10 on the head's files. A refusal where a file moved is **out of date**; where
   nothing moved, **refused again**.
5. Otherwise the patch, built against the head with 3 lines of context, and its digest
   (path, the head's blob id and the new content per file): **unchanged**, or **moved**
   when a file changed and every edit still applies.

Pure: no store, no clock, no I/O.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes.checks import Edit, PackFile, run_rules
from nexovue_platform.fixes.diff import (
    DISPLAY_FACT_KEYS,
    FILE_MODES,
    PatchFile,
    Span,
    build_patch,
    display_facts,
    suggestion_digest,
)
from nexovue_platform.fixes.policy import NO_OWNER_LISTS, OwnerLists
from nexovue_platform.fixes.records import CHECK_MOVED, CHECK_UNCHANGED
from nexovue_platform.fixes.stored import StoredRefusal, read_stored

EDIT_NO_LONGER_APPLIES: Final = "edit_no_longer_applies"
DISPLAY_FACTS_DIFFER: Final = "display_facts"


@dataclass(frozen=True, slots=True)
class CurrentFile:
    """A changed file at the default branch's head: its blob id and mode from the tree, and
    its text, or None when it could not be read as text within the client's cap."""

    blob_sha: str
    mode: str
    content: str | None


@dataclass(frozen=True, slots=True)
class NewFile:
    """A changed file as the draft job commits it (Step 7): held in memory only."""

    path: str
    mode: str
    content: str


@dataclass(frozen=True, slots=True)
class Rechecked:
    status: Literal["unchanged", "moved"]
    patch: str
    digest: str
    # Each changed file's path, and its blob id at the head the patch applies to.
    blob_ids: dict[str, str]
    moved_refs: tuple[str, ...]
    # The digest's own new contents, so the draft commits exactly what was checked (BR3).
    files: tuple[NewFile, ...] = ()


@dataclass(frozen=True, slots=True)
class OutOfDate:
    reason: str


@dataclass(frozen=True, slots=True)
class RefusedAgain:
    """D18's tamper signal: what failed, for the log and the audit entry, never shown."""

    failure: str


Outcome = Rechecked | OutOfDate | RefusedAgain


def recheck(
    edits: object,
    files_read: Mapping[str, Any] | None,
    *,
    folder: str | None,
    owner: OwnerLists = NO_OWNER_LISTS,
    allowed_evidence: Collection[str],
    aliases: AliasBook | None,
    head: Mapping[str, CurrentFile | None],
    base: Mapping[str, str | None],
) -> Outcome:
    """`head` by file ref, for every changed file; `base` by ref, for each that moved."""
    stored = read_stored(
        edits,
        files_read,
        folder=folder,
        owner=owner,
        allowed_evidence=allowed_evidence,
        aliases=aliases,
    )
    if isinstance(stored, StoredRefusal):
        return RefusedAgain(stored.failure)
    assert isinstance(edits, list)  # read_stored refuses anything else
    listed = _listed(files_read)
    by_ref: dict[str, list[dict[str, Any]]] = {}
    for item in edits:
        by_ref.setdefault(item["file_ref"], []).append(item)
    current: dict[str, CurrentFile] = {}
    for ref in by_ref:
        found = head.get(ref)
        if found is None or found.content is None or found.mode not in FILE_MODES:
            return OutOfDate(EDIT_NO_LONGER_APPLIES)
        current[ref] = found
    moved = tuple(ref for ref in by_ref if current[ref].blob_sha != listed[ref]["blob_sha"])
    for ref, items in by_ref.items():
        read_as = base.get(ref) if ref in moved else current[ref].content
        if read_as is None:
            return OutOfDate(EDIT_NO_LONGER_APPLIES)
        if not _facts_hold(read_as, items):
            return RefusedAgain(DISPLAY_FACTS_DIFFER)
    as_edits = [
        Edit(
            item["file_ref"],
            item["old_text"],
            item["new_text"],
            item["why"],
            tuple(item["evidence_ids"]),
        )
        for item in edits
    ]
    pack = {ref: PackFile(ref, listed[ref]["path"], str(current[ref].content)) for ref in by_ref}
    ruled = run_rules(as_edits, pack, folder=folder or "", owner=owner)
    if ruled.refused:
        if moved:
            return OutOfDate(EDIT_NO_LONGER_APPLIES)
        return RefusedAgain(ruled.refusals[0].rule)
    changed = [
        PatchFile(
            path=each.path,
            mode=current[each.ref].mode,
            old_content=each.old_content,
            spans=each.spans,
        )
        for each in ruled.files
    ]
    return Rechecked(
        status=CHECK_MOVED if moved else CHECK_UNCHANGED,
        patch=build_patch(changed),
        digest=suggestion_digest(
            (each.path, current[each.ref].blob_sha, each.new_content) for each in ruled.files
        ),
        blob_ids={each.path: current[each.ref].blob_sha for each in ruled.files},
        moved_refs=moved,
        files=tuple(
            NewFile(each.path, current[each.ref].mode, each.new_content) for each in ruled.files
        ),
    )


def _listed(files_read: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    return {
        str(each["ref"]): each
        for each in (files_read or {}).get("files") or []
        if isinstance(each, Mapping) and isinstance(each.get("ref"), str)
    }


def _facts_hold(content: str, items: list[dict[str, Any]]) -> bool:
    """D53 at a check run: the facts computed from the file as read equal those stored."""
    spans: list[Span] = []
    for item in items:
        start = content.find(item["old_text"])
        if start == -1 or content.find(item["old_text"], start + 1) != -1:
            return False
        spans.append(Span(start, start + len(item["old_text"]), item["new_text"]))
    computed = display_facts(content, spans)
    return all(
        {key: item[key] for key in DISPLAY_FACT_KEYS} == facts
        for item, facts in zip(items, computed, strict=True)
    )
