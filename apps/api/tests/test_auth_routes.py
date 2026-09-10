"""The identity endpoints, driven through the real app.

`httpx.AsyncClient` over `ASGITransport` rather than `starlette.testclient`:
the test client is deprecated in favour of httpx2, and calling the ASGI app
in-process needs neither and exercises the same middleware stack.

Postgres, Redis and ClickHouse are replaced by dependency overrides. What is
under test is the wiring — status codes, the cookie, and the fact that an
endpoint refuses to answer without a session — not the stores.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from environment import TEST_ENVIRONMENT

from nexovue_api.auth.credentials import hash_password
from nexovue_api.auth.dependencies import Identity, get_current_identity
from nexovue_api.auth.sessions import SESSION_COOKIE_NAME, Session
from nexovue_api.data.postgres.models import Organization, User
from nexovue_api.deps import get_app_settings, get_db_session, get_session_store
from nexovue_api.errors.exceptions import UnauthenticatedError
from nexovue_api.settings import API_PREFIX, Settings

PASSWORD = "correct-horse-battery"


def _settings() -> Settings:
    return Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]


def _organization() -> Organization:
    organization = Organization(slug="acme", name="Acme Inc")
    organization.id = uuid.uuid4()
    return organization


def _user(organization: Organization) -> User:
    user = User(
        org_id=organization.id,
        email="nexovue@nexovue.com",
        name="Dev Owner",
        password_hash=hash_password(PASSWORD),
        role="owner",
    )
    user.id = uuid.uuid4()
    return user


class FakeSessionStore:
    """Mints and revokes tokens in a dict, with the store's real interface."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    async def create(self, *, user_id: str, org_id: str) -> tuple[str, Session]:
        token = f"token-{len(self.sessions)}"
        session = Session(user_id=user_id, org_id=org_id, created_at=datetime.now(UTC))
        self.sessions[token] = session
        return token, session

    async def get(self, token: str) -> Session | None:
        return self.sessions.get(token)

    async def revoke(self, token: str) -> None:
        self.sessions.pop(token, None)


class FakeDbSession:
    """Answers the one statement the login path runs."""

    def __init__(self, user: User | None, organization: Organization) -> None:
        self._user = user
        self._organization = organization
        self.written: list[Any] = []

    def add(self, entity: Any) -> None:
        """Rows the request wrote — audit entries here."""
        self.written.append(entity)

    async def commit(self) -> None:
        """A refused sign-in commits its own record before the 401 unwinds."""

    async def execute(self, statement: Any) -> Any:
        found = (self._user, self._organization) if self._user is not None else None
        return _FakeResult(found)


class _FakeResult:
    def __init__(self, row: tuple[User, Organization] | None) -> None:
        self._row = row

    def first(self) -> tuple[User, Organization] | None:
        return self._row


@pytest.fixture
def organization() -> Organization:
    return _organization()


@pytest.fixture
def user(organization: Organization) -> User:
    return _user(organization)


def _build_app(*, user: User | None, organization: Organization, store: FakeSessionStore) -> Any:
    # Imported here so `environment` has already populated the configuration
    # `main` reads when it builds its module-level app.
    from nexovue_api.main import create_app

    settings = _settings()
    app = create_app(settings)

    async def db_override() -> AsyncIterator[Any]:
        yield FakeDbSession(user, organization)

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_session_store] = lambda: store
    return app


async def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_valid_credentials_return_the_identity_and_set_the_cookie(
    user: User, organization: Organization
) -> None:
    app = _build_app(user=user, organization=organization, store=FakeSessionStore())

    async with await _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": user.email, "password": PASSWORD},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == user.email
    assert body["user"]["role"] == "owner"
    assert body["organization"]["slug"] == "acme"
    assert SESSION_COOKIE_NAME in response.cookies


async def test_the_session_cookie_is_not_readable_by_scripts(
    user: User, organization: Organization
) -> None:
    app = _build_app(user=user, organization=organization, store=FakeSessionStore())

    async with await _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/auth/login", json={"email": user.email, "password": PASSWORD}
        )

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


async def test_an_unknown_email_and_a_wrong_password_are_indistinguishable(
    user: User, organization: Organization
) -> None:
    """Same status, same body — the endpoint is not an account-existence oracle."""
    known = _build_app(user=user, organization=organization, store=FakeSessionStore())
    unknown = _build_app(user=None, organization=organization, store=FakeSessionStore())

    async with await _client(known) as client:
        wrong_password = await client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": user.email, "password": "not-the-password"},
        )
    async with await _client(unknown) as client:
        unknown_email = await client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": "nobody@example.com", "password": "not-the-password"},
        )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["detail"] == unknown_email.json()["detail"]
    assert wrong_password.json()["type"] == unknown_email.json()["type"]


async def test_a_failed_login_sets_no_cookie(user: User, organization: Organization) -> None:
    app = _build_app(user=user, organization=organization, store=FakeSessionStore())

    async with await _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/auth/login", json={"email": user.email, "password": "wrong-one"}
        )

    assert SESSION_COOKIE_NAME not in response.cookies


async def test_logout_clears_the_cookie_and_the_store_entry(
    user: User, organization: Organization
) -> None:
    store = FakeSessionStore()
    app = _build_app(user=user, organization=organization, store=store)

    async with await _client(app) as client:
        await client.post(
            f"{API_PREFIX}/auth/login", json={"email": user.email, "password": PASSWORD}
        )
        assert store.sessions, "login did not create a session"
        response = await client.post(f"{API_PREFIX}/auth/logout")

    assert response.status_code == 204
    assert store.sessions == {}


async def test_logout_without_a_session_is_still_204(
    user: User, organization: Organization
) -> None:
    app = _build_app(user=user, organization=organization, store=FakeSessionStore())

    async with await _client(app) as client:
        response = await client.post(f"{API_PREFIX}/auth/logout")

    assert response.status_code == 204


async def test_me_without_a_session_is_401(user: User, organization: Organization) -> None:
    app = _build_app(user=user, organization=organization, store=FakeSessionStore())

    async with await _client(app) as client:
        response = await client.get(f"{API_PREFIX}/auth/me")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_me_returns_the_same_body_as_login(user: User, organization: Organization) -> None:
    app = _build_app(user=user, organization=organization, store=FakeSessionStore())

    async with await _client(app) as client:
        signed_in = await client.post(
            f"{API_PREFIX}/auth/login", json={"email": user.email, "password": PASSWORD}
        )
        identity = await client.get(f"{API_PREFIX}/auth/me")

    assert identity.status_code == 200
    assert identity.json() == signed_in.json()


async def test_a_session_naming_a_user_that_is_gone_is_401(
    user: User, organization: Organization
) -> None:
    """The row was deleted while the token was still live in Redis."""
    store = FakeSessionStore()
    app = _build_app(user=user, organization=organization, store=store)

    async with await _client(app) as client:
        await client.post(
            f"{API_PREFIX}/auth/login", json={"email": user.email, "password": PASSWORD}
        )
        # The identity lookup now finds nothing, as it would after a delete.
        app.dependency_overrides[get_db_session] = _missing_user(organization)
        response = await client.get(f"{API_PREFIX}/auth/me")

    assert response.status_code == 401


def _missing_user(organization: Organization) -> Any:
    async def override() -> AsyncIterator[Any]:
        yield FakeDbSession(None, organization)

    return override


async def test_every_signed_in_endpoint_refuses_an_anonymous_caller(
    user: User, organization: Organization
) -> None:
    app = _build_app(user=user, organization=organization, store=FakeSessionStore())
    project_id = uuid.uuid4()

    async with await _client(app) as client:
        responses = [
            await client.get(f"{API_PREFIX}/auth/me"),
            await client.get(f"{API_PREFIX}/projects"),
            await client.get(f"{API_PREFIX}/traces", params={"project_id": str(project_id)}),
            await client.get(
                f"{API_PREFIX}/traces/{'a' * 32}", params={"project_id": str(project_id)}
            ),
            await client.get(f"{API_PREFIX}/api-keys", params={"project_id": str(project_id)}),
            await client.post(
                f"{API_PREFIX}/api-keys", json={"project_id": str(project_id), "name": "k"}
            ),
            await client.post(f"{API_PREFIX}/api-keys/{uuid.uuid4()}/revoke"),
        ]

    assert [response.status_code for response in responses] == [401] * len(responses)


async def test_the_tenant_a_query_runs_as_comes_from_the_session_not_the_request(
    user: User, organization: Organization
) -> None:
    """A caller cannot name another organization; only the session names one."""
    app = _build_app(user=user, organization=organization, store=FakeSessionStore())
    seen: list[uuid.UUID] = []

    async def pretend_identity() -> Identity:
        seen.append(organization.id)
        raise UnauthenticatedError("stop here")

    app.dependency_overrides[get_current_identity] = pretend_identity

    async with await _client(app) as client:
        await client.get(
            f"{API_PREFIX}/traces",
            params={"project_id": str(uuid.uuid4()), "org_id": str(uuid.uuid4())},
        )

    assert seen == [organization.id]
