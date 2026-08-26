"""The outermost boundary: nothing escapes the application as a raw exception.

FastAPI's ``add_exception_handler(Exception, ...)`` is handled by Starlette's
ServerErrorMiddleware, which sends the response and then **re-raises** so the
ASGI server can record it — producing a second, unstructured copy of every
traceback in the logs. This boundary sits above that middleware and does not
re-raise, so one failure writes exactly one structured line.
"""

from __future__ import annotations

from typing import Final

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexovue_api.errors.handlers import handle_unexpected_error

_HTTP_SCOPE: Final = "http"


class ExceptionBoundaryMiddleware:
    """Turn any escaped exception into the same problem+json every failure gets."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != _HTTP_SCOPE:
            await self._app(scope, receive, send)
            return

        response_started = False

        async def send_tracking_start(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, receive, send_tracking_start)
        except Exception as error:
            if response_started:
                # The status line is already on the wire; the connection can only
                # be dropped now. The log line below is the record of what happened.
                raise
            response = await handle_unexpected_error(Request(scope, receive), error)
            await response(scope, receive, send)
