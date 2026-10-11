"""No request body larger than its route can use reaches the application.

Starlette has no cap of its own: login, signup and reset read the whole JSON
body, so a caller who is not signed in could make the API read gigabytes. The
middleware counts the bytes that actually arrive — `Content-Length` is a value
the client chose — and refuses past the route's cap with 413.

Driven as raw ASGI, because a well-behaved HTTP client cannot send a body that
contradicts its own `Content-Length`, and that is one of the cases.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.types import Message, Receive, Scope, Send

from nexovue_api.checkimports.schemas import MAX_IMPORT_CHARACTERS
from nexovue_api.errors import problems
from nexovue_api.errors.handlers import install_error_handlers
from nexovue_api.middleware import body_limit
from nexovue_api.middleware.body_limit import DEFAULT_CAP, RequestBodyLimitMiddleware, cap_for
from nexovue_api.settings import API_PREFIX
from nexovue_api.sourcemaps.service import MAX_MAP_BYTES

LOGIN = f"{API_PREFIX}/auth/login"
CHUNK = 64 * 1024


class _Reader:
    """An application that reads the whole body, as a JSON route does."""

    def __init__(self) -> None:
        self.received = 0
        self.called = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.called = True
        if scope["type"] == "http":
            while True:
                message = await receive()
                self.received += len(message.get("body", b""))
                if not message.get("more_body"):
                    break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _chunks(total: int) -> Iterator[Message]:
    sent = 0
    while sent < total:
        size = min(CHUNK, total - sent)
        sent += size
        yield {"type": "http.request", "body": b"x" * size, "more_body": sent < total}


async def _drive(
    path: str, body_bytes: int, *, declared: int | None = None, kind: str = "http"
) -> tuple[_Reader, list[Message]]:
    application = _Reader()
    middleware = RequestBodyLimitMiddleware(application)
    headers = [] if declared is None else [(b"content-length", str(declared).encode())]
    scope: dict[str, Any] = {"type": kind, "method": "POST", "path": path, "headers": headers}
    incoming = _chunks(body_bytes)
    sent: list[Message] = []

    async def receive() -> Message:
        return next(incoming, {"type": "http.request", "body": b"", "more_body": False})

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)
    return application, sent


def _status(sent: list[Message]) -> int:
    return int(next(m for m in sent if m["type"] == "http.response.start")["status"])


def _problem(sent: list[Message]) -> dict[str, Any]:
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return dict(json.loads(body))


async def test_a_body_under_the_cap_reaches_the_route() -> None:
    application, sent = await _drive(LOGIN, 4096, declared=4096)

    assert _status(sent) == 200
    assert application.received == 4096


async def test_a_declared_oversize_is_refused_before_the_route_runs() -> None:
    application, sent = await _drive(LOGIN, 0, declared=DEFAULT_CAP + 1)

    assert _status(sent) == 413
    assert not application.called
    assert _problem(sent)["type"] == problems.CONTENT_TOO_LARGE.uri


async def test_a_body_with_no_length_is_counted_as_it_arrives() -> None:
    application, sent = await _drive(LOGIN, DEFAULT_CAP + CHUNK)

    assert _status(sent) == 413
    assert application.received <= DEFAULT_CAP


async def test_a_length_that_understates_the_body_does_not_get_it_through() -> None:
    application, sent = await _drive(LOGIN, DEFAULT_CAP + CHUNK, declared=100)

    assert _status(sent) == 413
    assert application.received <= DEFAULT_CAP


async def test_a_source_map_may_be_as_large_as_the_service_accepts() -> None:
    assert cap_for(f"{API_PREFIX}/source-maps") > MAX_MAP_BYTES
    application, sent = await _drive(f"{API_PREFIX}/source-maps", DEFAULT_CAP + CHUNK)

    assert _status(sent) == 200
    assert application.received == DEFAULT_CAP + CHUNK


async def test_an_import_may_carry_its_whole_document_in_any_script() -> None:
    """Five million characters, each up to four bytes in UTF-8."""
    for route in ("preview", "commit"):
        assert cap_for(f"{API_PREFIX}/check-imports/{route}") > 4 * MAX_IMPORT_CHARACTERS


async def test_only_the_named_routes_are_raised() -> None:
    assert cap_for(f"{API_PREFIX}/source-maps/symbolicate") == DEFAULT_CAP
    assert cap_for(f"{API_PREFIX}/auth/signup") == DEFAULT_CAP


async def test_a_websocket_is_not_touched() -> None:
    application, _ = await _drive(f"{API_PREFIX}/logs/tail", 0, kind="websocket")

    assert application.called


def test_the_largest_cap_is_what_the_edge_must_allow() -> None:
    assert max(DEFAULT_CAP, *body_limit.RAISED_CAPS.values()) == body_limit.LARGEST_CAP


class _Login(BaseModel):
    email: str


def test_a_json_route_answers_413_through_fastapi_s_own_body_parsing() -> None:
    """FastAPI turns any error while reading a body into a 400 — except an
    HTTPException, which is how a body cut off mid-stream still says 413."""
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestBodyLimitMiddleware)

    @app.post(LOGIN)
    async def login(body: _Login) -> dict[str, str]:
        return {"email": body.email}

    def oversized() -> Iterator[bytes]:
        yield b'{"email": "'
        for _ in range(DEFAULT_CAP // CHUNK + 1):
            yield b"x" * CHUNK
        yield b'"}'

    response = TestClient(app).post(LOGIN, content=oversized())

    assert response.status_code == 413
    assert response.json()["type"] == problems.CONTENT_TOO_LARGE.uri
