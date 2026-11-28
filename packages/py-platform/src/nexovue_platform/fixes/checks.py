"""Nexovue's rules on a suggested change: the schema guards, K1 to K10, and the flags
(spec FR16 to FR18, D18, D22; design *The rules*).

**The same code in every room.** The AI job runs these at the model's return, the API at
every read, the connections room at every check run, and the sweep once more when it
first records a result, because a broken AI process could store anything (D18). So
this module is pure, holds no database driver and imports no `nexovue_platform.ai`
(`test_cross_service_contracts.py`, `test_fix_platform_holds_no_ai.py`).

**Every rule runs on the whole file.** Edits apply in order against the file that was
read, so a rule sees each added line as it will stand, and "already in that file" means
the file before the change. The patterns are guesses, each with what it misses stated
in the design; a subtle bug passes every rule, and only a person can catch it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes.diff import (
    DISPLAY_FACT_KEYS,
    LineChange,
    Span,
    apply_spans,
    line_changes,
)
from nexovue_platform.fixes.patterns import (
    addresses_in,
    hidden_characters,
    mixed_script_words,
    without_urls,
)
from nexovue_platform.fixes.policy import NO_OWNER_LISTS, OwnerLists, is_never_changed
from nexovue_platform.fixes.prose import check_why, evidence_ids_known
from nexovue_platform.fixes.secrets import secret_in, shannon_bits_per_character

# ------------------------------------------------------------ the answer's schema

OUTCOME_EDITS: Final = "edits"
OUTCOME_ABSTAIN: Final = "abstain"
# The fixed reasons an abstention may give (design, *What the model gets*).
ABSTAIN_REASONS: Final = (
    "not_in_these_files",
    "evidence_too_thin",
    "needs_a_decision",
    "not_a_code_problem",
)
EDIT_KEYS: Final = frozenset({"file_ref", "old_text", "new_text", "why", "evidence_ids"})
# Keys asking for something other than a change to an existing file's text (K4).
FILE_OPERATION_KEYS: Final = frozenset(
    {"mode", "new_mode", "create", "delete", "rename", "new_path", "new_file"}
)
# The stored `edits` column's cap (design, `finding_fixes`), also the answer's.
EDITS_MAX_BYTES: Final = 64 * 1024
# More edits than this is an oversized answer, whatever their bytes (a guess).
EDITS_MAX_COUNT: Final = 20
_FILE_REF: Final = re.compile(r"file_[1-9][0-9]{0,2}")
# C0 controls other than tab, line feed and carriage return; `jsonb` rejects `\u0000`.
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LONE_SURROGATE: Final = re.compile(r"[\ud800-\udfff]")

# The fixed codes an unusable answer is retried with, never echoing the model's text.
Unusable = Literal[
    "not_an_object",
    "unknown_outcome",
    "unknown_abstain_reason",
    "no_edits",
    "too_large",
    "edit_shape",
    "file_operation_key",
    "display_fact_key",
    "unchanged_edit",
    "control_character",
    "unknown_evidence",
    "why_refused",
]


@dataclass(frozen=True, slots=True)
class Edit:
    file_ref: str
    old_text: str
    new_text: str
    why: str
    evidence_ids: tuple[str, ...]
    # A K4 key the answer carried, kept so K4 refuses it by name.
    file_operation: str | None = None


@dataclass(frozen=True, slots=True)
class Abstention:
    reason: str


@dataclass(frozen=True, slots=True)
class ProposedEdits:
    edits: tuple[Edit, ...]


@dataclass(frozen=True, slots=True)
class UnusableAnswer:
    code: Unusable


ParsedAnswer = ProposedEdits | Abstention | UnusableAnswer


def _text_problem(text: str) -> bool:
    return bool(_CONTROL.search(text) or _LONE_SURROGATE.search(text))


def parse_answer(
    raw: object, *, allowed_evidence: Collection[str], aliases: AliasBook | None
) -> ParsedAnswer:
    """The schema guards, before any rule (FR15, FR16; AC19, AC20).

    `allowed_evidence` holds the evidence ids the input carried, or with no explanation
    row the pack's `file_N` references. A failure here is an unusable answer, which gets
    one retry with its fixed code, and never reaches the store as data.
    """
    if not isinstance(raw, Mapping):
        return UnusableAnswer("not_an_object")
    outcome = raw.get("outcome")
    if outcome == OUTCOME_ABSTAIN:
        reason = raw.get("abstain_reason")
        if reason not in ABSTAIN_REASONS:
            return UnusableAnswer("unknown_abstain_reason")
        return Abstention(str(reason))
    if outcome != OUTCOME_EDITS:
        return UnusableAnswer("unknown_outcome")
    edits = raw.get("edits")
    if not isinstance(edits, list) or not edits:
        return UnusableAnswer("no_edits")
    if len(edits) > EDITS_MAX_COUNT or _encoded_size(edits) > EDITS_MAX_BYTES:
        return UnusableAnswer("too_large")
    parsed: list[Edit] = []
    for item in edits:
        edit = _parse_edit(item, allowed_evidence=allowed_evidence, aliases=aliases)
        if isinstance(edit, UnusableAnswer):
            return edit
        parsed.append(edit)
    return ProposedEdits(tuple(parsed))


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8", "surrogatepass"))


def _parse_edit(
    item: object, *, allowed_evidence: Collection[str], aliases: AliasBook | None
) -> Edit | UnusableAnswer:
    if not isinstance(item, Mapping):
        return UnusableAnswer("edit_shape")
    keys = set(item)
    if keys & set(DISPLAY_FACT_KEYS):
        return UnusableAnswer("display_fact_key")
    operations = sorted(keys & FILE_OPERATION_KEYS)
    if keys - EDIT_KEYS - FILE_OPERATION_KEYS or not keys >= EDIT_KEYS:
        return UnusableAnswer("edit_shape")
    file_ref, old_text, new_text, why = (
        item["file_ref"],
        item["old_text"],
        item["new_text"],
        item["why"],
    )
    evidence = item["evidence_ids"]
    if not all(isinstance(value, str) for value in (file_ref, old_text, new_text, why)):
        return UnusableAnswer("edit_shape")
    if not isinstance(evidence, list) or not all(isinstance(each, str) for each in evidence):
        return UnusableAnswer("edit_shape")
    if new_text == old_text:
        return UnusableAnswer("unchanged_edit")
    if _text_problem(new_text) or _text_problem(old_text):
        return UnusableAnswer("control_character")
    if not evidence_ids_known(evidence, allowed_evidence):
        return UnusableAnswer("unknown_evidence")
    if check_why(why, aliases).refusal is not None:
        return UnusableAnswer("why_refused")
    return Edit(
        file_ref=file_ref,
        old_text=old_text,
        new_text=new_text,
        why=why,
        evidence_ids=tuple(evidence),
        file_operation=operations[0] if operations else None,
    )


# --------------------------------------------------------------- the rules

Rule = Literal["K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K10"]

# K5's caps (starting guesses, spec FR17).
MAX_FILES: Final = 5
MAX_CHANGED_LINES: Final = 60
MAX_ADDED_BYTES: Final = 16 * 1024
# K8 (guesses): a run this long of base64-like or hex text, this random.
OPAQUE_RUN_MIN: Final = 40
OPAQUE_MIN_BITS_PER_CHARACTER: Final = 4.0
# Hex has 16 symbols, so its randomness cannot reach 4.0 bits: the design's one
# threshold would let every hex value through. Hex runs use this instead (a guess).
OPAQUE_HEX_MIN_BITS_PER_CHARACTER: Final = 3.0
OPAQUE_WORD_MAX_LETTERS: Final = 20
_OPAQUE_RUN: Final = re.compile(rf"[A-Za-z0-9+/=_-]{{{OPAQUE_RUN_MIN},}}")
_HEX: Final = re.compile(r"[0-9A-Fa-f]+")
_JOINERS: Final = re.compile(r"[_/-]")
# A joined word: letters, then at most two digits (`v1`, `utf8`).
_JOINED_WORD: Final = re.compile(r"([A-Za-z]+)[0-9]{0,2}")
_CAMEL_HUMPS: Final = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+")
# K10 (guesses).
MAX_ADDED_LINE_CHARACTERS: Final = 200
_INNER_BLANK_RUN: Final = re.compile(r"\S[ \t]{20,}")


@dataclass(frozen=True, slots=True)
class PackFile:
    """A file as the pack holds it: the whole text that was read, and where."""

    ref: str
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class Refusal:
    rule: Rule
    file_ref: str
    # The new file's line, for a rule on an added line.
    line: int | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ChangedFile:
    ref: str
    path: str
    old_content: str
    new_content: str
    spans: tuple[Span, ...]
    changes: tuple[LineChange, ...]

    def added_lines(self) -> list[tuple[int, str]]:
        """Each added line with its 1-based line in the new file, ending stripped."""
        found, delta = [], 0
        for change in self.changes:
            first = change.old_index + delta + 1
            found += [(first + n, _bare(line)) for n, line in enumerate(change.added)]
            delta += len(change.added) - len(change.removed)
        return found

    def removed_lines(self) -> list[tuple[int, str]]:
        return [
            (change.old_index + n + 1, _bare(line))
            for change in self.changes
            for n, line in enumerate(change.removed)
        ]


@dataclass(frozen=True, slots=True)
class RulesResult:
    refusals: tuple[Refusal, ...]
    files: tuple[ChangedFile, ...] = field(default=())

    @property
    def refused(self) -> bool:
        return bool(self.refusals)


def _bare(line: str) -> str:
    return line.removesuffix("\n").removesuffix("\r")


def _inside(path: str, folder: str) -> bool:
    folder = folder.strip("/")
    return not folder or path == folder or path.startswith(folder + "/")


def _occurrences(content: str, text: str) -> list[int]:
    """Every place `text` starts, overlapping ones included."""
    places, start = [], content.find(text)
    while start != -1:
        places.append(start)
        start = content.find(text, start + 1)
    return places


def run_rules(
    edits: Sequence[Edit],
    pack: Mapping[str, PackFile],
    *,
    folder: str,
    owner: OwnerLists = NO_OWNER_LISTS,
) -> RulesResult:
    """K1 to K10 over a parsed answer. Refused if any refusal is returned (FR17)."""
    refusals: list[Refusal] = []
    spans_by_ref: dict[str, list[Span]] = {}
    for edit in edits:
        file = pack.get(edit.file_ref)
        if file is None or not _FILE_REF.fullmatch(edit.file_ref):
            refusals.append(Refusal("K1", edit.file_ref, detail="not_in_pack"))
            continue
        if not _inside(file.path, folder):
            refusals.append(Refusal("K1", edit.file_ref, detail="outside_folder"))
            continue
        if is_never_changed(file.path, owner):
            refusals.append(Refusal("K2", edit.file_ref, detail="never_changed"))
            continue
        if edit.file_operation is not None:
            refusals.append(Refusal("K4", edit.file_ref, detail=edit.file_operation))
            continue
        places = _occurrences(file.content, edit.old_text) if edit.old_text else []
        if len(places) != 1:
            detail = "not_found" if not places else "not_unique"
            refusals.append(Refusal("K3", edit.file_ref, detail=detail))
            continue
        span = Span(places[0], places[0] + len(edit.old_text), edit.new_text)
        same_file = spans_by_ref.setdefault(edit.file_ref, [])
        if any(span.start < other.end and other.start < span.end for other in same_file):
            refusals.append(Refusal("K3", edit.file_ref, detail="overlapping_edits"))
            continue
        same_file.append(span)
    if refusals:
        return RulesResult(tuple(refusals))
    files = tuple(_changed_file(pack[ref], spans) for ref, spans in spans_by_ref.items())
    refusals += _file_rules(files)
    refusals += _size_rule(files)
    for changed in files:
        refusals += _line_rules(changed)
    return RulesResult(tuple(refusals), files)


def _changed_file(file: PackFile, spans: list[Span]) -> ChangedFile:
    ordered = tuple(sorted(spans, key=lambda each: each.start))
    return ChangedFile(
        ref=file.ref,
        path=file.path,
        old_content=file.content,
        new_content=apply_spans(file.content, ordered),
        spans=ordered,
        changes=tuple(line_changes(file.content, ordered)),
    )


def _file_rules(files: Sequence[ChangedFile]) -> list[Refusal]:
    """K4: edits to existing files only, so a change may not empty a file."""
    return [
        Refusal("K4", changed.ref, detail="empties_file")
        for changed in files
        if changed.old_content and not changed.new_content
    ]


def _size_rule(files: Sequence[ChangedFile]) -> list[Refusal]:
    """K5: files, changed lines (added and removed) and bytes added."""
    changed_lines = sum(len(c.added_lines()) + len(c.removed_lines()) for c in files)
    added_bytes = sum(len(line.encode("utf-8")) for c in files for _, line in c.added_lines())
    over = [
        name
        for name, value, cap in (
            ("files", len(files), MAX_FILES),
            ("lines", changed_lines, MAX_CHANGED_LINES),
            ("bytes", added_bytes, MAX_ADDED_BYTES),
        )
        if value > cap
    ]
    return [Refusal("K5", files[0].ref, detail=name) for name in over]


def _line_rules(changed: ChangedFile) -> list[Refusal]:
    refusals = []
    known = addresses_in_file(changed.old_content)
    for line_number, line in changed.added_lines():
        for rule, detail in added_line_breaks(line, known):
            refusals.append(Refusal(rule, changed.ref, line=line_number, detail=detail))
    return refusals


def addresses_in_file(content: str) -> frozenset[str]:
    """K6's "already in that file": each whole address the file held, read anywhere in it,
    and compared case-insensitively, never as part of a longer one (`devil.com` does not
    hold `evil.com`)."""
    return frozenset(
        address.casefold()
        for line in content.split("\n")
        for address in addresses_in(line, hosts_anywhere=True)
    )


def added_line_breaks(line: str, known_addresses: frozenset[str]) -> list[tuple[Rule, str]]:
    """K6 to K10 on one added line, with the file before the change. T6.3's replay over
    real commit history (`tools/ai-eval/fix/replay_rules.py`) runs this same code."""
    found: list[tuple[Rule, str]] = []
    new_addresses = [
        a for a in addresses_in(line, hosts_anywhere=False) if a.casefold() not in known_addresses
    ]
    if new_addresses:
        found.append(("K6", "new_address"))
    return found + line_breaks_without_file(line)


def line_breaks_without_file(line: str) -> list[tuple[Rule, str]]:
    """K7 to K10 on one added line: the rules that need nothing but the line, so a read of a
    stored suggestion, which has no file, runs them again (design, *The flow*, step 6)."""
    found: list[tuple[Rule, str]] = []
    if hidden_characters(line):
        found.append(("K7", "hidden_character"))
    if mixed_script_words(line):
        found.append(("K7", "mixed_script"))
    if opaque_runs(line):
        found.append(("K8", "opaque_string"))
    secret = secret_in(line)
    if secret is not None:
        found.append(("K9", secret))
    if len(line) > MAX_ADDED_LINE_CHARACTERS:
        found.append(("K10", "long_line"))
    if _INNER_BLANK_RUN.search(line):
        found.append(("K10", "blank_run"))
    return found


def _is_joined_words(run: str) -> bool:
    """Identifiers and words joined by `_`, `-` or `/`: each word letters (camel humps of
    at most 20 letters each), then at most two digits."""
    for part in _JOINERS.split(run):
        if not part:
            continue
        word = _JOINED_WORD.fullmatch(part)
        if word is None:
            return False
        humps = _CAMEL_HUMPS.findall(word.group(1))
        if "".join(humps) != word.group(1) or any(len(h) > OPAQUE_WORD_MAX_LETTERS for h in humps):
            return False
    return True


def opaque_runs(line: str) -> list[str]:
    """K8's long opaque strings in a line, outside any URL (K6 already judges those)."""
    found = []
    for match in _OPAQUE_RUN.finditer(without_urls(line)):
        run = match.group(0)
        if _is_joined_words(run):
            continue
        threshold = (
            OPAQUE_HEX_MIN_BITS_PER_CHARACTER
            if _HEX.fullmatch(run)
            else OPAQUE_MIN_BITS_PER_CHARACTER
        )
        if shannon_bits_per_character(run) >= threshold:
            found.append(run)
    return found


# --------------------------------------------------------------- the flags

FlagKind = Literal[
    "new_import", "risky_call", "comment", "removed_check", "test_file", "new_letters"
]

# Where the import, call and comment lists apply. For any other language, "What was not
# checked" says these lists do not cover it (FR19).
_PYTHON: Final = frozenset({".py"})
_JAVASCRIPT: Final = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"})
_GO: Final = frozenset({".go"})
_JAVA: Final = frozenset({".java"})
FLAG_LIST_EXTENSIONS: Final = _PYTHON | _JAVASCRIPT | _GO | _JAVA
_HASH_COMMENT_EXTENSIONS: Final = _PYTHON | frozenset({".rb", ".ex", ".exs"})

_IMPORT: Final = re.compile(
    r"^\s*(?:import\b|from\s+\S+\s+import\b|#\s*include\b|use\s|using\s)|\brequire\s*\("
)
# A line inside a Go `import ( … )` block is a quoted path alone.
_GO_IMPORT_LINE: Final = re.compile(r'^\s*(?:\w+\s+)?"[\w./-]+"\s*$')
RISKY_CALLS: Final = (
    r"\brequests\.",
    r"\bhttpx\.",
    r"\burllib",
    r"\bfetch\s*\(",
    r"\baxios\b",
    r"\bhttp\.(?:Get|Post|Head|NewRequest)\b",
    r"\bHttpClient\b",
    r"\bexec\w*\s*\(",
    r"\beval\s*\(",
    r"\bsubprocess\b",
    r"\bos\.system\b",
    r"\bos\.popen\b",
    r"\bchild_process\b",
    r"\bRuntime\.getRuntime\b",
    r"\bProcessBuilder\b",
    r"\bexec\.Command\b",
    r"\bFunction\s*\(",
    r"\bpickle\.loads?\b",
    r"\bsocket\.",
    r"\b__import__\s*\(",
    r"\bimportlib\b",
)
_RISKY_CALL: Final = re.compile("|".join(RISKY_CALLS))
_CHECK_WORDS: Final = re.compile(r"\b(?:if|assert|raise|throw)\b")
_TEST_FOLDERS: Final = frozenset({"test", "tests", "__tests__"})
_TEST_NAME: Final = re.compile(r"(?:.+_test|.+\.test|.+\.spec|test_.+)\.[^.]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Flag:
    """A shown mark beside the diff: its kind, file and line, and which side (FR18)."""

    kind: FlagKind
    ref: str
    path: str
    line: int | None
    side: Literal["new", "old", "file"]


def _extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].casefold() if "." in name else ""


def flag_lists_cover(path: str) -> bool:
    return _extension(path) in FLAG_LIST_EXTENSIONS


def is_test_file(path: str) -> bool:
    """Under `test`, `tests` or `__tests__`, or named `*_test.*`, `*.test.*`, `*.spec.*`,
    or Python's `test_*` (the design's list, with the last added as Python's default)."""
    parts = path.casefold().split("/")
    return bool(_TEST_FOLDERS & set(parts[:-1])) or bool(_TEST_NAME.fullmatch(parts[-1]))


def _code_outside_strings(line: str) -> str:
    """The line with string literals' insides blanked, so a `#` in a string is not a comment."""
    out, quote, escaped = [], None, False
    for character in line:
        if quote is None:
            out.append(character)
            if character in "\"'`":
                quote = character
        elif escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == quote:
            out.append(character)
            quote = None
    return "".join(out)


def _has_comment(line: str, path: str) -> bool:
    code = _code_outside_strings(line)
    stripped = code.lstrip()
    if _extension(path) in _HASH_COMMENT_EXTENSIONS:
        return "#" in code
    return "//" in code or "/*" in code or stripped.startswith("*")


def flags(files: Sequence[ChangedFile]) -> list[Flag]:
    """FR18's flags over passing edits. Each names its file and line."""
    found: list[Flag] = []
    for changed in files:
        added = changed.added_lines()
        letters = {
            number
            for number, line in added
            if any(not c.isascii() and c.isalpha() and c not in changed.old_content for c in line)
        }
        found += line_flags(
            changed.ref, changed.path, added, changed.removed_lines(), new_letter_lines=letters
        )
    return found


def line_flags(
    ref: str,
    path: str,
    added: Sequence[tuple[int, str]],
    removed: Sequence[tuple[int, str]],
    *,
    new_letter_lines: Collection[int],
) -> list[Flag]:
    """FR18's flags from one file's numbered added and removed lines. `new_letter_lines`
    are the added lines holding letters new to the file: worked out from the file by the
    check run, or read from a stored suggestion's display facts (D53)."""
    found: list[Flag] = []
    covered = flag_lists_cover(path)
    is_go = _extension(path) in _GO
    if is_test_file(path):
        found.append(Flag("test_file", ref, path, None, "file"))
    for number, line in added:
        if covered and (_IMPORT.search(line) or (is_go and _GO_IMPORT_LINE.match(line))):
            found.append(Flag("new_import", ref, path, number, "new"))
        if covered and _RISKY_CALL.search(_code_outside_strings(line)):
            found.append(Flag("risky_call", ref, path, number, "new"))
        if covered and _has_comment(line, path):
            found.append(Flag("comment", ref, path, number, "new"))
        if number in new_letter_lines:
            found.append(Flag("new_letters", ref, path, number, "new"))
    for number, line in removed:
        if _CHECK_WORDS.search(_code_outside_strings(line)):
            found.append(Flag("removed_check", ref, path, number, "old"))
    return found


def uncovered_languages(files: Sequence[ChangedFile]) -> list[str]:
    """Paths whose language the import, call and comment lists do not cover (FR19)."""
    return [changed.path for changed in files if not flag_lists_cover(changed.path)]
