"""Judging a response: each assertion passed or failed, with what was found.

"Failed" always carries the actual value, because *expected 201, got 500* is the
diagnosis and *assertion 2 failed* is not (US18.15, AC3).

Values are compared as the text a person would read — `true`, `42`, `null` —
and as numbers when both sides are numbers, so `$.count equals 2` passes for a
JSON `2` and for `2.0`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Final

import re2
from nexovue_platform.checks.config import Assertion, Extraction
from nexovue_platform.checks.evidence import AssertionResult
from nexovue_platform.checks.jsonpath import find_values

from nexovue_scheduler.probes.failures import MisconfiguredError
from nexovue_scheduler.probes.result import HttpExchange

MILLISECONDS_PER_SECOND: Final = 1000.0
_EMPTY_TEXTS: Final = frozenset({"", "[]", "{}", "null"})


@dataclass(slots=True)
class ResponseView:
    """One response, read the ways assertions read it — each at most once."""

    exchange: HttpExchange
    _text: str | None = None
    _json: Any = None
    _json_read: bool = False
    _json_failed: bool = False

    @property
    def text(self) -> str:
        if self._text is None:
            self._text = self.exchange.body.decode("utf-8", "replace")
        return self._text

    def json_document(self) -> tuple[bool, Any]:
        if not self._json_read:
            self._json_read = True
            try:
                self._json = json.loads(self.text)
            except (ValueError, RecursionError):
                # Nested past the interpreter's depth is not JSON anyone asserts on.
                self._json_failed = True
        return (not self._json_failed, self._json)

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        found = [value for header, value in self.exchange.headers if header.lower() == wanted]
        return ", ".join(found) if found else None


def evaluate(assertion: Assertion, view: ResponseView) -> AssertionResult:
    actual = _actual(assertion, view)
    return AssertionResult(
        source=assertion.source,
        property=assertion.property,
        comparison=assertion.comparison,
        target=assertion.target,
        actual=actual,
        passed=compare(assertion.comparison, actual, assertion.target),
    )


def compare(comparison: str, actual: str | None, target: str) -> bool:
    if comparison == "is_empty":
        return actual is None or actual.strip() in _EMPTY_TEXTS
    if comparison == "is_not_empty":
        return not compare("is_empty", actual, target)
    if comparison in ("not_equals", "not_contains"):
        return not compare(comparison.removeprefix("not_"), actual, target)
    if actual is None:
        return False
    if comparison == "equals":
        numbers = _numbers(actual, target)
        return numbers[0] == numbers[1] if numbers else actual == target
    if comparison == "contains":
        return target in actual
    if comparison in ("less_than", "greater_than"):
        numbers = _numbers(actual, target)
        if numbers is None:
            return False
        return numbers[0] < numbers[1] if comparison == "less_than" else numbers[0] > numbers[1]
    if comparison == "matches":
        return re2.search(target, actual) is not None
    raise MisconfiguredError(f"the comparison {comparison} is not one this prober knows")


def extract(extraction: Extraction, view: ResponseView) -> str:
    """The value a later request will use. One that is not in the response is the
    response failing, not the check being wrong, so it raises `LookupError`."""
    if extraction.source == "header":
        value = view.header(extraction.expression)
    elif extraction.source == "json_path":
        value = _json_path_text(extraction.expression, view)
    else:
        match = re2.search(extraction.expression, view.text)
        value = None if match is None else (match.group(1) if match.re.groups else match.group(0))
    if value is None:
        raise LookupError(f"{extraction.name} was not found in the response")
    return value


def _actual(assertion: Assertion, view: ResponseView) -> str | None:
    exchange = view.exchange
    match assertion.source:
        case "status_code":
            return None if exchange.status_code is None else str(exchange.status_code)
        case "header":
            return view.header(assertion.property)
        case "body":
            return view.text
        case "json_path":
            return _json_path_text(assertion.property, view)
        case "response_time":
            return _milliseconds_text(exchange.total_seconds)
        case "tls_days_remaining":
            return (
                None if exchange.certificate is None else str(exchange.certificate.days_remaining)
            )


def _json_path_text(expression: str, view: ResponseView) -> str | None:
    parsed, document = view.json_document()
    if not parsed:
        return None
    try:
        found = find_values(expression, document)
    except ValueError as unparseable:
        raise MisconfiguredError(str(unparseable)) from unparseable
    if not found:
        return None
    return as_text(found[0] if len(found) == 1 else found)


def as_text(value: Any) -> str:
    """How a JSON value reads: strings as themselves, everything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))


def _milliseconds_text(seconds: float) -> str:
    return str(round(seconds * MILLISECONDS_PER_SECOND))


def _numbers(actual: str, target: str) -> tuple[float, float] | None:
    try:
        pair = (float(actual), float(target))
    except ValueError:
        return None
    return pair if all(math.isfinite(number) for number in pair) else None
