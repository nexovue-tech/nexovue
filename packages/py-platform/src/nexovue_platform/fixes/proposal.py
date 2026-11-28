"""What the AI job makes of the model's answer to the files it sent (spec FR15 to FR17, FR15b;
design, *What the model gets, and returns*, *Display facts (D53)*; plan T4.4, T4.5, T4.13).

Pure, in the shared package, so the rules the job runs are the same code the API, the
sweep and the check run run again (D18). It imports no `nexovue_platform.ai`: the job
hands it the answer's text.

1. **Read the text** as one JSON object; anything else is an unusable answer.
2. **`needs_files`** (rounds 1 and 2 only): at most 3 requests, each naming a file in the
   pack and a line within the lines sent, a plain relative path, and a reason that passes
   FR16's prose checks. In the last round such an answer is read as `not_in_these_files`.
3. **Edits or an abstention:** the schema guards of `checks.parse_answer`, then K1 to K10
   on the pack's whole files.
4. **The verdict:** a misquote (K3) or an edit on a never-changed file (K2) gets the one
   retry, by its fixed code; any other rule withholds at once, storing only the rule and
   the pack references (never the text). A passing answer is stored with the display
   facts Nexovue's own code computes from the whole files (D53), never the model's.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes.checks import (
    Abstention,
    Edit,
    PackFile,
    ProposedEdits,
    Refusal,
    Rule,
    UnusableAnswer,
    parse_answer,
    run_rules,
)
from nexovue_platform.fixes.diff import Span, display_facts
from nexovue_platform.fixes.policy import NO_OWNER_LISTS, OwnerLists
from nexovue_platform.fixes.prose import prose_refusal
from nexovue_platform.fixes.records import (
    EDIT_DID_NOT_APPLY,
    INVALID_OUTPUT,
    MAX_ASKED_PATHS,
    NOT_IN_THESE_FILES,
    RETRY_SOURCE_MISQUOTE,
    RETRY_SOURCE_NEVER_CHANGED,
    RETRY_SOURCE_SHAPE,
)
from nexovue_platform.fixes.source_prompt import (
    ASKED_PATH_MAX_CHARACTERS,
    ASKED_REASON_MAX_CHARACTERS,
    OUTCOME_NEEDS_FILES,
    SentFile,
)

# A relative path a person could have typed: no leading slash, no `..`, no control
# character, no backslash (the pack job checks the tree again, T3.19).
_PLAIN_PATH: Final = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\x00-\x1f\x7f\\]+$")
_REQUEST_KEYS: Final = frozenset({"path", "because", "reason"})
_BECAUSE_KEYS: Final = frozenset({"file_ref", "line"})
# Rules whose refusal earns the one retry, with its fixed code (design, *Why edits*).
_RETRIED: Final[dict[Rule, str]] = {
    "K3": RETRY_SOURCE_MISQUOTE,
    "K2": RETRY_SOURCE_NEVER_CHANGED,
}


@dataclass(frozen=True, slots=True)
class AskedFile:
    """One name the model asked for, with the shown line that needs it and its reason."""

    path: str
    ref: str
    line: int
    reason: str

    def stored(self) -> dict[str, Any]:
        return {"path": self.path, "ref": self.ref, "line": self.line, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class NeedsFiles:
    asked: tuple[AskedFile, ...]


@dataclass(frozen=True, slots=True)
class Proposed:
    """A passing answer, as stored: each edit with its display facts (D53)."""

    edits: tuple[dict[str, Any], ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Withheld:
    """A refusal: the reason stored, and the pack references `fix.withheld` resolves."""

    reason: str
    refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Abstained:
    reason: str


@dataclass(frozen=True, slots=True)
class RetryWith:
    """The one retry (D51), by its fixed code; `final` is what the row stores when no
    attempt is left."""

    code: str
    final: str


Verdict = NeedsFiles | Proposed | Withheld | Abstained | RetryWith


def judge(
    text: str,
    *,
    pack: Mapping[str, PackFile],
    sent: Sequence[SentFile],
    folder: str | None,
    owner: OwnerLists = NO_OWNER_LISTS,
    allowed_evidence: Collection[str],
    aliases: AliasBook | None,
    may_ask: bool,
) -> Verdict:
    """The model's answer, read and checked. Nothing here trusts a word of it."""
    try:
        raw = json.loads(text)
    except ValueError:
        return RetryWith(RETRY_SOURCE_SHAPE, INVALID_OUTPUT)
    if isinstance(raw, Mapping) and raw.get("outcome") == OUTCOME_NEEDS_FILES:
        if not may_ask:
            return Abstained(NOT_IN_THESE_FILES)
        return _needs_files(raw, sent=sent, aliases=aliases)
    parsed = parse_answer(
        raw,
        allowed_evidence=allowed_evidence,
        aliases=aliases,
    )
    if isinstance(parsed, UnusableAnswer):
        return RetryWith(RETRY_SOURCE_SHAPE, INVALID_OUTPUT)
    if isinstance(parsed, Abstention):
        return Abstained(parsed.reason)
    return _ruled(parsed, pack=pack, folder=folder or "", owner=owner)


def _needs_files(
    raw: Mapping[str, Any], *, sent: Sequence[SentFile], aliases: AliasBook | None
) -> NeedsFiles | RetryWith:
    requests = raw.get("requests")
    if set(raw) - {"outcome", "requests", "abstain_reason", "edits"} or raw.get("edits"):
        return RetryWith(RETRY_SOURCE_SHAPE, INVALID_OUTPUT)
    if not isinstance(requests, list) or not 0 < len(requests) <= MAX_ASKED_PATHS:
        return RetryWith(RETRY_SOURCE_SHAPE, INVALID_OUTPUT)
    windows = {each.ref: (each.first_line, each.last_line) for each in sent}
    asked: list[AskedFile] = []
    for request in requests:
        found = _asked(request, windows=windows, aliases=aliases)
        if found is None:
            return RetryWith(RETRY_SOURCE_SHAPE, INVALID_OUTPUT)
        if found.path not in {each.path for each in asked}:
            asked.append(found)
    return NeedsFiles(tuple(asked))


def _asked(
    request: object, *, windows: Mapping[str, tuple[int, int]], aliases: AliasBook | None
) -> AskedFile | None:
    if not isinstance(request, Mapping) or set(request) != _REQUEST_KEYS:
        return None
    path, because, reason = request["path"], request["because"], request["reason"]
    if not isinstance(path, str) or not isinstance(reason, str):
        return None
    if not isinstance(because, Mapping) or set(because) != _BECAUSE_KEYS:
        return None
    ref, line = because["file_ref"], because["line"]
    if not isinstance(ref, str) or ref not in windows:
        return None
    if not isinstance(line, int) or isinstance(line, bool):
        return None
    first, last = windows[ref]
    if not first <= line <= last:
        return None
    path = path.strip()
    if not 0 < len(path) <= ASKED_PATH_MAX_CHARACTERS or not _PLAIN_PATH.match(path):
        return None
    if len(reason) > ASKED_REASON_MAX_CHARACTERS or prose_refusal(reason, aliases) is not None:
        return None
    return AskedFile(path=path, ref=ref, line=line, reason=reason)


def _ruled(
    parsed: ProposedEdits, *, pack: Mapping[str, PackFile], folder: str, owner: OwnerLists
) -> Proposed | Withheld | RetryWith:
    result = run_rules(parsed.edits, pack, folder=folder, owner=owner)
    if result.refused:
        return _refused(result.refusals)
    stored = stored_edits(parsed.edits, pack)
    evidence = tuple(dict.fromkeys(eid for edit in parsed.edits for eid in edit.evidence_ids))
    return Proposed(edits=stored, evidence_ids=evidence)


def _refused(refusals: Sequence[Refusal]) -> Withheld | RetryWith:
    """Any rule other than K2 and K3 withholds at once, by the lowest such rule; otherwise
    the one retry. A misquote's last word is `edit_did_not_apply`, K2's is withheld."""
    rules = sorted({each.rule for each in refusals}, key=lambda rule: int(rule[1:]))
    final_rule = next((rule for rule in rules if rule not in _RETRIED), None)
    if final_rule is not None:
        refs = tuple(dict.fromkeys(r.file_ref for r in refusals if r.rule == final_rule))
        return Withheld(f"withheld_{final_rule}", refs)
    first = rules[0]
    final = f"withheld_{first}" if first == "K2" else EDIT_DID_NOT_APPLY
    return RetryWith(_RETRIED[first], final)


def stored_edits(edits: Sequence[Edit], pack: Mapping[str, PackFile]) -> tuple[dict[str, Any], ...]:
    """Each passing edit with the display facts computed from its whole file (D53). The
    facts come from Nexovue's code alone; the model's answer never carried them (its keys
    would have been an unusable answer)."""
    spans_by_ref: dict[str, list[tuple[int, Span]]] = {}
    for index, edit in enumerate(edits):
        content = pack[edit.file_ref].content
        start = content.find(edit.old_text)
        spans_by_ref.setdefault(edit.file_ref, []).append(
            (index, Span(start, start + len(edit.old_text), edit.new_text))
        )
    facts_of: dict[int, dict[str, Any]] = {}
    for ref, placed in spans_by_ref.items():
        computed = display_facts(pack[ref].content, [span for _, span in placed])
        for (index, _), facts in zip(placed, computed, strict=True):
            facts_of[index] = facts
    return tuple(
        {
            "file_ref": edit.file_ref,
            "old_text": edit.old_text,
            "new_text": edit.new_text,
            "why": edit.why,
            "evidence_ids": list(edit.evidence_ids),
            **facts_of[index],
        }
        for index, edit in enumerate(edits)
    )
