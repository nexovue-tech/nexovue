"""F4: the answer's shape and its check, in the worker, which alone holds what was read
(design, section 4; plan T2.9).

In order: **shape**; **citations** (a sentence citing nothing that ran is removed, and
counted); **characters and ways out** in the model's own words, `not_verified` included;
**commands**; **copying** (a long exact run of a tool result must be quoted by reference);
**quotes** by reference, each folded under a warning when its value reads as a link or a
command, and a sentence telling the reader to run what it quotes removed; **figures**, each
number matched against the cited rows' numbers, its offset flagged when unmatched; then each
sentence's **label**, which the model has no field to claim. A failure of shape, ways out,
commands or copying is sent back once naming the reason, then withheld.

Tool arguments are model output too: `display_argument` decides whether a free-text phrase
may be shown on a step.

The word lists, `URL` and `EMAIL` are F3's (`nexovue_platform.ai.validate`), extended here
with chat's own words only. **These are heuristics** (way-out and command words above all),
and the injection bed measures them; they are not a guarantee. So is figure matching: a
flag is shown, never a refusal, because its false-flag rate is unknown (D10).
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
from nexovue_platform.assistant_tools import (
    KNOWLEDGE_TOOL,
    MAX_DISPLAYED_ARGUMENT,
    STRICT_NAME,
    Basis,
    ChatTool,
    Unit,
)

MAX_SENTENCES: Final = 12
MAX_SENTENCE_CHARACTERS: Final = 600
MAX_NOT_VERIFIED: Final = 5
MAX_NOT_VERIFIED_CHARACTERS: Final = 200
# A run this long shared with a tool result is copying (a guess, design section 4).
COPY_RUN_CHARACTERS: Final = 40
# Quotes an answer may show (FR15, a guess); a further one is dropped and counted (AC87).
MAX_QUOTES: Final = 5

# `counted`: Counted from telemetry. `checked`: nexovue's own check. `text`: your telemetry
# says this. `team`: from your team's notes, with its age (Step 5). `unchecked`: the model's
# wording. The panel holds the words (design, *Words*).
Label = Literal["counted", "checked", "text", "team", "unchecked"]
# The row field a team's text carries its age in (`search_knowledge`'s `written_at`).
WRITTEN_AT: Final = "written_at"
# The knowledge row a runbook's chunk is: the one quote that may be acted on (BR7, D8).
_RUNBOOK_SOURCE: Final = "runbook"
# A teammate's note: the team's words, and never a command (BR7, AC63).
_NOTE_SOURCE: Final = "note"

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
# A date or a time of day is a moment, not a figure: the step's range shows what it read.
_MOMENT: Final = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?)?|\b\d{1,2}:\d{2}(?::\d{2})?\b"
)

# ---------------------------------------------------------------- quotes (check 5)
# A sentence pointing a reader at quoted text to act on it (design, check 5; AC63).
_ACT_ON_QUOTE: Final = re.compile(
    r"\b(?:run|execute|paste|apply|type in|enter)\b|\b(?:the|this) (?:command|line|snippet)"
    r"(?: below| above)?\b",
    re.IGNORECASE,
)


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
    # Quote id to its stored reference (`_Quote.stored`); never the quoted text (D28).
    quotes: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Sentences removed for telling the reader to act on quoted text (check 5).
    removed_for_acting: int = 0
    # Quotes past `MAX_QUOTES`, dropped (AC87).
    dropped_quote_count: int = 0

    def stored(self) -> dict[str, Any]:
        return {
            "sentences": self.sentences,
            "removed_sentence_count": self.removed_sentence_count,
            "removed_for_acting_count": self.removed_for_acting,
            "not_verified": self.not_verified,
            "quotes": self.quotes,
            "dropped_quote_count": self.dropped_quote_count,
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


def command(text: str, *, pointing_at_a_runbook: bool = False) -> str | None:
    """Why `text` reads as telling a person to run something, or None. A sentence pointing at
    a runbook's own quote may tell the reader to run it (BR7); a command written in the
    sentence itself is still refused, since commands come only by reference."""
    if not pointing_at_a_runbook and opens_with_command(text, _IMPERATIVES):
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


def _candidates(value: float, unit: Unit | None) -> tuple[float, ...]:
    """The ways a value may be written: a fraction as a percentage, milliseconds as seconds."""
    if unit == "fraction":
        return (value, value * 100)
    if unit == "ms":
        return (value, value / 1000)
    return (value,)


def _number_matches(shown: str, measured: Sequence[tuple[Any, Unit | None]]) -> bool:
    """Rounded to the digits shown, in any way the field's unit may be written (check 6)."""
    number = float(shown.replace(",", ""))
    decimals = len(shown.split(".")[1]) if "." in shown else 0
    tolerance = 0.5 * 10**-decimals + 1e-9
    for value, unit in measured:
        if isinstance(value, bool) or not isinstance(value, int | float) or math.isnan(value):
            continue
        for candidate in _candidates(float(value), unit):
            if abs(round(candidate, decimals) - number) <= tolerance:
                return True
    return False


def _figures(text: str, names: Sequence[str]) -> list[tuple[int, int, str]]:
    """Each number in a sentence with its offsets, leaving out dates, times of day and the
    digits inside a cited name (`checkout-v2`)."""
    skipped = [match.span() for match in _MOMENT.finditer(text)]
    for name in names:
        start = text.find(name)
        while name and start != -1:
            skipped.append((start, start + len(name)))
            start = text.find(name, start + 1)
    return [
        (match.start(), match.end(), match.group())
        for match in _NUMBER.finditer(text)
        if not any(low <= match.start() < high for low, high in skipped)
    ]


def _cited_fields(cited: list[_Cited]) -> list[tuple[_Cited, int, str]]:
    return [(cite, row, name) for cite in cited for row in cite.rows for name in cite.fields]


def _measured(cited: list[_Cited]) -> list[tuple[Any, Unit | None]]:
    """The cited numbers a figure may match. A sentence citing only sampled lists may match
    only measured values with a unit: a row count or a code is not a count (BR11)."""
    only_sampled = all(cite.step.tool.sampled for cite in cited)
    found: list[tuple[Any, Unit | None]] = []
    for cite, row, name in _cited_fields(cited):
        rule = cite.step.tool.fields[name]
        if rule.kind != "number" or (only_sampled and rule.unit is None):
            continue
        found.append((cite.step.rows[row].get(name), rule.unit))
    return found


def flagged_figures(text: str, cited: list[_Cited]) -> list[list[int]]:
    """The offsets of every figure no cited number matches (FR16): shown, never refused."""
    measured = _measured(cited)
    return [
        [start, end]
        for start, end, shown in _figures(text, _names(cited))
        if not _number_matches(shown, measured)
    ]


def _names(cited: list[_Cited]) -> list[str]:
    found: list[str] = []
    for value in _values(cited, frozenset({"name"})):
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item:
                found.append(item)
    return found


def _bases(cited: list[_Cited]) -> set[Basis]:
    return {
        cite.step.tool.basis_of(name, cite.step.rows[row])
        for cite, row, name in _cited_fields(cited)
    }


def _label(text: str, cited: list[_Cited], flagged: list[list[int]]) -> Label:
    """Check 7: a citation alone earns nothing (design, section 4). Telemetry text is the
    lowest claim, so it wins over the team's own words, which win over a count."""
    kinds = {cite.step.tool.fields[name].kind for cite in cited for name in cite.fields}
    bases = _bases(cited)
    if "text" in kinds or "telemetry_text" in bases:
        return "text"
    if "team_notes" in bases:
        return "team"
    figures = _figures(text, _names(cited))
    named = [name for name in _names(cited) if name in text]
    if (
        not (figures or named)
        or flagged
        or has_any(text, _CAUSE_PHRASES)
        or has_any(text, ORIGIN_WORDS)
        or command(text)
    ):
        return "unchecked"
    # Telemetry anywhere among the cited facts makes it "counted"; nexovue's own alone, "checked".
    return "counted" if "telemetry" in bases else "checked"


def _team_written_at(cited: list[_Cited]) -> str | None:
    """The oldest age among the team's cited rows, which the notes label states (FR17)."""
    found = [
        str(cite.step.rows[row][WRITTEN_AT])
        for cite, row, name in _cited_fields(cited)
        if cite.step.tool.basis_of(name, cite.step.rows[row]) == "team_notes"
        and cite.step.rows[row].get(WRITTEN_AT) is not None
    ]
    return min(found) if found else None


def _copied(text: str, results: Sequence[str]) -> bool:
    if len(text) < COPY_RUN_CHARACTERS:
        return False
    runs = {text[i : i + COPY_RUN_CHARACTERS] for i in range(len(text) - COPY_RUN_CHARACTERS + 1)}
    return any(run in result for result in results for run in runs)


@dataclass(frozen=True, slots=True)
class _Quote:
    """A quote the worker checked: what is stored to re-read it, and whether its value reads
    as a link or a command, so it is shown folded under the warning (FR15, AC63)."""

    step: str
    tool: str
    field: str
    reference: dict[str, Any]
    folded: bool
    # A runbook's own text: never folded, labelled as the team's, and the one quote a
    # sentence may tell the reader to run (BR7, T5.6).
    runbook: bool = False
    # A teammate's note (Step 7): the team's words, yet inert: folded like a log's when it
    # reads as a link or a command, and never a quote a sentence may point at to run (AC63).
    note: bool = False

    def stored(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool": self.tool,
            "field": self.field,
            "reference": self.reference,
            "folded": self.folded,
            "runbook": self.runbook,
            "note": self.note,
        }


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _quotes(raw: object, steps: Mapping[str, StepRead]) -> dict[str, _Quote]:
    """Each reference naming a step that ran, a row it returned and a field that can be
    quoted (check 5). The worker holds the value, so it runs checks 3 and 4 on it here."""
    found: dict[str, _Quote] = {}
    if not isinstance(raw, dict):
        return found
    for quote_id, reference in raw.items():
        if not isinstance(quote_id, str) or not isinstance(reference, dict):
            continue
        step_key = reference.get("step")
        step = steps.get(step_key) if isinstance(step_key, str) else None
        row = reference.get("row")
        name = reference.get("field")
        if (
            step is None
            or not isinstance(row, int)
            or isinstance(row, bool)
            or not 0 <= row < len(step.rows)
            or not isinstance(name, str)
            or not step.tool.quotable(name)
        ):
            continue
        values = step.rows[row]
        if name not in values or any(key not in values for key in step.tool.quote_keys):
            continue
        text = _as_text(values[name])
        knowledge = step.tool.name == KNOWLEDGE_TOOL
        runbook = knowledge and values.get("source_kind") == _RUNBOOK_SOURCE
        found[quote_id] = _Quote(
            step=step.key,
            tool=step.tool.name,
            field=name,
            reference={key: values[key] for key in step.tool.quote_keys},
            folded=not runbook and (way_out(text) is not None or command(text) is not None),
            runbook=runbook,
            note=knowledge and values.get("source_kind") == _NOTE_SOURCE,
        )
    return found


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
    quotes = _quotes(document.get("quotes"), steps)
    shown: dict[str, _Quote] = {}
    dropped_quotes: set[str] = set()
    kept: list[dict[str, Any]] = []
    removed = 0
    removed_for_acting = 0
    for sentence in document.get("sentences", []):
        cited = _citations(sentence.get("cites"), steps)
        if not cited:
            removed += 1
            continue
        words: str = sentence["text"].strip()
        named = _named_quotes(sentence.get("quotes"), quotes)
        runbooks_only = bool(named) and all(quotes[quote_id].runbook for quote_id in named)
        if named and _ACT_ON_QUOTE.search(words) and not runbooks_only:
            # Only a runbook's quote may be acted on; a log's, an error's or a note's never.
            removed_for_acting += 1
            continue
        allowed = frozenset(name for name in _names(cited) if STRICT_NAME.fullmatch(name))
        if why := way_out(words, allowed_names=allowed):
            raise AnswerRejected("way_out", f"A sentence held {why}. Write none.")
        if why := command(words, pointing_at_a_runbook=runbooks_only):
            raise AnswerRejected("command", f"A sentence held {why}. Tell no one to run anything.")
        if _copied(words, results):
            raise AnswerRejected(
                "copying", "A sentence copied a tool result. Describe it; do not copy it."
            )
        sentence_quotes: list[str] = []
        for quote_id in named:
            if quote_id not in shown and len(shown) >= MAX_QUOTES:
                dropped_quotes.add(quote_id)
                continue
            shown[quote_id] = quotes[quote_id]
            sentence_quotes.append(quote_id)
        flagged = flagged_figures(words, cited)
        label = _label(words, cited, flagged)
        kept.append(
            {
                "text": words,
                "label": label,
                "cause_warning": has_any(words, _CAUSE_PHRASES),
                "flagged_figures": flagged,
                "cites": sorted({cite.step.key for cite in cited}),
                "quotes": sentence_quotes,
                **({WRITTEN_AT: _team_written_at(cited)} if label == "team" else {}),
            }
        )
    if not kept:
        raise AnswerRejected("citations", "No sentence cited a step that ran and returned.")
    return CheckedAnswer(
        kept,
        removed,
        list(document.get("not_verified", [])),
        quotes={quote_id: quote.stored() for quote_id, quote in shown.items()},
        removed_for_acting=removed_for_acting,
        dropped_quote_count=len(dropped_quotes),
    )


def _named_quotes(raw: object, quotes: Mapping[str, _Quote]) -> list[str]:
    """The sentence's quote ids that name a checked quote, in order, once each."""
    if not isinstance(raw, list):
        return []
    named: list[str] = []
    for quote_id in raw:
        if isinstance(quote_id, str) and quote_id in quotes and quote_id not in named:
            named.append(quote_id)
    return named
