"""Hosted signup can be switched off (F31, FR30, AC31, D10).

nexovue's cloud onboards customers by hand after they pay (ADR-008); a
self-hosted install keeps signup open. With it off, `POST /signup` is refused
before anything is read or written, and the signup screen can ask why.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from environment import TEST_ENVIRONMENT
from fake_stores import use_fake_second_factors

from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.settings import API_PREFIX, Settings


class _UntouchedDatabase:
    """Fails the test if the refused request reaches the database at all."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"a refused signup touched the database ({name})")


def _app(*, signup_enabled: bool) -> Any:
    from nexovue_api.main import create_app

    settings = Settings(**{**TEST_ENVIRONMENT, "SIGNUP_ENABLED": str(signup_enabled).lower()})  # type: ignore[arg-type]
    app = create_app(settings)

    async def database() -> AsyncIterator[Any]:
        yield _UntouchedDatabase()

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = database
    use_fake_second_factors(app)
    return app


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


SIGNUP = {
    "organization_name": "Acme",
    "email": "owner@example.test",
    "password": "correct horse battery staple",
}


async def test_signup_is_refused_when_switched_off_and_says_how_to_buy() -> None:
    async with _client(_app(signup_enabled=False)) as client:
        response = await client.post(f"{API_PREFIX}/signup", json=SIGNUP)

    assert response.status_code == 403
    assert "set up after purchase" in response.json()["detail"]


async def test_the_screen_can_ask_whether_signup_is_open() -> None:
    async with _client(_app(signup_enabled=False)) as closed:
        assert (await closed.get(f"{API_PREFIX}/system/signup")).json() == {"enabled": False}
    async with _client(_app(signup_enabled=True)) as open_:
        assert (await open_.get(f"{API_PREFIX}/system/signup")).json() == {"enabled": True}
