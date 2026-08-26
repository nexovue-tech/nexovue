"""One log line per request, carrying the fields already bound to the context.

Pure ASGI, like the correlation middleware, and for the same reason: the context
variables bound upstream must still be visible when the line is written.
"""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexovue_api.telemetry.logging import get_logger

logger = get_logger(__name__)

_HTTP_SCOPE: Final = "http"
MILLISECONDS_PER_SECOND: Final = 1000

# Probes run every few seconds forever; logging them buries everything else.
SILENT_PATHS: Final = ("/healthz", "/readyz")


class RequestLogMiddleware:
    """Record how each request ended, at the level its status deserves."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != _HTTP_SCOPE or scope["path"] in SILENT_PATHS:
            await self._app(scope, receive, send)
            return

        started = time.perf_counter()
        status = int(HTTPStatus.INTERNAL_SERVER_ERROR)

        async def send_recording_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, send_recording_status)
        finally:
            _log_request(scope, status, started)


def _log_request(scope: Scope, status: int, started: float) -> None:
    duration_ms = int((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
    fields = {
        "method": scope.get("method", ""),
        "path": scope.get("path", ""),
        "status": status,
        "duration_ms": duration_ms,
    }
    if status >= int(HTTPStatus.INTERNAL_SERVER_ERROR):
        logger.error("request completed", **fields)
    elif status >= int(HTTPStatus.BAD_REQUEST):
        logger.warning("request completed", **fields)
    else:
        logger.info("request completed", **fields)
