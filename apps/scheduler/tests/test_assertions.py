"""Assertions compare what a person would read, and say what they found (AC3)."""

from __future__ import annotations

from typing import Any

import pytest
from nexovue_platform.checks.config import Assertion, Extraction

from nexovue_scheduler.probes.assertions import ResponseView, compare, evaluate, extract
from nexovue_scheduler.probes.result import Certificate, HttpExchange


def _view(
    body: bytes = b"", headers: list[tuple[str, str]] | None = None, **overrides: Any
) -> ResponseView:
    exchange = HttpExchange(
        status_code=overrides.get("status_code", 200),
        headers=headers or [],
        body=body,
        body_truncated=False,
        total_seconds=overrides.get("total_seconds", 0.25),
        phases={},
        certificate=overrides.get("certificate"),
        effective_url="https://a.example/",
    )
    return ResponseView(exchange)


def _assert(**fields: Any) -> Assertion:
    return Assertion.model_validate(fields)


@pytest.mark.parametrize(
    ("comparison", "actual", "target", "passed"),
    [
        ("equals", "2", "2.0", True),
        ("equals", "shoe", "shoe", True),
        ("not_equals", "shoe", "boot", True),
        ("contains", "hello world", "world", True),
        ("not_contains", None, "x", True),
        ("less_than", "199", "200", True),
        ("greater_than", "abc", "1", False),
        ("matches", "order-42", r"order-\d+", True),
        ("is_empty", None, "", True),
        ("is_empty", "[]", "", True),
        ("is_not_empty", "null", "", False),
        ("equals", None, "", False),
    ],
)
def test_comparisons(comparison: str, actual: str | None, target: str, passed: bool) -> None:
    assert compare(comparison, actual, target) is passed


def test_a_json_path_reads_json_values_as_text() -> None:
    view = _view(b'{"order": {"id": 7, "paid": true, "items": [1, 2]}}')

    assert evaluate(
        _assert(source="json_path", property="$.order.id", comparison="equals", target="7"), view
    ).passed
    paid = evaluate(
        _assert(source="json_path", property="$.order.paid", comparison="equals", target="true"),
        view,
    )
    assert paid.passed and paid.actual == "true"
    items = evaluate(
        _assert(source="json_path", property="$.order.items[*]", comparison="contains", target="2"),
        view,
    )
    assert items.actual == "[1,2]"


def test_a_body_that_is_not_json_has_nothing_at_the_path() -> None:
    result = evaluate(
        _assert(source="json_path", property="$.ok", comparison="equals", target="true"),
        _view(b"<html>"),
    )
    assert (result.passed, result.actual) == (False, None)


def test_headers_are_matched_case_insensitively() -> None:
    view = _view(headers=[("Content-Type", "application/json")])
    result = evaluate(
        _assert(source="header", property="content-type", comparison="contains", target="json"),
        view,
    )
    assert result.passed and result.actual == "application/json"


def test_response_time_and_certificate_are_numbers() -> None:
    view = _view(total_seconds=0.4321, certificate=Certificate(12, "CA", "a.example"))
    time = evaluate(_assert(source="response_time", comparison="less_than", target="500"), view)
    cert = evaluate(
        _assert(source="tls_days_remaining", comparison="greater_than", target="14"), view
    )
    assert (time.passed, time.actual) == (True, "432")
    assert (cert.passed, cert.actual) == (False, "12")


def test_extraction_takes_the_first_group_of_a_pattern() -> None:
    view = _view(b"token=abc123;")
    assert (
        extract(Extraction(name="t", source="regex", expression=r"token=(\w+)"), view) == "abc123"
    )
    with pytest.raises(LookupError):
        extract(Extraction(name="t", source="header", expression="X-Missing"), view)
