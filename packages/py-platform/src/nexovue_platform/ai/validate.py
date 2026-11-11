"""Code checks every claim a model writes, in FR37's order, and removes any that fails (BR9).

Pure: an answer and the `model_input` it was written from go in; checked words, or why there
are none, come out. The AI role runs it when it writes, and the API again when it reads,
against the input of the version the words were checked against (A48), because the AI
account can write wording for any organization.

**These checks test form, not truth.** A wrong sentence that fits them passes, and the
page's label says so. The word lists are crude starting points the bed extends (FR37).
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from nexovue_platform.ai.contract import REPORTED_MODEL
from nexovue_platform.ai.explain_prompt import CLAIM_TYPES, MAX_CLAIMS, MAX_PICKS

MAX_CLAIM_CHARACTERS: Final = 240

# Each rule a claim can fail, in the order it is checked (FR37).
Rule = Literal[
    "schema",
    "evidence",
    "characters",
    "alias",
    "number",
    "number_word",
    "causal",
    "origin",
    "command",
    "link",
    "length",
]

_ANSWER_KEYS: Final = frozenset({"claims", "next_checks", "abstain"})
_CLAIM_KEYS: Final = frozenset({"type", "text", "evidence_ids"})
_STORED_KEYS: Final = _ANSWER_KEYS | {"model"}

# Plain printable ASCII, and a few typographic marks a careful writer uses. Nothing that
# hides, reorders or joins text (no bidirectional or invisible characters).
# Times, dashes, curly quotes, ellipsis, arrow, micro.
_EXTRA_CHARACTERS: Final = frozenset("\u00d7\u2013\u2014\u2018\u2019\u201c\u201d\u2026\u2192\u00b5")
_ALIAS: Final = re.compile(r"\b(?:service|version|error|check)_[0-9]+\b")
_ALIAS_FIELDS: Final = ("subject", "callee", "version", "error", "check")
_NUMBER: Final = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")
_NUMBER_FIELDS: Final = (
    "before",
    "during",
    "minutes_from_start",
    "deploys_last_7_days",
    "days_of_records",
    "chance",
)
# Rounded to at most this many places, a written number must equal a value (FR37).
_MAX_PLACES: Final = 1
_NUMBER_WORDS: Final = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
    "hundred",
    "hundreds",
    "thousand",
    "thousands",
    "million",
    "millions",
    "billion",
    "dozen",
    "dozens",
    "twice",
    "thrice",
    "double",
    "doubled",
    "triple",
    "tripled",
    "half",
    "quarter",
]
# A start the bed extends (FR37). Matched as whole words, any case. These lists, `URL` and
# `EMAIL` are the one copy: F4's chat checks extend them and never restate them.
CAUSAL_PHRASES: Final = (
    "caused",
    "causes",
    "causing",
    "because",
    "root cause",
    "due to",
    "triggered",
    "triggers",
    "triggering",
    "led to",
    "leads to",
    "leading to",
    "resulted",
    "results in",
    "resulting in",
    "result of",
    "as a result",
    "responsible for",
)
ORIGIN_WORDS: Final = ("source", "sources", "origin", "originated", "culprit", "at fault", "blame")
COMMAND_WORDS: Final = (
    "run",
    "delete",
    "restart",
    "ignore",
    "disable",
    "rotate",
    "roll back",
    "rollback",
    "revert",
    "redeploy",
    "reboot",
    "kill",
    "drop",
    "execute",
)
URL: Final = re.compile(r"[a-z][a-z0-9+.-]*://|\bwww\.", re.IGNORECASE)
EMAIL: Final = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_DOMAIN: Final = re.compile(
    r"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b", re.IGNORECASE
)
_MARKUP: Final = frozenset("`*_#[]<>|~{}\\")
_SENTENCE_START: Final = re.compile(r"(?:^|[.!?;:]\s+)([A-Za-z][A-Za-z ]*)")
# What stands in for an alias while its digits and underscore are not the sentence's own.
_ALIAS_STAND_IN: Final = "SERVICE"


@dataclass(frozen=True, slots=True)
class Claim:
    type: str
    text: str
    evidence_ids: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text, "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True, slots=True)
class Checked:
    """Words that passed, or an abstention. `removed` counts claims taken out, never their
    text, so the bed can report how many good sentences a rule drops."""

    claims: tuple[Claim, ...]
    picks: tuple[str, ...]
    abstained: bool
    removed: int = 0

    def stored(self, model: str) -> dict[str, Any]:
        """The form `finding_explanations.wording` holds: aliases, never names."""
        return {
            "claims": [claim.as_json() for claim in self.claims],
            "next_checks": list(self.picks),
            "abstain": self.abstained,
            "model": model,
        }


@dataclass(frozen=True, slots=True)
class Refused:
    """No words survive. The reason is a closed word; the answer itself is never kept."""

    reason: Literal["invalid"] = "invalid"


def check_answer(answer: str, model_input: Mapping[str, Any]) -> Checked | Refused:
    """FR37 over a model's answer: claims that fail are removed. With none left and no
    abstention, the run has failed (AC52)."""
    document = _json(answer)
    if not _fits(document, _ANSWER_KEYS):
        return Refused()
    assert isinstance(document, dict)
    if document["abstain"] is True:
        return Checked(claims=(), picks=(), abstained=True)
    kept: list[Claim] = []
    removed = 0
    for raw in document["claims"]:
        claim = _claim(raw)
        if claim is None or check_claim(claim, model_input) is not None:
            removed += 1
            continue
        kept.append(claim)
    if not kept:
        return Refused()
    return Checked(
        claims=tuple(kept),
        picks=_picks(document["next_checks"], model_input),
        abstained=False,
        removed=removed,
    )


def recheck(stored: object, model_input: Mapping[str, Any]) -> Checked | None:
    """The API's read-time check (A48, AC65): stored words pass whole, or not at all. A row
    the AI account wrote may hold anything, so nothing in it is believed."""
    if not _fits(stored, _STORED_KEYS) or not isinstance(stored, dict):
        return None
    if not isinstance(stored["model"], str) or not REPORTED_MODEL.fullmatch(stored["model"]):
        return None
    if stored["abstain"] is True:
        return None
    claims = [_claim(raw) for raw in stored["claims"]]
    if not claims or any(c is None or check_claim(c, model_input) for c in claims):
        return None
    offered = _offered_checks(model_input)
    picks = stored["next_checks"]
    if any(not isinstance(pick, str) or pick not in offered for pick in picks):
        return None
    return Checked(
        claims=tuple(c for c in claims if c is not None),
        picks=tuple(picks),
        abstained=False,
    )


def check_claim(claim: Claim, model_input: Mapping[str, Any]) -> Rule | None:
    """The first FR37 rule `claim` fails, or None when it passes every one."""
    facts = _facts_by_id(model_input)
    if claim.type not in CLAIM_TYPES:
        return "schema"
    if not claim.evidence_ids or any(fact_id not in facts for fact_id in claim.evidence_ids):
        return "evidence"
    if not all(_allowed_character(character) for character in claim.text):
        return "characters"
    cited = [facts[fact_id] for fact_id in claim.evidence_ids]
    if not _aliases_belong(claim.text, cited):
        return "alias"
    prose = _ALIAS.sub(_ALIAS_STAND_IN, claim.text)
    if not _numbers_match(prose, cited):
        return "number"
    if has_any(prose, _NUMBER_WORDS):
        return "number_word"
    if has_any(prose, CAUSAL_PHRASES):
        return "causal"
    if has_any(prose, ORIGIN_WORDS) and not _cites_the_cause(claim, model_input):
        return "origin"
    if opens_with_command(prose):
        return "command"
    if _has_link_or_markup(prose):
        return "link"
    if len(claim.text) > MAX_CLAIM_CHARACTERS:
        return "length"
    return None


# ----------------------------------------------------------------- shape


def _json(answer: str) -> object:
    """The answer's JSON. A single Markdown code fence around it is taken off: some
    servers add one even when asked for JSON. Nothing else is repaired."""
    text = answer.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text[3:-3]
        text = text.removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None


def _fits(document: object, keys: frozenset[str]) -> bool:
    if not isinstance(document, dict) or set(document) != keys:
        return False
    claims, picks = document["claims"], document["next_checks"]
    return (
        isinstance(claims, list)
        and len(claims) <= MAX_CLAIMS
        and isinstance(picks, list)
        and len(picks) <= MAX_PICKS
        and isinstance(document["abstain"], bool)
    )


def _claim(raw: object) -> Claim | None:
    if not isinstance(raw, dict) or set(raw) != _CLAIM_KEYS:
        return None
    kind, text, ids = raw["type"], raw["text"], raw["evidence_ids"]
    if not isinstance(kind, str) or not isinstance(text, str) or not isinstance(ids, list):
        return None
    if not all(isinstance(each, str) for each in ids):
        return None
    return Claim(type=kind, text=text, evidence_ids=tuple(ids))


def _offered_checks(model_input: Mapping[str, Any]) -> set[str]:
    return {
        str(check.get("id"))
        for check in model_input.get("next_checks") or []
        if isinstance(check, Mapping)
    }


def _picks(raw: Sequence[object], model_input: Mapping[str, Any]) -> tuple[str, ...]:
    """A next check not on the offered list is dropped (FR37)."""
    offered = _offered_checks(model_input)
    picks: list[str] = []
    for pick in raw:
        if isinstance(pick, str) and pick in offered and pick not in picks:
            picks.append(pick)
    return tuple(picks)


def _facts_by_id(model_input: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(fact["id"]): fact
        for fact in model_input.get("facts") or []
        if isinstance(fact, Mapping) and "id" in fact
    }


# ----------------------------------------------------------------- rules


def _allowed_character(character: str) -> bool:
    if character in _EXTRA_CHARACTERS:
        return True
    return " " <= character <= "~" and unicodedata.category(character) != "Cc"


def _aliases_belong(text: str, cited: Iterable[Mapping[str, Any]]) -> bool:
    """Every alias the claim names is one of its own facts' (FR37)."""
    own = {fact[name] for fact in cited for name in _ALIAS_FIELDS if fact.get(name)}
    return all(alias in own for alias in _ALIAS.findall(text))


def _values(cited: Iterable[Mapping[str, Any]]) -> list[float]:
    """Each number a claim may write: its facts' values, their sizes, a chance as a
    percentage, and a ratio of a fact's two readings (`4x`, or with a times sign)."""
    found: list[float] = []
    for fact in cited:
        for name in _NUMBER_FIELDS:
            value = fact.get(name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            found.extend((float(value), abs(float(value))))
            if name == "chance":
                found.append(float(value) * 100)
        before, during = fact.get("before"), fact.get("during")
        if isinstance(before, int | float) and isinstance(during, int | float) and before:
            found.append(float(during) / float(before))
    return found


def _numbers_match(prose: str, cited: Sequence[Mapping[str, Any]]) -> bool:
    candidates = _values(cited)
    for written in _NUMBER.findall(prose):
        plain = written.replace(",", "")
        places = len(plain.split(".")[1]) if "." in plain else 0
        if places > _MAX_PLACES:
            return False
        number = float(plain)
        if not any(math.isclose(round(c, places), number, abs_tol=1e-9) for c in candidates):
            return False
    return True


def has_any(prose: str, phrases: Iterable[str]) -> bool:
    lowered = prose.lower()
    return any(re.search(rf"\b{re.escape(phrase)}\b", lowered) for phrase in phrases)


def _cites_the_cause(claim: Claim, model_input: Mapping[str, Any]) -> bool:
    """Origin words only beside the code-chosen cause (FR37): never with none."""
    cause = model_input.get("likely_cause")
    if not isinstance(cause, Mapping):
        return False
    return cause.get("evidence_id") in claim.evidence_ids


def opens_with_command(prose: str, words: Iterable[str] = COMMAND_WORDS) -> bool:
    """Whether any sentence or clause of `prose` starts with one of `words`."""
    chosen = tuple(words)
    for start in _SENTENCE_START.findall(prose):
        opening = start.lower()
        if any(opening == word or opening.startswith(f"{word} ") for word in chosen):
            return True
    return False


def _has_link_or_markup(prose: str) -> bool:
    if any(character in _MARKUP for character in prose):
        return True
    return bool(URL.search(prose) or EMAIL.search(prose) or _DOMAIN.search(prose))
