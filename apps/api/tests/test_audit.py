"""What the audit log records, and what it must never record.

Two properties are held here. The first is that the security-relevant events
actually produce an entry — the log's whole value is that it is complete, and
history it did not capture cannot be captured later. The second is that an
entry is worth nothing to whoever steals it: no plaintext key, no password
hash, no session token, in any column.

The entries are collected from a fake database session rather than read back
from Postgres. That is deliberate: what is under test is whether the service
layer writes them at all, and into the caller's own session.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from fake_stores import use_fake_second_factors

from nexovue_api.audit import service as audit
from nexovue_api.auth.clients import ClientDescription
from nexovue_api.auth.credentials import hash_password
from nexovue_api.auth.sessions import Session
from nexovue_api.data.postgres.models import ApiKey, AuditLogEntry, Organization, User
from nexovue_api.deps import get_app_settings, get_db_session, get_session_store
from nexovue_api.settings import API_PREFIX, Settings

PASSWORD = "correct-horse-battery"
PLAINTEXT_KEY = "nvk_live_secretsecretsecretsec"


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


def _api_key(organization: Organization) -> ApiKey:
    from nexovue_api.signup.apikeys import hash_api_key

    key = ApiKey(
        org_id=organization.id,
        project_id=uuid.uuid4(),
        name="default",
        key_prefix=PLAINTEXT_KEY[:16],
        key_hash=hash_api_key(PLAINTEXT_KEY),
    )
    key.id = uuid.uuid4()
    return key


class RecordingDbSession:
    """A session that keeps what was written to it and answers the login lookup."""

    def __init__(self, identity: tuple[User, Organization] | None = None) -> None:
        self._identity = identity
        self.written: list[Any] = []
        # Counted because a refused sign-in has to commit before its 401
        # unwinds the request; see `auth.service._record_refusal`.
        self.commits = 0

    def add(self, entity: Any) -> None:
        self.written.append(entity)

    async def commit(self) -> None:
        self.commits += 1

    async def execute(self, statement: Any) -> Any:
        return _FakeResult(self._identity)

    @property
    def entries(self) -> list[AuditLogEntry]:
        return [row for row in self.written if isinstance(row, AuditLogEntry)]

    @property
    def actions(self) -> list[str]:
        return [entry.action for entry in self.entries]


class _FakeResult:
    def __init__(self, row: tuple[User, Organization] | None) -> None:
        self._row = row

    def first(self) -> tuple[User, Organization] | None:
        return self._row


class FakeSessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    async def create(
        self, *, user_id: str, org_id: str, client: ClientDescription | None = None
    ) -> tuple[str, Session]:
        token = f"token-{len(self.sessions)}"
        session = Session(user_id=user_id, org_id=org_id, created_at=datetime.now(UTC))
        self.sessions[token] = session
        return token, session

    async def get(self, token: str) -> Session | None:
        return self.sessions.get(token)

    async def revoke(self, token: str) -> None:
        self.sessions.pop(token, None)


@pytest.fixture
def organization() -> Organization:
    return _organization()


@pytest.fixture
def user(organization: Organization) -> User:
    return _user(organization)


@pytest.fixture
def db(user: User, organization: Organization) -> RecordingDbSession:
    return RecordingDbSession((user, organization))


def _build_app(db: RecordingDbSession, store: FakeSessionStore) -> Any:
    from nexovue_api.main import create_app

    settings = _settings()
    app = create_app(settings)

    async def db_override() -> AsyncIterator[Any]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_session_store] = lambda: store
    # Sign-in asks whether a second factor is required, so every suite that
    # drives it needs that store too.
    use_fake_second_factors(app)
    return app


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


# ------------------------------------------------------- what each event records


def test_creating_a_tenant_records_the_organization_and_its_first_key(
    organization: Organization, user: User
) -> None:
    db = RecordingDbSession()

    audit.record_organization_created(db, organization=organization, owner=user)  # type: ignore[arg-type]
    audit.record_api_key_created(db, api_key=_api_key(organization), actor_user_id=user.id)  # type: ignore[arg-type]

    assert db.actions == [audit.ORGANIZATION_CREATED, audit.API_KEY_CREATED]
    assert all(entry.org_id == organization.id for entry in db.entries)
    assert all(entry.actor_user_id == user.id for entry in db.entries)


def test_an_entry_names_its_target_by_id_and_never_by_a_credential(
    organization: Organization, user: User
) -> None:
    """`key_prefix` is seven characters of the secret; the primary key is not."""
    db = RecordingDbSession()
    key = _api_key(organization)

    audit.record_api_key_created(db, api_key=key, actor_user_id=user.id)  # type: ignore[arg-type]

    (entry,) = db.entries
    assert entry.target == f"{audit.API_KEY_TARGET}:{key.id}"
    assert key.key_prefix not in entry.target
    assert key.key_hash not in entry.target


def test_a_system_action_records_no_actor(organization: Organization) -> None:
    db = RecordingDbSession()

    audit.record_api_key_created(db, api_key=_api_key(organization), actor_user_id=None)  # type: ignore[arg-type]

    (entry,) = db.entries
    assert entry.actor_user_id is None


# --------------------------------------------------------- through the endpoints


async def test_signing_in_records_that_a_session_was_created(
    db: RecordingDbSession, user: User
) -> None:
    app = _build_app(db, FakeSessionStore())

    async with _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/auth/login", json={"email": user.email, "password": PASSWORD}
        )

    assert response.status_code == 200
    assert db.actions == [audit.SESSION_CREATED]
    (entry,) = db.entries
    assert entry.actor_user_id == user.id
    assert entry.org_id == user.org_id


async def test_a_refused_sign_in_against_a_real_account_is_recorded(
    db: RecordingDbSession, user: User
) -> None:
    """Repeated failures against one account are what brute force looks like."""
    app = _build_app(db, FakeSessionStore())

    async with _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/auth/login", json={"email": user.email, "password": "not-the-password"}
        )

    assert response.status_code == 401
    entry = db.entries[-1]
    assert entry.action == audit.SESSION_DENIED
    assert entry.org_id == user.org_id
    assert entry.actor_user_id == user.id
    assert "not-the-password" not in entry.target


async def test_a_refused_sign_in_against_an_unknown_address_records_no_address() -> None:
    """The attempt is counted; the address is not kept.

    It belongs to somebody who is not our customer, so storing it would collect
    personal data on non-users and build a list of guessed addresses.
    """
    # No identity: this session finds no account for any address.
    db = RecordingDbSession()
    app = _build_app(db, FakeSessionStore())
    stranger = "nobody@example.invalid"

    async with _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/auth/login", json={"email": stranger, "password": PASSWORD}
        )

    assert response.status_code == 401
    entry = db.entries[-1]
    assert entry.action == audit.SESSION_DENIED
    assert entry.org_id is None
    assert entry.actor_user_id is None
    assert stranger not in entry.target
    assert entry.target == audit.UNKNOWN_ACCOUNT_TARGET


async def test_a_refused_sign_in_survives_the_401(db: RecordingDbSession, user: User) -> None:
    """The refusal is committed before the exception unwinds the request.

    Without that commit the entry rolls back with the failed request, which
    would leave every brute-force attempt unrecorded.
    """
    app = _build_app(db, FakeSessionStore())

    async with _client(app) as client:
        await client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": user.email, "password": "not-the-password"},
        )

    assert db.commits >= 1


async def test_signing_out_records_the_user_whose_session_ended(
    db: RecordingDbSession, user: User
) -> None:
    app = _build_app(db, FakeSessionStore())

    async with _client(app) as client:
        await client.post(
            f"{API_PREFIX}/auth/login", json={"email": user.email, "password": PASSWORD}
        )
        response = await client.post(f"{API_PREFIX}/auth/logout")

    assert response.status_code == 204
    assert db.actions == [audit.SESSION_CREATED, audit.SESSION_ENDED]
    assert db.entries[-1].actor_user_id == user.id


async def test_signing_out_without_a_session_records_nothing(
    db: RecordingDbSession, user: User
) -> None:
    """Logout stays idempotent, and an idempotent no-op is not an event."""
    app = _build_app(db, FakeSessionStore())

    async with _client(app) as client:
        response = await client.post(f"{API_PREFIX}/auth/logout")

    assert response.status_code == 204
    assert db.entries == []


async def test_a_session_token_never_reaches_the_audit_log(
    db: RecordingDbSession, user: User
) -> None:
    """The token authorises requests; a log that carries it hands them over."""
    store = FakeSessionStore()
    app = _build_app(db, store)

    async with _client(app) as client:
        await client.post(
            f"{API_PREFIX}/auth/login", json={"email": user.email, "password": PASSWORD}
        )
        await client.post(f"{API_PREFIX}/auth/logout")

    minted = set(store.sessions) | {"token-0"}
    recorded = " ".join(f"{entry.action} {entry.target}" for entry in db.entries)
    assert all(token not in recorded for token in minted)
    assert PASSWORD not in recorded
    assert user.password_hash is not None
    assert user.password_hash not in recorded
