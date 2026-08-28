"""Withdrawing an ingest key.

The store here answers from the values a statement *binds*, never from what the
caller meant. A lookup that stopped filtering `org_id` would no longer bind one,
and would find nothing — so a query that loses its tenancy predicate fails these
tests rather than quietly reaching another organization's key.

`test_apikeys_live.py` proves the same behaviour against real Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from environment import TEST_ENVIRONMENT

from nexovue_api.apikeys import service
from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.auth.dependencies import get_current_identity as current_identity_dependency
from nexovue_api.data.postgres.models import ApiKey, AuditLogEntry, Organization, Project, User
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.errors.exceptions import NotFoundError, PermissionDeniedError
from nexovue_api.settings import API_PREFIX, Settings

PLAINTEXT_KEY = "nvk_live_secretsecretsecretsec"


def _settings() -> Settings:
    return Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]


def _bound_values(statement: Any) -> set[Any]:
    """Every literal a SQLAlchemy statement carries into the driver."""
    return set(statement.compile().params.values())


class Tenant:
    """An organization with one project and one live key."""

    def __init__(self, slug: str) -> None:
        self.organization = Organization(slug=slug, name=slug.title())
        self.organization.id = uuid.uuid4()

        self.user = User(
            org_id=self.organization.id, email=f"owner@{slug}.test", role="owner", name=None
        )
        self.user.id = uuid.uuid4()

        self.project = Project(org_id=self.organization.id, slug="production", name="Production")
        self.project.id = uuid.uuid4()

        self.key = ApiKey(
            org_id=self.organization.id,
            project_id=self.project.id,
            name="default",
            key_prefix=PLAINTEXT_KEY[:16],
            key_hash="0" * 64,
        )
        self.key.id = uuid.uuid4()
        self.key.created_at = datetime.now(UTC) - timedelta(days=1)
        self.key.last_used_at = None
        self.key.revoked_at = None

    @property
    def identity(self) -> Identity:
        return Identity(user=self.user, organization=self.organization)


class FakeControlPlane:
    """Keys and projects, answered only from what a statement binds."""

    def __init__(self, tenants: Sequence[Tenant]) -> None:
        self._tenants = list(tenants)
        self.written: list[Any] = []

    def add(self, entity: Any) -> None:
        self.written.append(entity)

    async def scalar(self, statement: Any) -> Any:
        """`lock_key` and `find_project`: a row only when its owner is bound too."""
        values = _bound_values(statement)
        for tenant in self._tenants:
            if tenant.organization.id not in values:
                continue
            if tenant.key.id in values:
                return tenant.key
            if tenant.project.id in values:
                return tenant.project
        return None

    async def scalars(self, statement: Any) -> _Rows:
        """`list_keys`: whichever organization and project the statement binds."""
        values = _bound_values(statement)
        return _Rows(
            [
                tenant.key
                for tenant in self._tenants
                if tenant.organization.id in values and tenant.project.id in values
            ]
        )

    @property
    def entries(self) -> list[AuditLogEntry]:
        return [row for row in self.written if isinstance(row, AuditLogEntry)]


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


@pytest.fixture
def acme() -> Tenant:
    return Tenant("acme")


@pytest.fixture
def globex() -> Tenant:
    return Tenant("globex")


@pytest.fixture
def db(acme: Tenant, globex: Tenant) -> FakeControlPlane:
    return FakeControlPlane([acme, globex])


# ----------------------------------------------------------------- the service


async def test_revoking_a_key_marks_it_revoked(db: FakeControlPlane, acme: Tenant) -> None:
    summary = await service.revoke(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        key_id=acme.key.id,
    )

    assert acme.key.revoked_at is not None
    assert summary.revoked_at == acme.key.revoked_at
    assert summary.id == acme.key.id


async def test_revoking_a_key_records_who_revoked_it(db: FakeControlPlane, acme: Tenant) -> None:
    await service.revoke(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        key_id=acme.key.id,
    )

    (entry,) = db.entries
    assert entry.action == audit.API_KEY_REVOKED
    assert entry.org_id == acme.organization.id
    assert entry.actor_user_id == acme.user.id
    assert entry.target == f"{audit.API_KEY_TARGET}:{acme.key.id}"
    assert acme.key.key_prefix not in entry.target


async def test_an_organization_cannot_revoke_another_organizations_key(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    """A 404, and the other tenant's key is left live and unrecorded."""
    with pytest.raises(NotFoundError):
        await service.revoke(
            db,  # type: ignore[arg-type]
            org_id=acme.organization.id,
            actor=acme.user,
            key_id=globex.key.id,
        )

    assert globex.key.revoked_at is None
    assert db.entries == []


async def test_a_key_that_never_existed_is_the_same_404(db: FakeControlPlane, acme: Tenant) -> None:
    """Indistinguishable from another tenant's key, or the 404 is an oracle."""
    with pytest.raises(NotFoundError) as absent:
        await service.revoke(
            db,  # type: ignore[arg-type]
            org_id=acme.organization.id,
            actor=acme.user,
            key_id=uuid.uuid4(),
        )
    with pytest.raises(NotFoundError) as elsewhere:
        await service.revoke(
            db,  # type: ignore[arg-type]
            org_id=acme.organization.id,
            actor=acme.user,
            key_id=Tenant("initech").key.id,
        )

    assert str(absent.value) == str(elsewhere.value)


async def test_revoking_twice_changes_nothing_and_records_once(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """Revocation is idempotent: a repeat is a no-op, not a second event."""
    first = await service.revoke(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        key_id=acme.key.id,
    )
    second = await service.revoke(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        key_id=acme.key.id,
    )

    assert first.revoked_at == second.revoked_at
    assert len(db.entries) == 1


async def test_a_listing_holds_only_the_organizations_own_keys(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    listing = await service.list_for_project(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        project_id=acme.project.id,
    )

    assert [key.id for key in listing.api_keys] == [acme.key.id]


async def test_a_listing_never_carries_the_secret_or_its_lookup_prefix(
    db: FakeControlPlane, acme: Tenant
) -> None:
    listing = await service.list_for_project(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        project_id=acme.project.id,
    )

    body = listing.model_dump_json()
    assert PLAINTEXT_KEY not in body
    assert acme.key.key_prefix not in body
    assert acme.key.key_hash not in body


async def test_a_listing_shows_only_four_secret_characters(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The hint tells one key from another; it does not narrow the secret."""
    listing = await service.list_for_project(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        project_id=acme.project.id,
    )

    hint = listing.api_keys[0].key_hint
    secret = acme.key.key_prefix.rpartition("_")[2]
    assert hint.startswith("nvk_live_")
    assert hint.endswith(secret[-4:])
    # Every secret character beyond the last four stays undisclosed.
    assert secret[:-4] not in hint


async def test_a_member_may_not_revoke_a_key(db: FakeControlPlane, acme: Tenant) -> None:
    """Revocation is irreversible and stops ingest, so it is not everyone's."""
    acme.user.role = "member"

    with pytest.raises(PermissionDeniedError):
        await service.revoke(
            db,  # type: ignore[arg-type]
            org_id=acme.organization.id,
            actor=acme.user,
            key_id=acme.key.id,
        )

    assert acme.key.revoked_at is None
    assert not [row for row in db.written if isinstance(row, AuditLogEntry)]


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_an_owner_or_admin_may_revoke_a_key(
    db: FakeControlPlane, acme: Tenant, role: str
) -> None:
    acme.user.role = role

    await service.revoke(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        key_id=acme.key.id,
    )

    assert acme.key.revoked_at is not None


# ------------------------------------------------------------------ the routes


def _build_app(db: FakeControlPlane, identity: Identity) -> Any:
    """The app with a signed-in caller. Refusing an anonymous one is held in
    `test_auth_routes.py`, where every signed-in endpoint is enumerated."""
    from nexovue_api.main import create_app

    settings = _settings()
    app = create_app(settings)

    async def db_override() -> AsyncIterator[Any]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[current_identity_dependency] = lambda: identity
    return app


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_the_route_revokes_the_callers_own_key(db: FakeControlPlane, acme: Tenant) -> None:
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(f"{API_PREFIX}/api-keys/{acme.key.id}/revoke")

    assert response.status_code == 200
    assert response.json()["revoked_at"] is not None
    assert acme.key.revoked_at is not None


async def test_the_route_refuses_another_organizations_key(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(f"{API_PREFIX}/api-keys/{globex.key.id}/revoke")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert globex.key.revoked_at is None


async def test_listing_refuses_a_project_in_another_organization(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    """`require_project` is the gate: a project id from the URL proves nothing."""
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.get(
            f"{API_PREFIX}/api-keys", params={"project_id": str(globex.project.id)}
        )

    assert response.status_code == 404
