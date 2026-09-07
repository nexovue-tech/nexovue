"""The profile endpoints, driven through the real app (P1-72).

Postgres and Redis are replaced by dependency overrides, in the shape
`test_auth_routes.py` established. What is under test here is the *rules* —
which body is refused, which session survives, what the cookie does — and the
two structural facts that make these routes safe to expose to every role: no
route names a user, and no body carries one.

The half this file cannot prove is that a revoked session really stops
resolving against a row Postgres wrote. That is `test_profile_live.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Final

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from fastapi import FastAPI

from nexovue_api.auth.credentials import hash_password, verify_password
from nexovue_api.auth.sessions import SESSION_COOKIE_NAME, Session
from nexovue_api.data.postgres.models import NAME_MAX_LENGTH, Organization, User
from nexovue_api.deps import get_app_settings, get_db_session, get_session_store
from nexovue_api.settings import API_PREFIX, Settings

PASSWORD: Final = "correct-horse-battery"
NEW_PASSWORD: Final = "a-different-horse-entirely"
PROFILE: Final = f"{API_PREFIX}/profile"


def _settings() -> Settings:
    return Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]


class FakeSessionStore:
    """Mints and revokes tokens in a dict, with the store's real interface."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self._minted = 0

    async def create(self, *, user_id: str, org_id: str) -> tuple[str, Session]:
        self._minted += 1
        token = f"token-{self._minted}"
        session = Session(user_id=user_id, org_id=org_id, created_at=datetime.now(UTC))
        self.sessions[token] = session
        return token, session

    async def get(self, token: str) -> Session | None:
        return self.sessions.get(token)

    async def revoke(self, token: str) -> None:
        self.sessions.pop(token, None)


class FakeDbSession:
    """Answers the one statement `load_identity` and the login path both run."""

    def __init__(self, user: User | None, organization: Organization) -> None:
        self._user = user
        self._organization = organization
        self.written: list[Any] = []

    def add(self, entity: Any) -> None:
        self.written.append(entity)

    async def commit(self) -> None:
        """Nothing to commit: the fake user object is the row."""

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
    organization = Organization(slug="acme", name="Acme Inc")
    organization.id = uuid.uuid4()
    return organization


@pytest.fixture
def user(organization: Organization) -> User:
    user = User(
        org_id=organization.id,
        email="dev@nexovue.local",
        name="Dev Owner",
        password_hash=hash_password(PASSWORD),
        role="owner",
    )
    user.id = uuid.uuid4()
    return user


@pytest.fixture
def store() -> FakeSessionStore:
    return FakeSessionStore()


@pytest.fixture
def app(user: User, organization: Organization, store: FakeSessionStore) -> FastAPI:
    # Imported here so `environment` has already populated the configuration
    # `main` reads when it builds its module-level app.
    from nexovue_api.main import create_app

    settings = _settings()
    built = create_app(settings)

    async def db_override() -> AsyncIterator[Any]:
        yield FakeDbSession(user, organization)

    built.dependency_overrides[get_app_settings] = lambda: settings
    built.dependency_overrides[get_db_session] = db_override
    built.dependency_overrides[get_session_store] = lambda: store
    return built


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@asynccontextmanager
async def _signed_in(app: FastAPI, user: User) -> AsyncIterator[httpx.AsyncClient]:
    """A client holding a real session cookie, minted the way login mints one."""
    async with _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/auth/login", json={"email": user.email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        yield client


async def test_a_name_change_answers_with_the_updated_account(app: FastAPI, user: User) -> None:
    async with _signed_in(app, user) as client:
        response = await client.patch(PROFILE, json={"name": "Ada Lovelace"})

    assert response.status_code == 200
    assert response.json()["name"] == "Ada Lovelace"
    assert user.name == "Ada Lovelace"


async def test_a_name_is_stored_trimmed(app: FastAPI, user: User) -> None:
    async with _signed_in(app, user) as client:
        response = await client.patch(PROFILE, json={"name": "  Ada Lovelace  "})

    assert response.json()["name"] == "Ada Lovelace"


@pytest.mark.parametrize("blank", ["", "   ", None])
async def test_a_blank_name_clears_it_rather_than_storing_an_empty_string(
    app: FastAPI, user: User, blank: str | None
) -> None:
    """`users.name` is nullable and NULL already means "not set". Storing `""`
    would be a second value meaning the same thing."""
    async with _signed_in(app, user) as client:
        response = await client.patch(PROFILE, json={"name": blank})

    assert response.status_code == 200
    assert response.json()["name"] is None
    assert user.name is None


async def test_a_name_longer_than_the_column_is_refused(app: FastAPI, user: User) -> None:
    """Refused at the schema, not by Postgres: a 500 from a length the API knows
    about is a defect the API could have named."""
    async with _signed_in(app, user) as client:
        response = await client.patch(PROFILE, json={"name": "a" * (NAME_MAX_LENGTH + 1)})

    assert response.status_code == 422
    assert user.name == "Dev Owner"


async def test_a_password_change_needs_the_current_password(app: FastAPI, user: User) -> None:
    """**The whole reason the endpoint takes two passwords.** A signed-in but
    stolen session must not be able to lock the real owner out."""
    async with _signed_in(app, user) as client:
        response = await client.post(
            PROFILE + "/password",
            json={"current_password": "not-the-current-one", "new_password": NEW_PASSWORD},
        )

    assert response.status_code == 422
    assert verify_password(user.password_hash, PASSWORD), "the password changed anyway"
    assert user.sessions_valid_from is None, "a refused change still ended every session"


async def test_an_account_with_no_password_cannot_set_one_here(app: FastAPI, user: User) -> None:
    """An invited user (F26) has `password_hash` NULL and has proved nothing.
    Their route in is the reset link, and the refusal is worded the same so the
    endpoint never says which of the two cases it is."""
    async with _signed_in(app, user) as client:
        # Signed in first, then the hash removed: the session is real, and it is
        # the missing credential alone that the endpoint must refuse on.
        user.password_hash = None
        response = await client.post(
            PROFILE + "/password",
            json={"current_password": "anything", "new_password": NEW_PASSWORD},
        )

    assert response.status_code == 422
    assert user.password_hash is None, "a password was set without proving the old one"
    assert user.sessions_valid_from is None


async def test_the_new_password_may_not_be_the_one_already_set(app: FastAPI, user: User) -> None:
    async with _signed_in(app, user) as client:
        response = await client.post(
            PROFILE + "/password",
            json={"current_password": PASSWORD, "new_password": PASSWORD},
        )

    assert response.status_code == 422
    assert user.sessions_valid_from is None


async def test_a_short_new_password_is_held_to_the_signup_rules(app: FastAPI, user: User) -> None:
    """`auth.schemas.Password` is reused rather than restated, so this bound
    moves in one place."""
    async with _signed_in(app, user) as client:
        response = await client.post(
            PROFILE + "/password",
            json={"current_password": PASSWORD, "new_password": "short"},
        )

    assert response.status_code == 422


async def test_a_password_change_ends_every_session_and_replaces_this_one(
    app: FastAPI, user: User, store: FakeSessionStore
) -> None:
    """The asymmetry this endpoint is built around: the caller keeps a working
    session — a fresh one — and every session that existed before is refused."""
    async with _signed_in(app, user) as client:
        original_token = client.cookies[SESSION_COOKIE_NAME]
        response = await client.post(
            PROFILE + "/password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        replacement_token = client.cookies[SESSION_COOKIE_NAME]
        still_signed_in = await client.get(f"{API_PREFIX}/auth/me")

    assert response.status_code == 204
    assert verify_password(user.password_hash, NEW_PASSWORD)
    assert user.sessions_valid_from is not None, "the revocation epoch was not bumped"
    assert replacement_token != original_token, "the caller kept the session it arrived with"
    assert original_token not in store.sessions, "the old token is still in the store"
    assert still_signed_in.status_code == 200, "the caller was signed out of their own device"


async def test_signing_out_everywhere_ends_this_session_too(
    app: FastAPI, user: User, store: FakeSessionStore
) -> None:
    """`Everywhere` has to include the machine in front of the customer — it may
    be the borrowed one they are trying to get out of."""
    async with _signed_in(app, user) as client:
        token = client.cookies[SESSION_COOKIE_NAME]
        response = await client.post(PROFILE + "/sign-out-everywhere")
        afterwards = await client.get(f"{API_PREFIX}/auth/me")

    assert response.status_code == 204
    assert user.sessions_valid_from is not None
    assert token not in store.sessions
    assert SESSION_COOKIE_NAME not in client.cookies, "the cookie was left in the browser"
    assert afterwards.status_code == 401


async def test_signing_out_everywhere_asks_for_no_password(app: FastAPI, user: User) -> None:
    """Deliberate: the caller believes they are already compromised, and a
    stolen session calling this can only sign itself out alongside the owner."""
    async with _signed_in(app, user) as client:
        response = await client.post(PROFILE + "/sign-out-everywhere")

    assert response.status_code == 204


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "", {"name": "Nobody"}),
        ("POST", "/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}),
        ("POST", "/sign-out-everywhere", None),
    ],
)
async def test_every_profile_route_refuses_without_a_session(
    app: FastAPI, method: str, path: str, body: dict[str, str] | None
) -> None:
    async with _client(app) as client:
        response = await client.request(method, PROFILE + path, json=body)

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


def test_no_profile_route_can_name_another_user(app: FastAPI) -> None:
    """**The structural half of the authorization argument.** These routes are
    exempt from `test_authorization_coverage.py` because they can only ever
    affect the caller. That holds exactly as long as nothing in a path or a body
    names a user, so it is asserted rather than assumed."""
    from nexovue_api.profile.schemas import PasswordChange, ProfileUpdate

    # Read from the OpenAPI document rather than `app.routes`: this FastAPI
    # keeps an included router lazy, so `app.routes` holds the router and not
    # the paths, and a test that walked it would quietly assert nothing.
    paths = [path for path in app.openapi()["paths"] if path.startswith(PROFILE)]
    assert paths, "no profile routes were registered"
    parameterized = [path for path in paths if "{" in path]
    assert not parameterized, f"a profile route takes a path parameter: {parameterized}"

    fields = set(ProfileUpdate.model_fields) | set(PasswordChange.model_fields)
    assert not {name for name in fields if "user" in name or name == "id"}, fields
