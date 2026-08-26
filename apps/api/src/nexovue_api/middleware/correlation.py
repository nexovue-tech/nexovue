"""Correlation id: taken from the caller's trace when there is one, minted when not.

Written as pure ASGI middleware rather than ``BaseHTTPMiddleware`` deliberately.
Starlette runs ``BaseHTTPMiddleware`` in a separate task, and a context variable
bound there is not visible to the endpoint — which would silently strip the
correlation id off every log line the request produces.
"""

from __future__ import annotations

from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexovue_api.telemetry.context import (
    CORRELATION_ID_HEADER,
    TRACEPARENT_HEADER,
    bind_request_context,
    parse_traceparent,
)

_HTTP_SCOPE: str = "http"


class CorrelationIdMiddleware:
    """Bind one correlation id to the request, the logs and the response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != _HTTP_SCOPE:
            await self._app(scope, receive, send)
            return

        traced = parse_traceparent(Headers(scope=scope).get(TRACEPARENT_HEADER))
        trace_id, span_id = traced if traced else (None, None)
        # A caller that already has a trace gets that trace id as its correlation
        # id, so their telemetry and ours line up without a translation table.
        correlation_id = trace_id or uuid4().hex

        bind_request_context(correlation_id=correlation_id, trace_id=trace_id, span_id=span_id)
        scope.setdefault("state", {})["correlation_id"] = correlation_id

        async def send_with_correlation_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[CORRELATION_ID_HEADER] = correlation_id
            await send(message)

        await self._app(scope, receive, send_with_correlation_id)
