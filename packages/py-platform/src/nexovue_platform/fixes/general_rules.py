"""Rung 2b's rules, G1 to G9 (spec FR36; design, *Rung 2b*, *The rules, exactly*; plan
T2b.3).

Pure, and run in two rooms: by the AI role when the model answers, and by the API on every
read, because the AI room writes the row (D18). **All or nothing:** the first rule broken
decides, nothing of the answer is shown, and one sentence says why. G1 and G2 may get one
retry (D51); G3 to G9 refuse at once.

Reused, never copied: K7's hidden and look-alike letters and K6's addresses
(`patterns.py`), U4's email and markup and BR11's words (`prose.py`), K8's opaque runs
(`checks.py`), K9's secrets (`secrets.py`), K2's dependency files (`policy.py`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from nexovue_platform.evidence.aliases import ALIAS_TOKEN
from nexovue_platform.fixes.checks import opaque_runs
from nexovue_platform.fixes.general_lists import (
    process_or_dynamic_code,
    setup_step,
    shell_command,
    unlisted_imports,
)
from nexovue_platform.fixes.patterns import addresses_in, is_hidden_character, mixed_script_words
from nexovue_platform.fixes.policy import NEVER_CHANGED_BUILD, SOURCE_EXTENSIONS, matches_any
from nexovue_platform.fixes.prose import BANNED, EMAIL, has_markup
from nexovue_platform.fixes.records import (
    GENERAL_ABSTAIN_REASONS,
    GENERAL_LANGUAGES,
    GENERAL_SUGGESTION_MAX_BYTES,
    RETRY_SHAPE,
    RETRY_TOO_LONG,
    GeneralLanguage,
)
from nexovue_platform.fixes.secrets import secret_in

Rule = Literal["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9"]

OUTCOME_SUGGESTION: Final = "suggestion"
OUTCOME_ABSTAIN: Final = "abstain"
ANSWER_KEYS: Final = frozenset(
    {"outcome", "abstain_reason", "summary", "steps", "example", "evidence_ids"}
)
EXAMPLE_KEYS: Final = frozenset({"language", "code"})

# FR35's caps (guesses).
SUMMARY_MAX_CHARACTERS: Final = 300
STEP_MAX_CHARACTERS: Final = 200
STEPS_MIN: Final = 1
STEPS_MAX: Final = 5
CODE_MAX_LINES: Final = 40
CODE_LINE_MAX_CHARACTERS: Final = 120
CODE_MAX_BYTES: Final = 2 * 1024
ANSWER_MAX_BYTES: Final = GENERAL_SUGGESTION_MAX_BYTES

# G8: wording that claims to have read the customer's code.
READ_YOUR_CODE: Final = re.compile(
    r"\byour\s+(?:code|file|files|repository|repo|function|codebase)\b|\bin\s+your\b",
    re.IGNORECASE,
)
_EXTENSIONS: Final = "|".join(sorted((ext[1:] for ext in SOURCE_EXTENSIONS), key=len, reverse=True))
# A name with a source extension, and the same followed by `:line`.
_SOURCE_NAME: Final = re.compile(rf"[\w./-]*\w\.(?:{_EXTENSIONS})\b(?!\()")
_SOURCE_PATH: Final = re.compile(rf"[\w.-]*/[\w./-]*\.(?:{_EXTENSIONS})\b")
_FILE_AND_LINE: Final = re.compile(rf"[\w./-]+\.(?:{_EXTENSIONS}):[0-9]+")
# A token that may name a file: K2's dependency and build files are matched on it.
_FILE_TOKEN: Final = re.compile(r"[\w.*-]+(?:\.[\w-]+)*")

# Characters prose may never hold; code may hold tab and line feed (G3).
_CODE_CONTROLS_ALLOWED: Final = frozenset({"\t", "\n"})


@dataclass(frozen=True, slots=True)
class GeneralSuggestion:
    """An answer that passed G1 to G9, as stored and shown."""

    summary: str
    steps: tuple[str, ...]
    language: GeneralLanguage
    code: str
    evidence_ids: tuple[str, ...]

    def stored(self) -> dict[str, Any]:
        """`finding_fixes.general_suggestion`: the checked values, nothing else."""
        return {
            "outcome": OUTCOME_SUGGESTION,
            "abstain_reason": None,
            "summary": self.summary,
            "steps": list(self.steps),
            "example": {"language": self.language, "code": self.code},
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class GeneralAbstained:
    """The model declined, from FR35's list: a good outcome, stored as `refused`."""

    reason: str


@dataclass(frozen=True, slots=True)
class GeneralRefused:
    """A rule broke. `retry_code` is set only for G1 and G2, which get one more attempt."""

    rule: Rule

    @property
    def retry_code(self) -> str | None:
        return {"G1": RETRY_SHAPE, "G2": RETRY_TOO_LONG}.get(self.rule)

    @property
    def reason(self) -> str:
        """The stored reason: G3 to G9 name their rule; G1 and G2, retried and still
        broken, end as `invalid_output` (FR36)."""
        return "invalid_output" if self.retry_code else f"general_withheld_{self.rule}"


GeneralVerdict = GeneralSuggestion | GeneralAbstained | GeneralRefused


def aliases_in(model_input: Mapping[str, Any]) -> frozenset[str]:
    """Every stand-in `model_input` holds: the aliases this version's facts may name (G9)."""
    return frozenset(_strings_matching(model_input, ALIAS_TOKEN))


def evidence_ids_in(model_input: Mapping[str, Any]) -> frozenset[str]:
    """The fact numbers the input offered (G1: an answer may rest on these only)."""
    return frozenset(
        str(fact["id"])
        for fact in model_input.get("facts") or []
        if isinstance(fact, Mapping) and isinstance(fact.get("id"), str)
    )


def check_general_text(
    text: str, *, language: GeneralLanguage, model_input: Mapping[str, Any]
) -> GeneralVerdict:
    """FR36 on the model's raw answer, as it arrives."""
    if len(text.encode("utf-8", "surrogatepass")) > ANSWER_MAX_BYTES:
        return GeneralRefused("G2")
    try:
        document = json.loads(text)
    except ValueError:
        return GeneralRefused("G1")
    return check_general(document, language=language, model_input=model_input)


def check_general(
    document: object, *, language: GeneralLanguage, model_input: Mapping[str, Any]
) -> GeneralVerdict:
    """FR36 on a parsed answer: at the return, and again on every read of the stored one."""
    if language not in GENERAL_LANGUAGES:
        return GeneralRefused("G1")
    shaped = _shape(document, language, evidence_ids_in(model_input))
    if not isinstance(shaped, GeneralSuggestion):
        return shaped
    if _encoded_size(document) > ANSWER_MAX_BYTES:
        return GeneralRefused("G2")
    for rule in (_caps, _characters):
        broken = rule(shaped)
        if broken is not None:
            return broken
    return _content(shaped, aliases_in(model_input)) or shaped


# ------------------------------------------------------------------ G1


def _encoded_size(document: object) -> int:
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    return len(text.encode("utf-8", "surrogatepass"))


def _shape(
    document: object, language: str, offered_ids: Collection[str]
) -> GeneralSuggestion | GeneralAbstained | GeneralRefused:
    if not isinstance(document, dict) or set(document) != ANSWER_KEYS:
        return GeneralRefused("G1")
    ids = document["evidence_ids"]
    if not isinstance(ids, list) or not all(isinstance(each, str) for each in ids):
        return GeneralRefused("G1")
    if any(each not in offered_ids for each in ids):
        return GeneralRefused("G1")
    if document["outcome"] == OUTCOME_ABSTAIN:
        return _abstention(document)
    if document["outcome"] != OUTCOME_SUGGESTION or document["abstain_reason"] is not None:
        return GeneralRefused("G1")
    summary, steps, example = document["summary"], document["steps"], document["example"]
    if not isinstance(summary, str) or not summary.strip() or not ids:
        return GeneralRefused("G1")
    if not isinstance(steps, list) or not STEPS_MIN <= len(steps) <= STEPS_MAX:
        return GeneralRefused("G1")
    if not all(isinstance(step, str) and step.strip() for step in steps):
        return GeneralRefused("G1")
    if not isinstance(example, dict) or set(example) != EXAMPLE_KEYS:
        return GeneralRefused("G1")
    code = example["code"]
    if example["language"] != language or not isinstance(code, str) or not code.strip():
        return GeneralRefused("G1")
    return GeneralSuggestion(
        summary=summary,
        steps=tuple(steps),
        language=example["language"],
        code=code,
        evidence_ids=tuple(ids),
    )


def _abstention(document: dict[str, Any]) -> GeneralAbstained | GeneralRefused:
    reason = document["abstain_reason"]
    empty = document["summary"] is None and document["steps"] == [] and document["example"] is None
    if reason not in GENERAL_ABSTAIN_REASONS or not empty:
        return GeneralRefused("G1")
    return GeneralAbstained(reason=reason)


# ------------------------------------------------------------------ G2, G3


def _caps(answer: GeneralSuggestion) -> GeneralRefused | None:
    lines = answer.code.split("\n")
    too_long = (
        len(answer.summary) > SUMMARY_MAX_CHARACTERS
        or any(len(step) > STEP_MAX_CHARACTERS for step in answer.steps)
        or len(lines) > CODE_MAX_LINES
        or any(len(line) > CODE_LINE_MAX_CHARACTERS for line in lines)
        or len(answer.code.encode("utf-8", "surrogatepass")) > CODE_MAX_BYTES
    )
    return GeneralRefused("G2") if too_long else None


def _forbidden(character: str, *, in_code: bool) -> bool:
    point = ord(character)
    if point < 0x20:
        return not (in_code and character in _CODE_CONTROLS_ALLOWED)
    return 0x7F <= point <= 0x9F or 0xD800 <= point <= 0xDFFF or is_hidden_character(character)


def _characters(answer: GeneralSuggestion) -> GeneralRefused | None:
    prose = (answer.summary, *answer.steps)
    if any(_forbidden(c, in_code=False) for text in prose for c in text):
        return GeneralRefused("G3")
    if any(_forbidden(c, in_code=True) for c in answer.code):
        return GeneralRefused("G3")
    if any(mixed_script_words(text) for text in (*prose, answer.code)):
        return GeneralRefused("G3")
    return None


# ------------------------------------------------------------------ G4 to G9


def _content(answer: GeneralSuggestion, known_aliases: frozenset[str]) -> GeneralRefused | None:
    prose = (answer.summary, *answer.steps)
    everything = (*prose, answer.code)
    checks: tuple[tuple[Rule, bool], ...] = (
        ("G4", any(_has_address(text) for text in everything)),
        ("G5", _starts_a_process(everything, answer)),
        ("G6", _sets_something_up(everything, answer)),
        ("G7", any(_secret_shaped(text) for text in everything)),
        ("G8", _claims_to_have_read(prose, answer.code)),
        ("G9", _unknown_alias(prose, answer.code, known_aliases)),
    )
    return next((GeneralRefused(rule) for rule, broken in checks if broken), None)


def _has_address(text: str) -> bool:
    """U4 and K6 with no "already in the file" allowance: there is no file (G4)."""
    return bool(EMAIL.search(text) or has_markup(text) or addresses_in(text, hosts_anywhere=True))


def _starts_a_process(texts: Iterable[str], answer: GeneralSuggestion) -> bool:
    return any(shell_command(text) for text in texts) or (
        process_or_dynamic_code(answer.code, answer.language) is not None
    )


def _sets_something_up(texts: Iterable[str], answer: GeneralSuggestion) -> bool:
    texts = tuple(texts)
    names_a_build_file = any(
        matches_any(NEVER_CHANGED_BUILD, token.strip("."))
        for text in texts
        for token in _FILE_TOKEN.findall(text)
    )
    return (
        any(setup_step(text) for text in texts)
        or names_a_build_file
        or bool(unlisted_imports(answer.code, answer.language))
    )


def _secret_shaped(text: str) -> bool:
    return secret_in(text) is not None or any(opaque_runs(line) for line in text.splitlines())


def _claims_to_have_read(prose: Iterable[str], code: str) -> bool:
    """BR11's words and "your code" anywhere; a source file's name in prose, and in code
    only as a path or with a line, since `x.go(` there is a call (G8)."""
    prose = tuple(prose)
    for text in (*prose, code):
        if BANNED.search(text) or READ_YOUR_CODE.search(text) or _FILE_AND_LINE.search(text):
            return True
    return any(_SOURCE_NAME.search(text) for text in prose) or bool(_SOURCE_PATH.search(code))


def _unknown_alias(prose: Iterable[str], code: str, known: frozenset[str]) -> bool:
    """In prose, only this version's aliases; in code, none (G9)."""
    if ALIAS_TOKEN.search(code):
        return True
    return any(alias not in known for text in prose for alias in ALIAS_TOKEN.findall(text))


def _strings_matching(value: object, pattern: re.Pattern[str]) -> Iterable[str]:
    if isinstance(value, str):
        yield from (match.group(0) for match in pattern.finditer(value))
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings_matching(item, pattern)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings_matching(item, pattern)
