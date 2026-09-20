"""What the read API publishes about itself (F36, FR17, FR20).

The console's own OpenAPI document describes every endpoint the console uses,
most of which an API token is refused. A customer generating a client from that
gets methods that answer 403 and no way to tell their mistake from our fault.

So there is a second document, and these tests hold it to three promises: it
describes exactly the read surface and nothing else, it says a bearer token is
needed, and it contains no operation that changes anything. It is built from the
routes rather than from a list, so the surface and its description are one fact;
these tests are what prove that stays true.
"""

from __future__ import annotations

from typing import Any, Final

import pytest
from environment import TEST_ENVIRONMENT
from fastapi import FastAPI

from nexovue_api.readsurface.router import READ_SPEC_PATH
from nexovue_api.readsurface.spec import (
    BEARER_SCHEME,
    TITLE,
    read_surface_spec,
    surface_by_path,
)
from nexovue_api.settings import API_PREFIX, Settings

WRITE_METHODS: Final = frozenset({"post", "put", "patch", "delete"})

# Endpoints the console uses and a token may never reach. Named rather than
# counted: "not in the document" is the promise, and a count would pass even if
# the wrong ones were missing. Paths are relative to the server the document
# declares, which is why there is no prefix on them.
CONSOLE_ONLY: Final = (
    "/auth/login",
    "/members",
    "/api-keys",
    "/api-tokens",
    "/audit",
    "/support-access",
    "/billing/checkout",
)


@pytest.fixture(scope="module")
def app() -> FastAPI:
    from nexovue_api.main import create_app

    return create_app(Settings(**TEST_ENVIRONMENT))  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def spec(app: FastAPI) -> dict[str, Any]:
    return read_surface_spec(app)


def test_it_describes_exactly_the_routes_a_token_may_reach(
    app: FastAPI, spec: dict[str, Any]
) -> None:
    """One fact, not two: the document is derived from the routes themselves."""
    reachable = set(surface_by_path(app))

    assert set(spec["paths"]) == reachable
    assert reachable, "no route takes the reader; the surface cannot be empty"


def test_a_nested_router_publishes_the_path_it_is_served_at(spec: dict[str, Any]) -> None:
    """`/logs/search` lives on a router included inside another, and its own
    path is `/search`. Publishing that described an endpoint nobody can call."""
    assert "/logs/search" in spec["paths"]
    assert "/search" not in spec["paths"]


def test_the_prefix_is_declared_once_as_the_server(spec: dict[str, Any]) -> None:
    """So a generated client calls /api/v1/services, not /services."""
    assert spec["servers"] == [{"url": API_PREFIX}]
    assert all(not path.startswith(API_PREFIX) for path in spec["paths"])


def test_a_deployment_that_knows_its_own_address_publishes_it(app: FastAPI) -> None:
    absolute = read_surface_spec(app, base_url="https://api.nexovue.com/")

    assert absolute["servers"] == [{"url": f"https://api.nexovue.com{API_PREFIX}"}]


def test_every_operation_in_it_is_a_read(spec: dict[str, Any]) -> None:
    writes = {
        f"{method.upper()} {path}"
        for path, operations in spec["paths"].items()
        for method in operations
        if method in WRITE_METHODS
    }

    assert not writes, f"the read API describes operations that change things: {sorted(writes)}"


@pytest.mark.parametrize("path", CONSOLE_ONLY)
def test_the_consoles_own_endpoints_are_not_in_it(spec: dict[str, Any], path: str) -> None:
    assert path not in spec["paths"]


def test_it_says_a_token_is_needed(spec: dict[str, Any]) -> None:
    """Without this a generated client sends no credential and gets 401s that
    look like our fault."""
    schemes = spec["components"]["securitySchemes"]

    assert schemes[BEARER_SCHEME] == {
        "type": "http",
        "scheme": "bearer",
        "description": "An API token from Settings → API tokens (`nvt_live_…`).",
    }
    assert spec["security"] == [{BEARER_SCHEME: []}]


def test_it_is_a_document_a_generator_can_read(spec: dict[str, Any]) -> None:
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == TITLE
    assert "Settings → API tokens" in spec["info"]["description"]


def test_the_console_document_still_holds_everything(app: FastAPI, spec: dict[str, Any]) -> None:
    """The two documents are different on purpose. The console's is the whole
    API; a reader that returned it would publish every admin route to anyone."""
    whole = app.openapi()

    assert len(whole["paths"]) > len(spec["paths"])
    assert f"{API_PREFIX}/members" in whole["paths"]


async def test_the_document_is_served_without_a_credential(app: FastAPI) -> None:
    """A description nobody can fetch without a token is a description nobody
    fetches: the generator runs before the customer has wired one up."""
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        answer = await client.get(f"{API_PREFIX}{READ_SPEC_PATH}")

    assert answer.status_code == 200
    assert answer.json()["info"]["title"] == TITLE
