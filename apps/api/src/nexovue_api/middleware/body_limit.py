"""A cap on every request body, counted as it arrives (readiness group 6).

Starlette has none of its own, and login, signup and reset read the whole JSON
body, so without this a caller who is not signed in can make the API read
gigabytes. `Content-Length` is a value the client chose: a declared oversize is
refused before the route runs, and every body is counted as it arrives whatever
was declared.

Every route gets `DEFAULT_CAP`, which is above the largest JSON body the API
takes (a check's 256 K-character inline body). Two routes are raised to what
their own validation accepts. The edge refuses anything above `LARGEST_CAP`
first (`deploy/caddy/Caddyfile`); the routes that stream keep their own, smaller
caps under this one.
"""

from __future__ import annotations

from typing import Final

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexovue_api.checkimports.schemas import MAX_IMPORT_CHARACTERS
from nexovue_api.errors import problems
from nexovue_api.errors.handlers import problem_response
from nexovue_api.settings import API_PREFIX
from nexovue_api.sourcemaps.service import MAX_MAP_BYTES

MIB: Final = 1024 * 1024
DEFAULT_CAP: Final = 2 * MIB

# Room for a multipart form's other fields and boundaries, or a JSON envelope.
_ENVELOPE: Final = MIB

RAISED_CAPS: Final[dict[str, int]] = {
    # The map itself, as a multipart upload.
    f"{API_PREFIX}/source-maps": MAX_MAP_BYTES + _ENVELOPE,
    # Five million characters of document, each up to four bytes in UTF-8.
    f"{API_PREFIX}/check-imports/preview": 4 * MAX_IMPORT_CHARACTERS + _ENVELOPE,
    f"{API_PREFIX}/check-imports/commit": 4 * MAX_IMPORT_CHARACTERS + _ENVELOPE,
}
LARGEST_CAP: Final = max(DEFAULT_CAP, *RAISED_CAPS.values())


def cap_for(path: str) -> int:
    return RAISED_CAPS.get(path.rstrip("/"), DEFAULT_CAP)


def _too_large(cap: int) -> HTTPException:
    return HTTPException(
        status_code=problems.CONTENT_TOO_LARGE.status,
        detail=f"The request body is larger than the {cap // MIB} MiB this route accepts.",
    )


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        cap = cap_for(scope["path"])
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > cap:
            await self._refuse(scope, receive, send, _too_large(cap))
            return

        received = 0
        started = False

        async def counted_receive() -> Message:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > cap:
                # FastAPI passes an HTTPException out of its body parsing, so a
                # route reading its body answers through the 413 handler.
                raise _too_large(cap)
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self._app(scope, counted_receive, tracked_send)
        except HTTPException as refused:
            if started or refused.status_code != problems.CONTENT_TOO_LARGE.status:
                raise
            await self._refuse(scope, receive, send, refused)

    async def _refuse(
        self, scope: Scope, receive: Receive, send: Send, refused: HTTPException
    ) -> None:
        response = problem_response(
            Request(scope, receive), problems.CONTENT_TOO_LARGE, str(refused.detail)
        )
        await response(scope, receive, send)
