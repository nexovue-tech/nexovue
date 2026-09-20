"""Who an assistant is reading as (F36, FR33).

The MCP endpoint takes the same `Authorization: Bearer nvt_live_…` the read API
takes, resolved by the same code, so a tool can never see what its endpoint
would refuse. A thin ASGI wrapper does it before the SDK's app is entered, and
puts the resolved reader in the request's scope — **not** in a header a tool
re-reads, and not in a context variable: the SDK moves tool bodies between task
groups, and `scope["state"]` is the one place that is documented to survive.

A refusal here is a JSON body with one sentence, because the thing reading it is
a model that will repeat it to a person.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from nexovue_api.apitokens import service as api_tokens
from nexovue_api.apitokens import usage as token_usage
from nexovue_api.apitokens.tokens import looks_like_api_token
from nexovue_api.auth.sessions import bearer_token_from_headers
from nexovue_api.errors.exceptions import UnauthenticatedError

READER_KEY: Final = "assistant_reader"
# The API's pools, carried on the request rather than fetched from the app: a
# mounted sub-application sets `scope["app"]` to *itself*, so a tool reading the
# app would find the MCP server and not the API that owns the connections.
RESOURCES_KEY: Final = "assistant_resources"

NO_TOKEN: Final = (
    "This endpoint needs a nexovue API token. Create one in the console under "
    "Settings → API tokens, then send it as an Authorization: Bearer header."
)


class AssistantAuthentication:
    """Authenticates every MCP request, and refuses the rest in one sentence."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        presented = bearer_token_from_headers(Headers(scope=scope))
        if presented is None or not looks_like_api_token(presented):
            await _refuse(send, NO_TOKEN)
            return

        resources = getattr(getattr(scope.get("app"), "state", None), "resources", None)
        if resources is None:  # pragma: no cover — the app always has its pools
            await _refuse(send, "This server is not ready.")
            return

        now = datetime.now(UTC)
        async with resources.session_factory() as session:
            try:
                token = await api_tokens.token_for(session, presented, now=now)
            except UnauthenticatedError as refused:
                await _refuse(send, refused.detail)
                return
            await token_usage.count_request(resources.redis, session, token=token, now=now)
            reader = {
                "org_id": token.org_id,
                "project_scope": token.project_id,
                "token_id": token.id,
            }
            await session.commit()

        state = scope.setdefault("state", {})
        state[READER_KEY] = reader
        state[RESOURCES_KEY] = resources
        await self._app(scope, receive, send)


async def _refuse(send: Send, detail: str) -> None:
    body = json.dumps({"error": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="nexovue"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def reader_of(scope: Scope) -> dict[str, Any]:
    """The reader this request authenticated as. Absent means a bug, not a guess."""
    reader: dict[str, Any] | None = scope.get("state", {}).get(READER_KEY)
    if reader is None:  # pragma: no cover — the wrapper refuses before this runs
        raise UnauthenticatedError(NO_TOKEN)
    return reader


def resources_of(scope: Scope) -> Any:
    """The API's pools, put here by the wrapper on the way in."""
    resources = scope.get("state", {}).get(RESOURCES_KEY)
    if resources is None:  # pragma: no cover — set on every authenticated request
        raise UnauthenticatedError(NO_TOKEN)
    return resources
