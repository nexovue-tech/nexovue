"""The console's own errors reach the API's log (readiness group 7).

Before this, production dropped every browser error: the console's logger sent
to a no-op. Logs are the channel that works when Kafka or ClickHouse is the
broken part (decision 0053, rule 2), so a crash in a customer's browser becomes
one structured line next to every other service's.

Anyone can call it — a sign-in screen can fail too — so what it accepts is
bounded: warnings and errors only, at most a batch of twenty, every field cut to
a fixed length, and nothing done but a log line.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from nexovue_api.clientlogs import router as client_logs_router
from nexovue_api.clientlogs.schemas import MAX_MESSAGE, MAX_RECORDS, MAX_STACK
from nexovue_api.errors.handlers import install_error_handlers
from nexovue_api.settings import API_PREFIX

PATH = f"{API_PREFIX}/client-logs"


def _client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    versioned = APIRouter(prefix=API_PREFIX)
    versioned.include_router(client_logs_router.router)
    app.include_router(versioned)
    return TestClient(app)


def _record(**overrides: Any) -> dict[str, Any]:
    return {
        "severity": "error",
        "message": "Uncaught render error",
        "timestamp": "2026-10-11T12:00:00.000Z",
        "page": "/services/checkout",
        "error": {"name": "TypeError", "message": "x is undefined", "stack": "at render (a.js:1)"},
        "attributes": {"component_stack": "in ServicesScreen", "caught_by_boundary": False},
        "correlation_id": "c0ffee",
        **overrides,
    }


def test_an_error_from_the_console_is_one_log_line_at_its_own_level() -> None:
    with capture_logs() as lines:
        answered = _client().post(PATH, json={"records": [_record()]})

    assert answered.status_code == 204
    [line] = [line for line in lines if line.get("source") == "browser"]
    assert line["log_level"] == "error"
    assert line["event"] == "Uncaught render error"
    assert line["page"] == "/services/checkout"
    assert line["error_name"] == "TypeError"
    assert line["error_stack"] == "at render (a.js:1)"
    assert line["browser_correlation_id"] == "c0ffee"
    assert line["component_stack"] == "in ServicesScreen"


def test_a_warning_is_logged_as_a_warning() -> None:
    with capture_logs() as lines:
        _client().post(PATH, json={"records": [_record(severity="warn", error=None)]})

    assert [line["log_level"] for line in lines if line.get("source") == "browser"] == ["warning"]


def test_every_field_is_cut_to_its_length() -> None:
    long = _record(
        message="m" * (MAX_MESSAGE * 3),
        error={"name": "E", "message": "e", "stack": "s" * (MAX_STACK * 3)},
    )
    with capture_logs() as lines:
        _client().post(PATH, json={"records": [long]})

    [line] = [line for line in lines if line.get("source") == "browser"]
    assert len(line["event"]) == MAX_MESSAGE
    assert len(line["error_stack"]) == MAX_STACK


def test_debug_and_info_are_refused() -> None:
    for severity in ("debug", "info"):
        assert (
            _client().post(PATH, json={"records": [_record(severity=severity)]}).status_code == 422
        )


def test_a_batch_larger_than_the_limit_is_refused() -> None:
    batch = {"records": [_record()] * (MAX_RECORDS + 1)}

    assert _client().post(PATH, json=batch).status_code == 422
