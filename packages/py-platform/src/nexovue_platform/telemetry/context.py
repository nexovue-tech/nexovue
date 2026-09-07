"""Request-scoped log fields, and the W3C trace context they come from.

structlog's contextvars integration is what makes this work under FastAPI:
values bound here reach every log line emitted while handling the request,
without a logger being threaded through every call.
"""

from __future__ import annotations

import re
from typing import Final

import structlog

# Field names, fixed across Go and Python. One name per concept is what makes a
# single query work over the whole system's logs.
FIELD_TIMESTAMP: Final = "ts"
FIELD_LEVEL: Final = "level"
FIELD_MESSAGE: Final = "msg"
FIELD_SERVICE: Final = "service"
FIELD_ENV: Final = "env"
FIELD_TRACE_ID: Final = "trace_id"
FIELD_SPAN_ID: Final = "span_id"
FIELD_CORRELATION_ID: Final = "correlation_id"
FIELD_ORG_ID: Final = "org_id"
FIELD_PROJECT_ID: Final = "project_id"

TRACEPARENT_HEADER: Final = "traceparent"

# Correlation id returned to the caller. It is the string to quote in a
# support conversation and the one to query the logs by.
CORRELATION_ID_HEADER: Final = "X-Correlation-Id"

# version-traceid-spanid-flags, lowercase hex, per W3C Trace Context.
_TRACEPARENT_PATTERN: Final = re.compile(
    r"^(?P<version>[0-9a-f]{2})-"
    r"(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-"
    r"(?P<flags>[0-9a-f]{2})$"
)
_FORBIDDEN_VERSION: Final = "ff"
_ALL_ZERO_TRACE_ID: Final = "0" * 32
_ALL_ZERO_SPAN_ID: Final = "0" * 16


def parse_traceparent(header: str | None) -> tuple[str, str] | None:
    """Extract (trace_id, span_id) from a W3C traceparent header.

    A malformed header is not an error — it just means the caller gave us
    nothing usable and we start our own correlation id instead.
    """
    if not header:
        return None
    match = _TRACEPARENT_PATTERN.match(header.strip())
    if match is None or match["version"] == _FORBIDDEN_VERSION:
        return None
    if match["trace_id"] == _ALL_ZERO_TRACE_ID or match["span_id"] == _ALL_ZERO_SPAN_ID:
        return None
    return match["trace_id"], match["span_id"]


def bind_request_context(
    *,
    correlation_id: str,
    trace_id: str | None = None,
    span_id: str | None = None,
) -> None:
    """Start a fresh log context for one request."""
    structlog.contextvars.clear_contextvars()
    fields = {FIELD_CORRELATION_ID: correlation_id}
    if trace_id:
        fields[FIELD_TRACE_ID] = trace_id
    if span_id:
        fields[FIELD_SPAN_ID] = span_id
    structlog.contextvars.bind_contextvars(**fields)


def bind_tenant(*, org_id: str | None = None, project_id: str | None = None) -> None:
    """Add tenancy to the current request's log context once it is known."""
    fields = {
        key: value
        for key, value in ((FIELD_ORG_ID, org_id), (FIELD_PROJECT_ID, project_id))
        if value
    }
    if fields:
        structlog.contextvars.bind_contextvars(**fields)


def current_correlation_id() -> str | None:
    """The correlation id bound to this request, if the middleware has run."""
    value = structlog.contextvars.get_contextvars().get(FIELD_CORRELATION_ID)
    return str(value) if value is not None else None
