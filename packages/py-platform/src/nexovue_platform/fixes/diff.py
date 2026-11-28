"""Building what a person reads and downloads from checked edits (spec FR20, FR20a; design
*Display facts (D53)*, *The check run*).

Nexovue builds the diff itself, so a model cannot misdescribe what its change does.
The model returns edits (exact old text and new text); here they become line changes,
numbered hunks with 3 lines of context, and a git-format patch with the headers, mode
and `\\ No newline at end of file` markers `git apply` needs (AC41).

**One way of turning edits into hunks, used twice.** The check run has the whole file,
and builds hunks from it. A read of a stored proposal has only the display facts the AI
job stored inside each edit (D53), and builds hunks from those. Both call
`line_changes` and `hunks` on the lines they know, so for an unmoved file they agree
(`test_fix_diff.py`). Changes are worked out per region of the file an edit touches,
never by diffing the whole file, which is what makes the two agree.

Pure: no store, no clock, no I/O.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Final, Literal

CONTEXT_LINES: Final = 3
# A gap of equal lines up to this long joins two changes into one hunk, as `diff -U3`.
_JOINING_GAP: Final = 2 * CONTEXT_LINES
# Display facts per edit (a guess, D53). Over this, context is left out.
DISPLAY_FACTS_MAX_BYTES: Final = 4 * 1024
DISPLAY_FACT_KEYS: Final = (
    "old_start",
    "first_line_prefix",
    "last_line_suffix",
    "context_before",
    "context_after",
    "new_letter_lines",
)
REGULAR_FILE_MODE: Final = "100644"
EXECUTABLE_FILE_MODE: Final = "100755"
FILE_MODES: Final = (REGULAR_FILE_MODE, EXECUTABLE_FILE_MODE)
NO_FINAL_NEWLINE: Final = "\\ No newline at end of file\n"

Marker = Literal[" ", "-", "+"]


@dataclass(frozen=True, slots=True)
class Span:
    """One edit placed in the file it was read from: old text at [start, end)."""

    start: int
    end: int
    new_text: str


@dataclass(frozen=True, slots=True)
class LineChange:
    """Lines removed from the old file at `old_index` (0-based), and lines added there.
    Each line keeps its own ending, so CRLF and a missing final newline survive."""

    old_index: int
    removed: tuple[str, ...]
    added: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[tuple[Marker, str], ...]

    @property
    def header(self) -> str:
        return f"@@ -{self.old_start},{self.old_count} +{self.new_start},{self.new_count} @@"


@dataclass(frozen=True, slots=True)
class Region:
    """A run of whole old lines that one or more edits change."""

    first_line: int
    start: int
    end: int
    spans: tuple[Span, ...]


# --------------------------------------------------------------- applying edits


def apply_spans(content: str, spans: Sequence[Span]) -> str:
    """The new file: each span's old text replaced, all against the original."""
    pieces, position = [], 0
    for span in sorted(spans, key=lambda each: each.start):
        pieces += [content[position : span.start], span.new_text]
        position = span.end
    pieces.append(content[position:])
    return "".join(pieces)


def split_lines(text: str) -> list[str]:
    """Lines with their endings. Only `\\n` ends a line, as for git, so a CRLF line keeps
    its `\\r` and a form feed or `\\u2028` stays inside its line."""
    lines = [line + "\n" for line in text.split("\n")]
    lines[-1] = lines[-1][:-1]
    return [line for line in lines if line]


def _line_starts(content: str) -> list[int]:
    starts = [0]
    starts += [index + 1 for index, character in enumerate(content) if character == "\n"]
    if starts[-1] == len(content) and len(starts) > 1:
        starts.pop()
    return starts


def regions(content: str, spans: Sequence[Span]) -> list[Region]:
    """The whole lines each edit touches, joined where they share or run into a line.

    A region whose new text no longer ends a line (old text `a\\n` made `a`) takes in
    the line after it, so the region's new lines are whole lines too.
    """
    starts = _line_starts(content)

    def line_of(offset: int) -> int:
        return max(bisect.bisect_right(starts, offset) - 1, 0)

    def line_end(line: int) -> int:
        return starts[line + 1] if line + 1 < len(starts) else len(content)

    found: list[Region] = []
    for span in sorted(spans, key=lambda each: each.start):
        first = line_of(span.start)
        last = line_of(max(span.end - 1, span.start))
        start, end = starts[first] if content else 0, line_end(last)
        if found and start < found[-1].end:
            previous = found.pop()
            first, start = previous.first_line, previous.start
            end, merged = max(end, previous.end), (*previous.spans, span)
        else:
            merged = (span,)
        found.append(Region(first_line=first, start=start, end=end, spans=merged))
        while True:
            region = found[-1]
            new_text = _region_new_text(content, region)
            if not new_text or new_text.endswith("\n") or region.end >= len(content):
                break
            found[-1] = Region(
                region.first_line, region.start, line_end(line_of(region.end)), region.spans
            )
    return found


def _region_new_text(content: str, region: Region) -> str:
    shifted = [
        Span(span.start - region.start, span.end - region.start, span.new_text)
        for span in region.spans
    ]
    return apply_spans(content[region.start : region.end], shifted)


def region_changes(content: str, spans: Sequence[Span]) -> list[tuple[Region, list[LineChange]]]:
    """Each region with its removed and added lines, found by `SequenceMatcher` within
    the region only, never across the file."""
    found = []
    for region in regions(content, spans):
        old_lines = split_lines(content[region.start : region.end])
        new_lines = split_lines(_region_new_text(content, region))
        matcher = SequenceMatcher(None, old_lines, new_lines, autojunk=False)
        found.append(
            (
                region,
                [
                    LineChange(
                        old_index=region.first_line + i1,
                        removed=tuple(old_lines[i1:i2]),
                        added=tuple(new_lines[j1:j2]),
                    )
                    for tag, i1, i2, j1, j2 in matcher.get_opcodes()
                    if tag != "equal"
                ],
            )
        )
    return found


def line_changes(content: str, spans: Sequence[Span]) -> list[LineChange]:
    return [change for _, changes in region_changes(content, spans) for change in changes]


# -------------------------------------------------------------------- hunks


def hunks(
    old_lines: Sequence[str],
    changes: Sequence[LineChange],
    *,
    first_line: int = 0,
    line_delta: int = 0,
) -> list[Hunk]:
    """Numbered hunks with 3 lines of context.

    `old_lines` may be a block of the file starting at `first_line` (0-based), and
    `line_delta` is how many lines earlier blocks' changes added; the changes' indexes
    are the file's.
    """
    groups: list[list[LineChange]] = []
    for change in sorted(changes, key=lambda each: each.old_index):
        if groups:
            last = groups[-1][-1]
            if change.old_index - (last.old_index + len(last.removed)) <= _JOINING_GAP:
                groups[-1].append(change)
                continue
        groups.append([change])
    built, delta = [], line_delta
    for group in groups:
        local_first = group[0].old_index - first_line
        local_last = group[-1].old_index + len(group[-1].removed) - first_line
        begin = max(local_first - CONTEXT_LINES, 0)
        finish = min(local_last + CONTEXT_LINES, len(old_lines))
        by_index = {change.old_index - first_line: change for change in group}
        lines: list[tuple[Marker, str]] = []
        index = begin
        while index < finish or index in by_index:
            at_index = by_index.get(index)
            if at_index is not None:
                lines += [("-", line) for line in at_index.removed]
                lines += [("+", line) for line in at_index.added]
                index += len(at_index.removed)
                if not at_index.removed:
                    if index < finish:
                        lines.append((" ", old_lines[index]))
                    index += 1
                continue
            lines.append((" ", old_lines[index]))
            index += 1
        old_count = sum(1 for marker, _ in lines if marker != "+")
        new_count = sum(1 for marker, _ in lines if marker != "-")
        old_begin = first_line + begin
        built.append(
            Hunk(
                old_start=old_begin + 1 if old_count else old_begin,
                old_count=old_count,
                new_start=old_begin + delta + 1 if new_count else old_begin + delta,
                new_count=new_count,
                lines=tuple(lines),
            )
        )
        delta += sum(len(change.added) - len(change.removed) for change in group)
    return built


def file_hunks(content: str, spans: Sequence[Span]) -> list[Hunk]:
    """Hunks from the whole file, as the check run builds them."""
    return hunks(split_lines(content), line_changes(content, spans))


# --------------------------------------------------------------- the patch


def git_blob_id(content: str) -> str:
    """Git's id for a file's bytes: SHA-1 over `blob <length>\\0` and the bytes."""
    data = content.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _quoted(name: str) -> str:
    """A path as git writes it in a header: C-quoted when it holds a quote, a backslash,
    a control character or a byte outside ASCII."""
    if all(0x20 <= ord(c) < 0x7F and c not in '"\\' for c in name):
        return name
    escaped = []
    for byte in name.encode("utf-8"):
        if byte in (0x22, 0x5C):
            escaped.append("\\" + chr(byte))
        elif 0x20 <= byte < 0x7F:
            escaped.append(chr(byte))
        else:
            escaped.append(f"\\{byte:03o}")
    return '"' + "".join(escaped) + '"'


@dataclass(frozen=True, slots=True)
class PatchFile:
    path: str
    mode: str
    old_content: str
    spans: tuple[Span, ...]


def file_patch(changed: PatchFile) -> str:
    if changed.mode not in FILE_MODES:
        raise ValueError(f"not a mode a suggestion may keep: {changed.mode}")
    new_content = apply_spans(changed.old_content, changed.spans)
    old_name, new_name = _quoted(f"a/{changed.path}"), _quoted(f"b/{changed.path}")
    text = [
        f"diff --git {old_name} {new_name}\n",
        f"index {git_blob_id(changed.old_content)}..{git_blob_id(new_content)} {changed.mode}\n",
        f"--- {old_name}\n",
        f"+++ {new_name}\n",
    ]
    for hunk in file_hunks(changed.old_content, changed.spans):
        text.append(hunk.header + "\n")
        for marker, line in hunk.lines:
            ended = line if line.endswith("\n") else line + "\n" + NO_FINAL_NEWLINE
            text.append(marker + ended)
    return "".join(text)


def build_patch(files: Iterable[PatchFile]) -> str:
    """A git-format patch, files in path order, that `git apply` accepts (AC41)."""
    return "".join(file_patch(each) for each in sorted(files, key=lambda each: each.path))


_HUNK_HEADER: Final = re.compile(r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@\n$")


def patch_hunks(patch: str) -> list[list[Hunk]]:
    """Each file's hunks, in the patch's order, read back from `build_patch`'s own text.

    The check run stores the patch it built; a read shows the rebuilt diff from it, so the
    page and the download can never differ (AC19). Only this module's own form is read:
    anything else raises `ValueError`, and the read shows no code.
    """
    files: list[list[Hunk]] = []
    open_hunk: list[tuple[Marker, str]] | None = None
    header: tuple[int, int, int, int] = (0, 0, 0, 0)

    def close() -> None:
        if open_hunk is not None:
            files[-1].append(Hunk(*header, lines=tuple(open_hunk)))

    for line in split_lines(patch):
        if line.startswith("diff --git "):
            close()
            open_hunk = None
            files.append([])
        elif line.startswith(("index ", "--- ", "+++ ")) and open_hunk is None:
            continue
        elif (match := _HUNK_HEADER.match(line)) is not None and files:
            close()
            old_start, old_count, new_start, new_count = (int(each) for each in match.groups())
            header = (old_start, old_count, new_start, new_count)
            open_hunk = []
        elif line == NO_FINAL_NEWLINE and open_hunk:
            marker, text = open_hunk[-1]
            open_hunk[-1] = (marker, text.removesuffix("\n"))
        elif open_hunk is not None and line[:1] in (" ", "-", "+"):
            open_hunk.append((_marker(line[:1]), line[1:]))
        else:
            raise ValueError("not a patch this module wrote")
    close()
    return files


def _marker(character: str) -> Marker:
    return " " if character == " " else "-" if character == "-" else "+"


def suggestion_digest(files: Iterable[tuple[str, str, str]]) -> str:
    """SHA-256 over each changed file's path, base blob id and new content, in path
    order (design, *The check run*; AC19a).

    Each field is length-prefixed, so no two different lists share a digest. It holds
    no head sha, so an unrelated commit on a busy default branch does not change it.
    """
    digest = hashlib.sha256()
    for path, base_blob_id, new_content in sorted(files):
        for field in (path, base_blob_id, new_content):
            data = field.encode("utf-8")
            digest.update(b"%d:" % len(data) + data)
    return digest.hexdigest()


# ------------------------------------------------------- display facts (D53)


def _letters_new_to(line: str, original: str) -> bool:
    return any(not c.isascii() and c.isalpha() and c not in original for c in line)


def display_facts(content: str, spans: Sequence[Span]) -> list[dict[str, Any]]:
    """For each span, in the order given, what a read needs to show it without the file.

    Computed by Nexovue's own code from the file the AI job holds, never taken from the
    model's answer. `new_letter_lines` are the new file's line numbers (1-based) of
    added lines holding non-ASCII letters not already in the file.
    """
    starts = _line_starts(content)
    lines = split_lines(content)
    found = region_changes(content, spans)
    region_of = {span: region for region, _ in found for span in region.spans}
    letters: dict[int, list[int]] = {}
    delta = 0
    for region, changes in found:
        for change in changes:
            new_first = change.old_index + delta + 1
            letters.setdefault(region.first_line, []).extend(
                new_first + offset
                for offset, line in enumerate(change.added)
                if _letters_new_to(line, content)
            )
            delta += len(change.added) - len(change.removed)
    facts = []
    for span in spans:
        region = region_of[span]
        first = max(bisect.bisect_right(starts, span.start) - 1, 0)
        after = _last_line(region, starts) + 1
        each: dict[str, Any] = {
            "old_start": first + 1,
            "first_line_prefix": content[starts[first] if content else 0 : span.start],
            "last_line_suffix": content[span.end : region.end],
            "context_before": "".join(lines[max(first - CONTEXT_LINES, 0) : first]),
            "context_after": "".join(lines[after : after + CONTEXT_LINES]),
            "new_letter_lines": sorted(set(letters.get(region.first_line, []))),
        }
        if len(json.dumps(each).encode("utf-8")) > DISPLAY_FACTS_MAX_BYTES:
            each["context_before"] = each["context_after"] = None
        facts.append(each)
    return facts


def _last_line(region: Region, starts: Sequence[int]) -> int:
    return max(bisect.bisect_right(starts, max(region.end - 1, region.start)) - 1, 0)


def facts_have_shape(facts: Mapping[str, Any]) -> bool:
    """The API's check on read: a fact of the wrong shape means no code is shown (AC19)."""
    if set(facts) != set(DISPLAY_FACT_KEYS):
        return False
    start, letters = facts["old_start"], facts["new_letter_lines"]
    texts = (facts["first_line_prefix"], facts["last_line_suffix"])
    contexts = (facts["context_before"], facts["context_after"])
    return (
        isinstance(start, int)
        and not isinstance(start, bool)
        and start >= 1
        and all(isinstance(text, str) for text in texts)
        and "\n" not in facts["first_line_prefix"]
        and all(context is None or isinstance(context, str) for context in contexts)
        and (contexts[0] is None) == (contexts[1] is None)
        and (contexts[0] is None or len(split_lines(contexts[0])) <= CONTEXT_LINES)
        and (contexts[1] is None or len(split_lines(contexts[1])) <= CONTEXT_LINES)
        and isinstance(letters, list)
        and all(isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in letters)
    )


@dataclass(frozen=True, slots=True)
class StoredEdit:
    """An edit as stored: its old and new text, and its display facts."""

    old_text: str
    new_text: str
    facts: Mapping[str, Any]


def derived_hunks(edits: Sequence[StoredEdit]) -> list[Hunk] | None:
    """One file's hunks from its stored edits' display facts alone (D53).

    None when the facts cannot rebuild them: context left out for size, or facts that
    disagree with each other or with the old text, which a read treats as tampering.
    """
    known: dict[int, str] = {}
    placed: list[tuple[int, int, StoredEdit]] = []
    for edit in edits:
        facts = edit.facts
        if not facts_have_shape(facts) or facts["context_before"] is None:
            return None
        first = int(facts["old_start"]) - 1
        before = split_lines(str(facts["context_before"]))
        region = split_lines(facts["first_line_prefix"] + edit.old_text + facts["last_line_suffix"])
        after = split_lines(str(facts["context_after"]))
        if first - len(before) < 0:
            return None
        for index, line in enumerate([*before, *region, *after], start=first - len(before)):
            if known.setdefault(index, line) != line:
                return None
        placed.append((first, len(facts["first_line_prefix"]), edit))
    hunks_found: list[Hunk] = []
    delta = 0
    for block_first, block_lines in _blocks(known):
        offsets = [0]
        for line in block_lines:
            offsets.append(offsets[-1] + len(line))
        text = "".join(block_lines)
        spans = []
        for first, prefix, edit in placed:
            if block_first <= first < block_first + len(block_lines):
                start = offsets[first - block_first] + prefix
                if text[start : start + len(edit.old_text)] != edit.old_text:
                    return None
                spans.append(Span(start, start + len(edit.old_text), edit.new_text))
        changes = [
            LineChange(change.old_index + block_first, change.removed, change.added)
            for change in line_changes(text, spans)
        ]
        hunks_found += hunks(block_lines, changes, first_line=block_first, line_delta=delta)
        delta += sum(len(change.added) - len(change.removed) for change in changes)
    return hunks_found


def _blocks(known: Mapping[int, str]) -> list[tuple[int, list[str]]]:
    """Runs of consecutive known lines."""
    blocks: list[tuple[int, list[str]]] = []
    for index in sorted(known):
        if blocks and blocks[-1][0] + len(blocks[-1][1]) == index:
            blocks[-1][1].append(known[index])
        else:
            blocks.append((index, [known[index]]))
    return blocks
