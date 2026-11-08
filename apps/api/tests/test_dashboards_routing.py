"""Each dashboard path reaches its own handler, through the real app (F20, T2.13, AC24).

The batch has a prefix of its own so no route order is load-bearing (D41), and
`/dashboards/templates` is declared before `/dashboards/{dashboard_id}`, whose wildcard
would otherwise take it as an id. Matched the way Starlette matches a request, in the
order `create_app()` installed the routes: a real request, read for the route it reached.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Final

import pytest
from environment import TEST_ENVIRONMENT

from nexovue_api.deps import get_current_session, get_resources
from nexovue_api.errors.exceptions import UnauthenticatedError
from nexovue_api.main import create_app
from nexovue_api.settings import API_PREFIX, Settings

PATHS: Final = [
    ("GET", "/dashboard-panels/read", "read_panels"),
    ("GET", "/dashboards/templates", "list_templates"),
    ("GET", "/dashboards", "list_dashboards"),
    ("POST", "/dashboards", "create_dashboard"),
    ("GET", "/dashboards/0190e3f4-0000-7000-8000-000000000001", "get_dashboard"),
    ("PUT", "/dashboards/0190e3f4-0000-7000-8000-000000000001", "replace_dashboard"),
    ("DELETE", "/dashboards/0190e3f4-0000-7000-8000-000000000001", "delete_dashboard"),
    ("GET", "/dashboards/0190e3f4-0000-7000-8000-000000000001/context", "dashboard_context"),
]


async def _handler(method: str, path: str) -> str:
    """Which handler a real request reaches, read from the route Starlette records.

    Every request is refused at authentication, so nothing past routing runs."""
    app = create_app(Settings(**TEST_ENVIRONMENT))  # type: ignore[arg-type]

    def refused() -> None:
        raise UnauthenticatedError("routing test")

    app.dependency_overrides[get_current_session] = refused
    app.dependency_overrides[get_resources] = refused
    full_path = f"{API_PREFIX}{path}"
    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": full_path,
        "raw_path": full_path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("10.0.0.1", 40_000),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_: MutableMapping[str, Any]) -> None:
        return None

    await app(scope, receive, send)
    route = scope.get("route")
    return str(getattr(route, "name", "<no route>"))


@pytest.mark.parametrize(("method", "path", "handler"), PATHS)
async def test_ac24_each_dashboard_path_reaches_its_own_handler(
    method: str, path: str, handler: str
) -> None:
    assert await _handler(method, path) == handler


def test_the_batch_and_its_context_are_built_once_with_the_app() -> None:
    app = create_app(Settings(**TEST_ENVIRONMENT))  # type: ignore[arg-type]

    assert app.state.dashboard_reads is not None
