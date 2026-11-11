"""F5's routes refuse an API token (plan T1.3; spec AC3, BR5).

A fix route is a person's: it asks for `CurrentIdentity`, so a token is refused below
it with 403 before any store is read, and it stays off `THE_SURFACE`
(`test_read_surface_structure.py`). Driven through the real app with the stores replaced:
what is under test is the gate, not a read.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import AsyncIterator
from typing import Any, Final

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from fastapi import FastAPI
from fastapi.routing import APIRoute

from nexovue_api.apitokens.tokens import API_TOKEN_PREFIX, TOKENS_ARE_READ_ONLY
from nexovue_api.auth.dependencies import get_current_identity, get_current_reader
from nexovue_api.deps import get_app_settings, get_db_session, get_session_store
from nexovue_api.findings import router as findings_router
from nexovue_api.settings import API_PREFIX, Settings

FIX_PATHS: Final = ("/findings/{finding_id}/fixes",)


class _NothingStored:
    """A store a refused request must never reach."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"a refused request reached the store ({name})")


@pytest.fixture
def app() -> FastAPI:
    from nexovue_api.main import create_app

    settings = Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]
    built = create_app(settings)

    async def no_db() -> AsyncIterator[Any]:
        yield _NothingStored()

    built.dependency_overrides[get_app_settings] = lambda: settings
    built.dependency_overrides[get_db_session] = no_db
    built.dependency_overrides[get_session_store] = lambda: _NothingStored()
    return built


async def test_ac3_an_api_token_gets_403_on_every_fix_route(app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for path in FIX_PATHS:
            response = await client.get(
                API_PREFIX + path.format(finding_id=uuid.uuid4()),
                headers={"authorization": f"Bearer {API_TOKEN_PREFIX}{'a' * 40}"},
            )

            assert response.status_code == 403, path
            assert response.json()["detail"] == TOKENS_ARE_READ_ONLY


def test_ac3_every_fix_route_asks_for_a_person_and_never_a_reader() -> None:
    routes = [
        route
        for route in findings_router.router.routes
        if isinstance(route, APIRoute) and route.path.removeprefix("/findings") in _suffixes()
    ]

    assert {route.path for route in routes} == set(FIX_PATHS)
    for route in routes:
        calls = {dependency.call for dependency in route.dependant.dependencies}
        assert get_current_identity in calls, route.path
        assert get_current_reader not in calls, route.path
        assert "CurrentReader" not in inspect.getsource(route.endpoint), route.path


def _suffixes() -> set[str]:
    return {path.removeprefix("/findings") for path in FIX_PATHS}
