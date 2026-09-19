"""The admin app and the customer app do not recognise each other (F31, AC27, ADR-016).

A customer session — as a cookie or a bearer header — is nothing to the admin
app; a staff cookie is nothing to the customer app; neither app serves the
other's routes; and every admin route but sign-in refuses without a staff
session.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from fastapi.routing import APIRoute

from nexovue_api.admin.dependencies import get_staff_sessions
from nexovue_api.auth.sessions import SESSION_COOKIE_NAME, Session
from nexovue_api.data.postgres.models import StaffAccount
from nexovue_api.deps import get_app_settings, get_db_session, get_session_store
from nexovue_api.settings import API_PREFIX, Settings
from nexovue_api.staff.sessions import STAFF_COOKIE_NAME, StaffSession

CUSTOMER_TOKEN = "customer-token"
STAFF_TOKEN = "staff-token"


class _CustomerSessions:
    """Knows one customer token, and nothing else."""

    async def get(self, token: str) -> Session | None:
        if token != CUSTOMER_TOKEN:
            return None
        return Session(
            user_id=str(uuid.uuid4()), org_id=str(uuid.uuid4()), created_at=datetime.now(UTC)
        )

    peek = get


class _StaffSessions:
    """Knows one staff token, and nothing else."""

    async def get(self, token: str) -> StaffSession | None:
        if token != STAFF_TOKEN:
            return None
        return StaffSession(staff_id=uuid.uuid4(), created_at=datetime.now(UTC))


async def _no_database() -> AsyncIterator[Any]:
    # Every refusal here happens before a query; a query would be the defect.
    yield None


def _settings() -> Settings:
    return Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]


def _admin_app() -> Any:
    from nexovue_api.admin.main import create_admin_app

    settings = _settings()
    app = create_admin_app(settings)
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = _no_database
    app.dependency_overrides[get_staff_sessions] = _StaffSessions
    app.dependency_overrides[get_session_store] = _CustomerSessions
    return app


def _customer_app() -> Any:
    from nexovue_api.main import create_app

    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = _no_database
    app.dependency_overrides[get_session_store] = _CustomerSessions
    return app


def _client(app: Any, **options: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver", **options
    )


@pytest.mark.parametrize(
    "credential",
    [
        {"cookies": {SESSION_COOKIE_NAME: CUSTOMER_TOKEN}},
        {"headers": {"Authorization": f"Bearer {CUSTOMER_TOKEN}"}},
    ],
    ids=["customer cookie", "customer bearer"],
)
async def test_a_customer_session_is_nothing_to_the_admin_app(credential: dict[str, Any]) -> None:
    async with _client(_admin_app(), **credential) as client:
        response = await client.get("/admin/v1/me")
    assert response.status_code == 401


async def test_a_staff_cookie_is_nothing_to_the_customer_app() -> None:
    async with _client(_customer_app(), cookies={STAFF_COOKIE_NAME: STAFF_TOKEN}) as client:
        response = await client.get(f"{API_PREFIX}/auth/me")
    assert response.status_code == 401


async def test_neither_app_serves_the_others_routes() -> None:
    async with _client(_admin_app()) as admin:
        assert (await admin.get(f"{API_PREFIX}/auth/me")).status_code == 404
    async with _client(_customer_app()) as customer:
        assert (await customer.get("/admin/v1/me")).status_code == 404


async def test_every_admin_route_but_sign_in_refuses_without_a_staff_session() -> None:
    from nexovue_api.admin import router as admin_router
    from nexovue_api.admin.main import ADMIN_PREFIX

    # The router's own list: FastAPI keeps an included router nested rather than
    # flattening its routes into the app's.
    routes = [route for route in admin_router.router.routes if isinstance(route, APIRoute)]
    assert routes, "the admin app serves no admin routes"
    async with _client(_admin_app()) as client:
        for route in routes:
            if route.path == "/sign-in":
                continue
            path = ADMIN_PREFIX + route.path.replace("{org_id}", str(uuid.uuid4()))
            for method in route.methods or set():
                response = await client.request(method, path, params={"q": "acme"})
                assert response.status_code == 401, f"{method} {route.path}"


class _StaffDatabase:
    """Answers the one read a signed-in staff request makes: the account."""

    async def get(self, _model: object, staff_id: uuid.UUID) -> StaffAccount:
        account = StaffAccount(email="raj@nexovue.test", name="Raj", password_hash="x")
        account.id = staff_id
        return account


async def test_a_staff_session_reaches_the_admin_app() -> None:
    app = _admin_app()

    async def staff_database() -> AsyncIterator[Any]:
        yield _StaffDatabase()

    app.dependency_overrides[get_db_session] = staff_database
    async with _client(app, cookies={STAFF_COOKIE_NAME: STAFF_TOKEN}) as client:
        response = await client.get("/admin/v1/me")
    assert response.status_code == 200
    assert response.json()["email"] == "raj@nexovue.test"
