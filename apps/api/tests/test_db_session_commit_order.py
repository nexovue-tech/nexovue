"""A write is committed before its response is sent.

FastAPI runs a yield dependency's exit *after* the response by default. The
request's session commits in that exit, so a client re-reading on a 2xx could
read before the commit — a channel just created missing from the list (found by
`alerts.spec.ts`, `docs/changes/commit-before-response/`) — and a failed commit
followed a success the client had already been told.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from starlette.types import Message

from nexovue_api.deps import DbSession, get_resources


class _FakeSession:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def commit(self) -> None:
        self._events.append("commit")

    async def rollback(self) -> None:
        self._events.append("rollback")


class _FakeResources:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    @asynccontextmanager
    async def session_factory(self) -> AsyncIterator[_FakeSession]:
        yield _FakeSession(self._events)


async def _post(app: FastAPI, events: list[str]) -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/things",
        "raw_path": b"/things",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            events.append("response")

    await app(scope, receive, send)


async def test_the_session_commits_before_the_response_starts() -> None:
    events: list[str] = []
    app = FastAPI()
    app.dependency_overrides[get_resources] = lambda: _FakeResources(events)

    @app.post("/things", status_code=201)
    async def create(db: DbSession) -> dict[str, str]:
        events.append("handler")
        return {"ok": "yes"}

    await _post(app, events)

    assert events == ["handler", "commit", "response"]
