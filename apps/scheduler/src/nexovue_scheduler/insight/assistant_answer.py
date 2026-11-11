"""F4: the answer's shape and its check, in the worker, which alone holds what was read
(design, section 4; plan T2.9).

In order: **shape**; **citations** (a sentence citing nothing that ran is removed, and
counted); **characters and ways out** in the model's own words, `not_verified` included;
**commands**; **copying** (a long exact run of a tool result must be quoted by reference);
then each sentence's **label**, which the model has no field to claim. A failure of shape,
ways out, commands or copying is sent back once naming the reason, then withheld.

Tool arguments are model output too: `display_argument` decides whether a free-text phrase
may be shown on a step.

The word lists, `URL` and `EMAIL` are F3's (`nexovue_platform.ai.validate`), extended here
with chat's own words only. **These are heuristics** (way-out and command words above all),
and the injection bed measures them; they are not a guarantee. Quotes and figure flags
come in plan Step 3.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from nexovue_platform.ai.untrusted import strip_hidden
from nexovue_platform.ai.validate import (
    CAUSAL_PHRASES,
    COMMAND_WORDS,
    EMAIL,
    ORIGIN_WORDS,
    URL,
    has_any,
    opens_with_command,
)
from nexovue_platform.assistant_records import ABSTAIN_REASONS, AbstainReason, RejectionReason
from nexovue_platform.assistant_tools import MAX_DISPLAYED_ARGUMENT, STRICT_NAME, ChatTool

MAX_SENTENCES: Final = 12
MAX_SENTENCE_CHARACTERS: Final = 600
MAX_NOT_VERIFIED: Final = 5
MAX_NOT_VERIFIED_CHARACTERS: Final = 200
# A run this long shared with a tool result is copying (a guess, design section 4).
COPY_RUN_CHARACTERS: Final = 40

# `counted`: Counted from telemetry. `checked`: nexovue's own check. `text`: your telemetry
# says this. `unchecked`: the model's wording. The panel holds the words (design, *Words*).
Label = Literal["counted", "checked", "text", "unchecked"]

# ---------------------------------------------------------------- ways out (check 3)
# F3's `URL` and `EMAIL` (validate.py), plus what chat's answers may also carry: Markdown,
# HTML and `data:`, and a host-like word with an optional port, allowed only when a cited
# row named it exactly (design, section 4).
_MARKDOWN_LINK: Final = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
_HTML_TAG: Final = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)
_DATA_URI: Final = re.compile(r"\bdata:", re.IGNORECASE)
# A word with a dot and a letters-only last part: `evil.example`, `api.stripe.com`.
_HOST_LIKE: Final = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d{1,5})?\b", re.IGNORECASE)
# Line and paragraph separators, beside F3's hidden characters: they move what a reader sees.
_SEPARATOR_CATEGORIES: Final = frozenset({"Zl", "Zp"})

# ---------------------------------------------------------------- commands (check 4)
# F3's FR37 command verbs, extended (design, check 7): chat's own, and each after "please".
_CHAT_COMMAND_WORDS: Final = ("paste", "truncate", "uninstall")
_IMPERATIVES: Final = (
    *COMMAND_WORDS,
    *_CHAT_COMMAND_WORDS,
    *(f"please {word}" for word in (*COMMAND_WORDS, *_CHAT_COMMAND_WORDS)),
)
_SHELL_WORDS: Final = (
    r"curl|wget|sudo|rm|kubectl|docker|chmod|chown|systemctl|bash|sh|psql|helm|ssh|scp|nc"
)
_COMMAND_LINE: Final = re.compile(
    rf"(?:^|[\s`(])(?:{_SHELL_WORDS})\s+(?:-|\S*/|(?:restart|rm|exec|run|kill|stop|apply|"
    r"delete|rollout|scale|drop)\b)",
    re.IGNORECASE,
)
_PIPE_TO_SHELL: Final = re.compile(r"\|\s*(?:sudo\s+)?(?:ba|z)?sh\b|&&|;\s*rm\b")
_BACKTICK_SHELL: Final = re.compile(rf"`[^`]*\b(?:{_SHELL_WORDS})\b[^`]*`", re.IGNORECASE)

# ---------------------------------------------------------------- cause words (label)
# F3's FR37 causal phrases, extended with chat's own; origin words also cost a fact label.
_CHAT_CAUSE_WORDS: Final = ("cause", "stems from", "stem from", "thanks to", "originated")
_CAUSE_PHRASES: Final = (*CAUSAL_PHRASES, *_CHAT_CAUSE_WORDS)
_NUMBER: Final = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])-?\d+(?:\.\d+)?")


class AnswerRejected(Exception):  # noqa: N818 — a rejection, named for the reader
    def __init__(self, reason: RejectionReason, detail: str) -> None:
        super().__init__(detail)
        self.reason: RejectionReason = reason


@dataclass(frozen=True, slots=True)
class StepRead:
    """What one step sent the model: the rows, after the cut, and the tool's field rules."""

    key: str
    tool: ChatTool
    rows: Sequence[Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class Abstained:
    reason: AbstainReason


@dataclass(frozen=True, slots=True)
class CheckedAnswer:
    sentences: list[dict[str, Any]]
    removed_sentence_count: int
    not_verified: list[str] = field(default_factory=list)

    def stored(self) -> dict[str, Any]:
        return {
            "sentences": self.sentences,
            "removed_sentence_count": self.removed_sentence_count,
            "not_verified": self.not_verified,
        }


def _invisible(text: str) -> bool:
    """F3's hidden characters (`untrusted.strip_hidden`) or a line separator, so a
    zero-width character cannot split `https://` past the checks below."""
    return strip_hidden(text) != text or any(
        unicodedata.category(character) in _SEPARATOR_CATEGORIES for character in text
    )


def way_out(text: str, *, allowed_names: frozenset[str] = frozenset()) -> str | None:
    """Why `text` could take a reader somewhere, or None. A host-like word passes only when it
    is a strict name a cited row named exactly (a service map's `api.stripe.com`)."""
    if _invisible(text):
        return "an invisible or direction character"
    for pattern, why in (
        (URL, "an address"),
        (EMAIL, "an email address"),
        (_MARKDOWN_LINK, "a Markdown link or image"),
        (_HTML_TAG, "an HTML tag"),
        (_DATA_URI, "a data address"),
    ):
        if pattern.search(text):
            return why
    for host in _HOST_LIKE.findall(text):
        if host not in allowed_names or STRICT_NAME.fullmatch(host) is None:
            return "a host name"
    return None


def command(text: str) -> str | None:
    """Why `text` reads as telling a person to run something, or None."""
    if opens_with_command(text, _IMPERATIVES):
        return "an instruction to act"
    if _COMMAND_LINE.search(text) or _PIPE_TO_SHELL.search(text) or _BACKTICK_SHELL.search(text):
        return "a command"
    return None


def display_argument(value: str) -> str | None:
    """A free-text tool argument as a step may show it, or None to hide it (FR14, AC62)."""
    if len(value) > MAX_DISPLAYED_ARGUMENT or way_out(value) or command(value):
        return None
    return value


# ---------------------------------------------------------------- parsing


def _shape(text: str | None) -> dict[str, Any]:
    try:
        document = json.loads(text or "")
    except ValueError as error:
        raise AnswerRejected("shape", "The answer was not one JSON object.") from error
    if not isinstance(document, dict):
        raise AnswerRejected("shape", "The answer was not one JSON object.")
    sentences = document.get("sentences", [])
    not_verified = document.get("not_verified", [])
    abstained = document.get("abstained")
    if (
        not isinstance(sentences, list)
        or len(sentences) > MAX_SENTENCES
        or not isinstance(not_verified, list)
        or len(not_verified) > MAX_NOT_VERIFIED
        or not all(
            isinstance(item, str) and len(item) <= MAX_NOT_VERIFIED_CHARACTERS
            for item in not_verified
        )
        or not (abstained is None or abstained in ABSTAIN_REASONS)
    ):
        raise AnswerRejected("shape", "The answer did not fit the shape asked for.")
    for sentence in sentences:
        if (
            not isinstance(sentence, dict)
            or not isinstance(sentence.get("text"), str)
            or not sentence["text"].strip()
            or len(sentence["text"]) > MAX_SENTENCE_CHARACTERS
            or not isinstance(sentence.get("cites", []), list)
        ):
            raise AnswerRejected("shape", "A sentence did not fit the shape asked for.")
    return document


@dataclass(frozen=True, slots=True)
class _Cited:
    step: StepRead
    rows: tuple[int, ...]
    fields: tuple[str, ...]


def _citations(raw: object, steps: Mapping[str, StepRead]) -> list[_Cited]:
    """The citations naming a step that ran and returned, with rows and fields that exist."""
    found: list[_Cited] = []
    if not isinstance(raw, list):
        return found
    for cite in raw:
        if not isinstance(cite, dict) or cite.get("step") not in steps:
            continue
        step = steps[cite["step"]]
        rows = cite.get("rows")
        fields = cite.get("fields")
        row_list = (
            tuple(range(len(step.rows)))
            if rows is None
            else tuple(r for r in rows if isinstance(r, int) and 0 <= r < len(step.rows))
            if isinstance(rows, list)
            else ()
        )
        field_list = (
            tuple(step.tool.fields)
            if fields is None
            else tuple(f for f in fields if isinstance(f, str) and f in step.tool.fields)
            if isinstance(fields, list)
            else ()
        )
        if row_list and field_list:
            found.append(_Cited(step, row_list, field_list))
    return found


def _values(cited: list[_Cited], kinds: frozenset[str]) -> list[Any]:
    return [
        cite.step.rows[row].get(name)
        for cite in cited
        for row in cite.rows
        for name in cite.fields
        if cite.step.tool.fields[name].kind in kinds
    ]


def _number_matches(shown: str, values: list[Any]) -> bool:
    """Rounded to the digits shown, as a value or as a percentage of a fraction."""
    number = float(shown.replace(",", ""))
    decimals = len(shown.split(".")[1]) if "." in shown else 0
    tolerance = 0.5 * 10**-decimals + 1e-9
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int | float) or math.isnan(value):
            continue
        for candidate in (float(value), float(value) * 100):
            if abs(round(candidate, decimals) - number) <= tolerance:
                return True
    return False


def _label(text: str, cited: list[_Cited]) -> Label:
    """Check 7: a citation alone earns nothing (design, section 4)."""
    kinds = {cite.step.tool.fields[name].kind for cite in cited for name in cite.fields}
    if kinds & {"text", "team_text"}:
        return "text"
    numbers = _NUMBER.findall(text)
    names = {value for value in _values(cited, frozenset({"name"})) if isinstance(value, str)}
    named = [name for name in names if name and name in text]
    if (
        not (numbers or named)
        or has_any(text, _CAUSE_PHRASES)
        or has_any(text, ORIGIN_WORDS)
        or command(text)
    ):
        return "unchecked"
    measured = _values(cited, frozenset({"number"}))
    if not all(_number_matches(shown, measured) for shown in numbers):
        return "unchecked"
    bases = {
        cite.step.tool.fields[name].basis
        for cite in cited
        for name in cite.fields
        if cite.step.tool.fields[name].kind in ("number", "name", "closed", "time", "id")
    }
    # Telemetry anywhere among the cited facts makes it "counted"; nexovue's own alone, "checked".
    return "counted" if "telemetry" in bases else "checked"


def _copied(text: str, results: Sequence[str]) -> bool:
    if len(text) < COPY_RUN_CHARACTERS:
        return False
    runs = {text[i : i + COPY_RUN_CHARACTERS] for i in range(len(text) - COPY_RUN_CHARACTERS + 1)}
    return any(run in result for result in results for run in runs)


def check_answer(
    text: str | None, steps: Mapping[str, StepRead], *, results: Sequence[str]
) -> CheckedAnswer | Abstained:
    """The stored answer, or `AnswerRejected` with the reason to send back once.

    `results` are the tool results as the model read them, for the copying check."""
    document = _shape(text)
    if document.get("abstained") is not None:
        reason: AbstainReason = document["abstained"]
        return Abstained(reason)
    for item in document.get("not_verified", []):
        if why := way_out(item):
            raise AnswerRejected("way_out", f"A 'not verified' line held {why}.")
        if why := command(item):
            raise AnswerRejected("command", f"A 'not verified' line held {why}.")
    kept: list[dict[str, Any]] = []
    removed = 0
    for sentence in document.get("sentences", []):
        cited = _citations(sentence.get("cites"), steps)
        if not cited:
            removed += 1
            continue
        words: str = sentence["text"].strip()
        allowed = frozenset(
            value
            for value in _values(cited, frozenset({"name"}))
            if isinstance(value, str) and STRICT_NAME.fullmatch(value)
        )
        if why := way_out(words, allowed_names=allowed):
            raise AnswerRejected("way_out", f"A sentence held {why}. Write none.")
        if why := command(words):
            raise AnswerRejected("command", f"A sentence held {why}. Tell no one to run anything.")
        if _copied(words, results):
            raise AnswerRejected(
                "copying", "A sentence copied a tool result. Describe it; do not copy it."
            )
        kept.append(
            {
                "text": words,
                "label": _label(words, cited),
                "cause_warning": has_any(words, _CAUSE_PHRASES),
                "cites": sorted({cite.step.key for cite in cited}),
            }
        )
    if not kept:
        raise AnswerRejected("citations", "No sentence cited a step that ran and returned.")
    return CheckedAnswer(kept, removed, list(document.get("not_verified", [])))
