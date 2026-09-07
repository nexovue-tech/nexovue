"""The log contract: shared field names, and secrets that never reach a line."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog
from nexovue_platform.telemetry.context import (
    FIELD_CORRELATION_ID,
    FIELD_ENV,
    FIELD_LEVEL,
    FIELD_MESSAGE,
    FIELD_SERVICE,
    FIELD_SPAN_ID,
    FIELD_TIMESTAMP,
    FIELD_TRACE_ID,
    bind_request_context,
    bind_tenant,
    parse_traceparent,
)
from nexovue_platform.telemetry.logging import FORMAT_CONSOLE, FORMAT_JSON, configure_logging
from nexovue_platform.telemetry.redaction import PLACEHOLDER, redact_credentials

# Planted everywhere a credential can enter a log line. It must survive none of them.
KNOWN_SECRET = "hunter2-do-not-log-this"

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN_ID = "00f067aa0ba902b7"


@pytest.fixture(autouse=True)
def _clean_log_context() -> Iterator[None]:
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()
    logging.getLogger().handlers = []


def _emit(
    log_format: str, level: str = "info", message: str = "connecting", **fields: object
) -> str:
    """Configure the logger, write one line, and return exactly what was written."""
    stream = io.StringIO()
    configure_logging(
        level="debug",
        log_format=log_format,
        service="api",
        env="development",
        stream=stream,
    )
    getattr(structlog.stdlib.get_logger("test"), level)(message, **fields)
    return stream.getvalue()


def test_json_line_uses_the_shared_field_names() -> None:
    bind_request_context(correlation_id="corr-1", trace_id=TRACE_ID, span_id=SPAN_ID)
    bind_tenant(org_id="org-1", project_id="proj-1")

    line = json.loads(_emit(FORMAT_JSON))

    assert line[FIELD_MESSAGE] == "connecting"
    assert line[FIELD_LEVEL] == "info"
    assert line[FIELD_SERVICE] == "api"
    assert line[FIELD_ENV] == "development"
    assert line[FIELD_TRACE_ID] == TRACE_ID
    assert line[FIELD_SPAN_ID] == SPAN_ID
    assert line[FIELD_CORRELATION_ID] == "corr-1"
    assert line["org_id"] == "org-1"
    assert line["project_id"] == "proj-1"
    assert FIELD_TIMESTAMP in line


def test_warning_is_written_as_warn_like_the_go_services() -> None:
    line = json.loads(_emit(FORMAT_JSON, level="warning", message="degraded but handled"))

    assert line[FIELD_LEVEL] == "warn"


@pytest.mark.parametrize("log_format", [FORMAT_JSON, FORMAT_CONSOLE])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("password", KNOWN_SECRET),
        ("api_key", KNOWN_SECRET),
        ("session_id", KNOWN_SECRET),
        ("postgres_dsn", f"postgresql://nexovue:{KNOWN_SECRET}@db:5432/nexovue"),
        ("detail", f"could not connect to redis://default:{KNOWN_SECRET}@cache:6379/0"),
    ],
)
def test_a_known_secret_never_reaches_a_log_line(log_format: str, field: str, value: str) -> None:
    line = _emit(log_format, **{field: value})

    assert KNOWN_SECRET not in line, f"secret leaked: {line}"
    assert PLACEHOLDER in line


def test_redaction_keeps_the_host_an_operator_needs() -> None:
    redacted = redact_credentials(f"postgresql://nexovue:{KNOWN_SECRET}@db:5432/nexovue")

    assert KNOWN_SECRET not in redacted
    assert "db:5432" in redacted


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (f"00-{TRACE_ID}-{SPAN_ID}-01", (TRACE_ID, SPAN_ID)),
        (f"00-{TRACE_ID}-{SPAN_ID}-00", (TRACE_ID, SPAN_ID)),
        (None, None),
        ("", None),
        (f"00-{TRACE_ID}-{SPAN_ID}", None),
        (f"ff-{TRACE_ID}-{SPAN_ID}-01", None),
        (f"00-{'0' * 32}-{SPAN_ID}-01", None),
        (f"00-{TRACE_ID.upper()}-{SPAN_ID}-01", None),
    ],
)
def test_traceparent_parsing(header: str | None, expected: tuple[str, str] | None) -> None:
    assert parse_traceparent(header) == expected
