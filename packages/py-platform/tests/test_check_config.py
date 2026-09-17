"""The check configuration refuses what could not run, and says which field (AC25, BR1)."""

from __future__ import annotations

from typing import Any

import pytest
from nexovue_platform.checks.config import (
    MAX_RUN_SECONDS,
    ApiCheckConfig,
    MultistepCheckConfig,
    file_ids_of,
    parse_config,
    run_seconds_of,
    secret_names_of,
    undefined_variables,
)
from pydantic import ValidationError

STATUS_OK: dict[str, str] = {"source": "status_code", "comparison": "less_than", "target": "400"}


def _api(**request: Any) -> dict[str, Any]:
    return {"type": "api", "request": {"url": "https://api.example.com/", **request}}


def _problems(document: dict[str, Any]) -> list[tuple[str, str]]:
    with pytest.raises(ValidationError) as refused:
        parse_config(document)
    return [
        (".".join(str(part) for part in error["loc"]), error["msg"])
        for error in refused.value.errors()
    ]


def test_a_check_that_asserts_nothing_is_refused() -> None:
    """BR1: a check must be able to fail."""
    problems = _problems(_api(assertions=[]))
    assert any("assertions" in field for field, _ in problems)


def test_any_method_token_is_accepted_and_a_split_one_is_not() -> None:
    config = parse_config(_api(method="PROPFIND", assertions=[STATUS_OK]))
    assert isinstance(config, ApiCheckConfig) and config.request.method == "PROPFIND"

    assert _problems(_api(method="GET /x HTTP/1.1", assertions=[STATUS_OK]))


@pytest.mark.parametrize("value", ["a\r\nX-Injected: yes", "a\nb", "a\x00"])
def test_a_header_value_with_a_line_break_is_refused(value: str) -> None:
    problems = _problems(_api(headers=[{"name": "X-A", "value": value}], assertions=[STATUS_OK]))
    assert any("headers" in field for field, _ in problems)


def test_a_header_name_must_be_a_token() -> None:
    assert _problems(_api(headers=[{"name": "Bad Name", "value": "x"}], assertions=[STATUS_OK]))


def test_an_unknown_field_is_refused_rather_than_ignored() -> None:
    assert _problems(_api(assertions=[STATUS_OK], retries_on_tuesday=True))


@pytest.mark.parametrize(
    ("assertion", "why"),
    [
        ({"source": "json_path", "comparison": "equals", "target": "1"}, "no path"),
        (
            {"source": "json_path", "property": "$[", "comparison": "equals", "target": "1"},
            "bad path",
        ),
        (
            {"source": "json_path", "property": "$[?match(@.a, 'x')]", "comparison": "is_empty"},
            "backtracking function",
        ),
        ({"source": "status_code", "comparison": "contains", "target": "20"}, "status is a number"),
        ({"source": "status_code", "comparison": "less_than", "target": "soon"}, "not a number"),
        ({"source": "body", "comparison": "matches", "target": "(unclosed"}, "bad pattern"),
        (
            {"source": "body", "comparison": "matches", "target": "(a)\\1"},
            "RE2 has no backreferences",
        ),
        (
            {"source": "header", "comparison": "is_empty", "target": "x", "property": "A"},
            "no value taken",
        ),
        (
            {"source": "response_time", "property": "x", "comparison": "less_than", "target": "5"},
            "no property",
        ),
    ],
)
def test_an_assertion_that_cannot_be_judged_is_refused(assertion: dict[str, str], why: str) -> None:
    assert _problems(_api(assertions=[assertion])), why


def test_extractions_belong_to_multistep_checks() -> None:
    extraction = {"name": "token", "source": "json_path", "expression": "$.token"}
    assert _problems(_api(assertions=[STATUS_OK], extractions=[extraction]))


def test_a_multistep_run_must_fit_the_task_time_limit() -> None:
    step = {"url": "https://a.example/", "assertions": [STATUS_OK], "timeout_seconds": 30}
    problems = _problems({"type": "multistep", "steps": [step, step, step]})
    assert any(str(MAX_RUN_SECONDS) in message for _, message in problems)


def test_at_most_three_files_travel_in_one_request() -> None:
    parts = [
        {"kind": "file", "name": f"f{i}", "file_id": f"00000000-0000-0000-0000-00000000000{i}"}
        for i in range(4)
    ]
    assert _problems(_api(body={"kind": "multipart", "parts": parts}, assertions=[STATUS_OK]))


def test_secrets_are_found_wherever_a_request_names_them() -> None:
    config = parse_config(
        _api(
            url="https://api.example.com/{{secret.PATH_TOKEN}}",
            headers=[{"name": "X-Key", "value": "{{ secret.HEADER_KEY }}"}],
            auth={"kind": "bearer", "token_secret": "BEARER"},
            assertions=[STATUS_OK],
        )
    )
    assert secret_names_of(config) == {"PATH_TOKEN", "HEADER_KEY", "BEARER"}


def test_a_later_request_may_use_what_an_earlier_one_extracted_and_not_the_reverse() -> None:
    config = parse_config(
        {
            "type": "multistep",
            "steps": [
                {
                    "url": "https://a.example/{{order_id}}",
                    "assertions": [STATUS_OK],
                    "extractions": [{"name": "token", "source": "header", "expression": "X-T"}],
                },
                {"url": "https://a.example/{{token}}/{{region}}", "assertions": [STATUS_OK]},
            ],
        }
    )
    assert isinstance(config, MultistepCheckConfig)
    assert undefined_variables(config, frozenset({"region"})) == [(0, "order_id")]


def test_files_and_run_time_are_read_off_the_configuration() -> None:
    config = parse_config(
        _api(
            body={"kind": "binary", "file_id": "00000000-0000-0000-0000-000000000009"},
            timeout_seconds=12,
            assertions=[STATUS_OK],
        )
    )
    assert [str(file_id) for file_id in file_ids_of(config)] == [
        "00000000-0000-0000-0000-000000000009"
    ]
    assert run_seconds_of(config) == 12


@pytest.mark.parametrize(
    "document",
    [
        {"type": "tcp", "host": "db.example.com", "port": 5432},
        {"type": "dns", "name": "example.com", "record_type": "MX"},
        {"type": "heartbeat", "grace_seconds": 120},
    ],
)
def test_the_other_kinds_parse(document: dict[str, Any]) -> None:
    assert parse_config(document).type == document["type"]


def test_a_tcp_host_is_a_host_not_a_url() -> None:
    assert _problems({"type": "tcp", "host": "https://db.example.com/", "port": 5432})
