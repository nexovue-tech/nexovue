"""Minting an ingest key, and withdrawing one.

The store here answers from the values a statement *binds*, never from what the
caller meant. A lookup that stopped filtering `org_id` would no longer bind one,
and would find nothing — so a query that loses its tenancy predicate fails these
tests rather than quietly reaching another organization's key.

Creation is an insert, so the tenant travels as a *value* rather than a filter,
and these tests assert on the row the service actually built. The claims that
only a database can settle — that the minted key authenticates, that both keys
work during a rotation, that the prefix is unique — are in
`test_apikeys_live.py`, against real Postgres.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from pydantic import ValidationError as PydanticValidationError

from nexovue_api.apikeys import service
from nexovue_api.apikeys.schemas import (
    SHOWN_ONCE_NOTICE,
    ApiKeyCreated,
    SiteKeyCreateRequest,
    normalize_origin,
)
from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.auth.dependencies import get_current_identity as current_identity_dependency
from nexovue_api.data.postgres.models import (
    API_KEY_PREFIX_LENGTH,
    SITE_KEY_LENGTH,
    ApiKey,
    AuditLogEntry,
    Organization,
    Project,
    SiteKey,
    User,
)
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.errors.exceptions import (
    InternalError,
    NotFoundError,
)
from nexovue_api.settings import API_PREFIX, Settings

PLAINTEXT_KEY = "nvk_live_secretsecretsecretsec"
PUBLIC_SITE_KEY = "nvp_live_publicpublicpublicpu"


def _plaintext(created: ApiKeyCreated) -> str:
    """The minted secret, asked for by name.

    `ApiKeyCreated.key` is a `SecretStr`, so nothing renders it by accident.
    A test that asserts about the plaintext has to say so, here.
    """
    return created.key.get_secret_value()


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

        # The public kind, in the same tenant, so a test can prove one is never
        # answered where the other was asked for.
        self.site_key = SiteKey(
            org_id=self.organization.id,
            project_id=self.project.id,
            name="marketing site",
            public_key=PUBLIC_SITE_KEY,
            allowed_origins=["https://app.example.com"],
        )
        self.site_key.id = uuid.uuid4()
        self.site_key.created_at = datetime.now(UTC) - timedelta(days=1)
        self.site_key.last_used_at = None
        self.site_key.revoked_at = None

    @property
    def identity(self) -> Identity:
        return Identity(user=self.user, organization=self.organization)


class FakeControlPlane:
    """Keys and projects, answered only from what a statement binds."""

    def __init__(self, tenants: Sequence[Tenant]) -> None:
        self._tenants = list(tenants)
        self.written: list[Any] = []
        # Prefixes this store claims are already taken, so the collision retry
        # can be driven without waiting on a one-in-billions random draw.
        self.claimed_prefixes: set[str] = set()
        # How far into `written` a flush has already reached, so a second flush
        # does not re-stamp a row the first one gave an id to.
        self._flushed_through = 0

    def add(self, entity: Any) -> None:
        self.written.append(entity)

    async def flush(self) -> None:
        """Only what a flush is relied on for: an id and a creation time.

        Nothing about tenancy is simulated here. `uuidv7()` and `now()` are
        server defaults, and the row the service built is what the assertions
        read — so a flush that invented an org_id would be inventing the very
        thing under test.
        """
        for row in self.written[self._flushed_through :]:
            if isinstance(row, ApiKey | SiteKey):
                row.id = uuid.uuid4()
                row.created_at = datetime.now(UTC)
                row.last_used_at = None
                row.revoked_at = None
        self._flushed_through = len(self.written)

    @property
    def minted(self) -> list[ApiKey]:
        return [row for row in self.written if isinstance(row, ApiKey)]

    @property
    def minted_site_keys(self) -> list[SiteKey]:
        return [row for row in self.written if isinstance(row, SiteKey)]

    async def scalar(self, statement: Any) -> Any:
        """`lock_key`, `lock_site_key` and `find_project`: a row only when its
        owner is bound too, and only from the table the statement names.

        The entity check is what makes the two key kinds separate here as well
        as in Postgres. Answering by bound id alone would hand `lock_key` a
        SiteKey — which is the very confusion the two tables exist to prevent,
        so a fake that allowed it would hide the thing under test.

        `key_prefix_is_taken` and `site_key_is_taken` are the unscoped reads,
        answered from the claimed set rather than from a tenant.
        """
        values = _bound_values(statement)
        claimed = values & self.claimed_prefixes
        if claimed:
            return next(iter(claimed))

        entity = statement.column_descriptions[0]["entity"]
        rows = {ApiKey: "key", SiteKey: "site_key", Project: "project"}
        attribute = rows.get(entity)
        if attribute is None:
            return None

        for tenant in self._tenants:
            if tenant.organization.id not in values:
                continue
            row = getattr(tenant, attribute)
            if row.id in values:
                return row
        return None

    async def scalars(self, statement: Any) -> _Rows:
        """`list_keys` and `list_site_keys`: whichever tenant the statement binds.

        The kind comes from the entity the `select` names, not from the bound
        values, because both statements bind the same organization and project.
        A list query that selected the wrong table would answer with the wrong
        kind here, exactly as it would against Postgres.
        """
        values = _bound_values(statement)
        entity = statement.column_descriptions[0]["entity"]
        matched = [
            tenant
            for tenant in self._tenants
            if tenant.organization.id in values and tenant.project.id in values
        ]
        if entity is SiteKey:
            return _Rows([tenant.site_key for tenant in matched])
        return _Rows([tenant.key for tenant in matched])

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


# ------------------------------------------------------------------ minting


async def test_creating_a_key_returns_the_plaintext_once(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The response body is the entire life of the secret outside the customer."""
    created = await service.create(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        project_id=acme.project.id,
        name="staging exporter",
    )

    assert _plaintext(created).startswith("nvk_live_")
    assert created.notice == SHOWN_ONCE_NOTICE
    assert created.api_key.name == "staging exporter"
    assert created.api_key.revoked_at is None
    assert created.api_key.last_used_at is None


async def test_the_minted_row_stores_a_hash_and_a_prefix_but_never_the_key(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The whole reason the plaintext can only be shown once."""
    created = await service.create(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        project_id=acme.project.id,
        name="exporter",
    )

    (row,) = [key for key in db.minted if key.id == created.api_key.id]
    assert row.key_hash == hashlib.sha256(_plaintext(created).encode()).hexdigest()
    assert row.key_prefix == _plaintext(created)[:API_KEY_PREFIX_LENGTH]
    # Every attribute of the stored row, so a new column carrying the secret
    # fails here rather than shipping.
    stored = " ".join(str(getattr(row, column.name)) for column in ApiKey.__table__.columns)
    assert _plaintext(created) not in stored


async def test_a_minted_key_belongs_to_the_session_tenant_not_the_request(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    """`org_id` is a value on an insert, so this is what tenancy means here."""
    created = await service.create(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        project_id=acme.project.id,
        name="exporter",
    )

    (row,) = [key for key in db.minted if key.id == created.api_key.id]
    assert row.org_id == acme.organization.id
    assert row.org_id != globex.organization.id
    assert row.project_id == acme.project.id


async def test_creating_a_key_records_who_minted_it_without_the_credential(
    db: FakeControlPlane, acme: Tenant
) -> None:
    created = await service.create(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        project_id=acme.project.id,
        name="exporter",
    )

    (entry,) = db.entries
    assert entry.action == audit.API_KEY_CREATED
    assert entry.org_id == acme.organization.id
    assert entry.actor_user_id == acme.user.id
    assert entry.target == f"{audit.API_KEY_TARGET}:{created.api_key.id}"
    assert _plaintext(created) not in entry.target
    assert created.api_key.key_hint not in str(entry.target)


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_an_owner_or_admin_may_create_a_key(
    db: FakeControlPlane, acme: Tenant, role: str
) -> None:
    acme.user.role = role

    created = await service.create(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        project_id=acme.project.id,
        name="exporter",
    )

    assert _plaintext(created)


async def test_a_claimed_prefix_is_retried_rather_than_surfaced(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """`uq_api_keys_key_prefix` is global, so somebody else's key can collide."""
    taken = await service.allocate_unused_key(db)  # type: ignore[arg-type]
    db.claimed_prefixes.add(taken[:API_KEY_PREFIX_LENGTH])

    replacement = await service.allocate_unused_key(db)  # type: ignore[arg-type]

    assert replacement[:API_KEY_PREFIX_LENGTH] not in db.claimed_prefixes


async def test_an_exhausted_keyspace_fails_loudly_instead_of_spinning(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """A store that claims everything must end the loop, not run it forever."""

    class ClaimsEverything(FakeControlPlane):
        async def scalar(self, statement: Any) -> Any:
            return uuid.uuid4()

    with pytest.raises(InternalError):
        await service.allocate_unused_key(ClaimsEverything([acme]))  # type: ignore[arg-type]


async def test_minting_leaves_existing_keys_live(db: FakeControlPlane, acme: Tenant) -> None:
    """Rotation depends on this: deploy the new key before withdrawing the old."""
    await service.create(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        project_id=acme.project.id,
        name="replacement",
    )

    assert acme.key.revoked_at is None


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


async def test_the_route_mints_a_key_for_the_callers_own_project(
    db: FakeControlPlane, acme: Tenant
) -> None:
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/api-keys",
            json={"project_id": str(acme.project.id), "name": "staging exporter"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["key"].startswith("nvk_live_")
    assert body["notice"] == SHOWN_ONCE_NOTICE
    assert body["api_key"]["name"] == "staging exporter"


async def test_the_route_refuses_a_project_in_another_organization(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    """A project id in the body is a value the caller chose, like one in a query."""
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/api-keys",
            json={"project_id": str(globex.project.id), "name": "stolen"},
        )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert db.minted == []


async def test_the_route_refuses_a_key_with_no_name(db: FakeControlPlane, acme: Tenant) -> None:
    """A blank name is five keys nobody can tell apart when one of them leaks."""
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/api-keys", json={"project_id": str(acme.project.id), "name": ""}
        )

    assert response.status_code == 422
    assert db.minted == []


async def test_a_listing_after_minting_still_carries_no_secret(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The one place the plaintext appears is the create response, and only there."""
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        created = await client.post(
            f"{API_PREFIX}/api-keys",
            json={"project_id": str(acme.project.id), "name": "exporter"},
        )
        listing = await client.get(
            f"{API_PREFIX}/api-keys", params={"project_id": str(acme.project.id)}
        )

    assert created.json()["key"] not in listing.text


# --------------------------------------------------- the public kind, and the
# --------------------------------------------------- boundary between the two


async def test_a_site_key_is_stored_in_the_clear_and_never_hashed(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The decision this whole change turns on.

    A site key is printed in the customer's page, so hashing it would protect a
    published value while costing the exact-match lookup. If a `key_hash` ever
    appears on this row, the browser door's single-probe lookup has quietly
    become a prefix scan and this test is the thing that says so.
    """
    created = await service.create_site_key(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        project_id=acme.project.id,
        name="marketing",
        allowed_origins=["https://app.example.com"],
    )

    (row,) = [key for key in db.minted_site_keys if key.id == created.id]
    assert row.public_key == created.public_key
    assert not hasattr(row, "key_hash")
    # Stored whole: nothing is split off, because nothing is withheld.
    assert row.public_key.startswith("nvp_live_")
    assert len(row.public_key) == SITE_KEY_LENGTH


async def test_a_site_key_is_returned_in_full_every_time_it_is_listed(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The mirror image of "shown once": there is nothing to withhold, and a
    customer redeploying their site has to be able to read the key back."""
    listed = await service.list_site_keys_for_project(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        project_id=acme.project.id,
    )

    (summary,) = listed.site_keys
    assert summary.public_key == PUBLIC_SITE_KEY
    assert "…" not in summary.public_key


async def test_every_summary_declares_its_kind(db: FakeControlPlane, acme: Tenant) -> None:
    """The kind travels on the wire, so the console never infers it from a route."""
    secret = await service.list_for_project(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        project_id=acme.project.id,
    )
    public = await service.list_site_keys_for_project(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        project_id=acme.project.id,
    )

    assert [key.kind for key in secret.api_keys] == ["secret"]
    assert [key.kind for key in public.site_keys] == ["public"]


async def test_listing_secret_keys_never_answers_with_a_public_one(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The blast radius this design exists to remove.

    Both tenants hold one key of each kind, and both list statements bind the
    same organization and project. Only the table named in the `select` tells
    them apart — which is the guarantee a `kind` column could not have given.
    """
    secret = await service.list_for_project(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        project_id=acme.project.id,
    )

    assert all(key.kind == "secret" for key in secret.api_keys)
    assert all(key.key_hint.startswith("nvk_live_") for key in secret.api_keys)
    assert PUBLIC_SITE_KEY not in [key.key_hint for key in secret.api_keys]


async def test_a_site_key_belongs_to_its_own_tenant_only(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    """Another organization's site key is a 404, not a row."""
    with pytest.raises(NotFoundError):
        await service.revoke_site_key(
            db,  # type: ignore[arg-type]
            org_id=acme.organization.id,
            actor=acme.user,
            key_id=globex.site_key.id,
        )


async def test_minting_a_site_key_is_audited_under_its_own_action(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """`site_key.created`, not `api_key.created`: reading the audit log must not
    make a browser key look like a grant of server ingest."""
    created = await service.create_site_key(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        project_id=acme.project.id,
        name="marketing",
        allowed_origins=["https://app.example.com"],
    )

    (entry,) = db.entries
    assert entry.action == audit.SITE_KEY_CREATED
    assert entry.target == f"site_key:{created.id}"
    assert entry.org_id == acme.organization.id


async def test_revoking_a_site_key_is_one_way_and_idempotent(
    db: FakeControlPlane, acme: Tenant
) -> None:
    first = await service.revoke_site_key(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        key_id=acme.site_key.id,
    )
    second = await service.revoke_site_key(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        key_id=acme.site_key.id,
    )

    assert first.revoked_at is not None
    assert second.revoked_at == first.revoked_at
    # One record, because the second call withdrew nothing.
    assert [entry.action for entry in db.entries] == [audit.SITE_KEY_REVOKED]


async def test_a_colliding_public_key_is_retried_rather_than_inserted(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """`uq_site_keys_public_key` is global, so a fresh key can genuinely collide."""
    db.claimed_prefixes = {PUBLIC_SITE_KEY}

    created = await service.create_site_key(
        db,  # type: ignore[arg-type]
        org_id=acme.organization.id,
        actor=acme.user,
        project_id=acme.project.id,
        name="marketing",
        allowed_origins=["https://app.example.com"],
    )

    assert created.public_key != PUBLIC_SITE_KEY


async def test_an_exhausted_keyspace_fails_loudly_rather_than_spinning(
    db: FakeControlPlane, acme: Tenant
) -> None:
    class AlwaysTaken(FakeControlPlane):
        async def scalar(self, statement: Any) -> Any:
            values = _bound_values(statement)
            if any(isinstance(value, str) and value.startswith("nvp_live_") for value in values):
                return uuid.uuid4()
            return await super().scalar(statement)

    with pytest.raises(InternalError):
        await service.create_site_key(
            AlwaysTaken([acme]),  # type: ignore[arg-type]
            org_id=acme.organization.id,
            actor=acme.user,
            project_id=acme.project.id,
            name="marketing",
            allowed_origins=["https://app.example.com"],
        )


# ------------------------------------------------------------ allowed origins


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        ("https://app.example.com", "https://app.example.com"),
        ("HTTPS://App.Example.COM", "https://app.example.com"),
        # A default port is not part of the origin a browser sends.
        ("https://example.com:443", "https://example.com"),
        ("http://example.com:80", "http://example.com"),
        ("https://example.com/", "https://example.com"),
        ("  https://example.com  ", "https://example.com"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("https://*.example.com", "https://*.example.com"),
    ],
)
def test_an_origin_is_normalized_to_what_a_browser_will_send(written: str, stored: str) -> None:
    """Stored unnormalized, an origin the customer configured would never match."""
    assert normalize_origin(written) == stored


@pytest.mark.parametrize(
    "written",
    [
        "example.com",  # no scheme
        "ftp://example.com",  # not a browser origin
        "https://example.com/path",  # a URL, not an origin
        "https://example.com?q=1",
        "https://example.com#fragment",
        "https://user:pass@example.com",
        "https://*.com",  # one label: an allowlist that allows the internet
        "https://*",
        "null",
        "",
    ],
)
def test_an_origin_that_could_never_match_is_refused_at_the_door(written: str) -> None:
    with pytest.raises(ValueError, match="origin"):
        normalize_origin(written)


def test_a_site_key_cannot_be_created_without_an_origin() -> None:
    """An empty allowlist accepts nothing, so it is refused rather than stored."""
    with pytest.raises(PydanticValidationError):
        SiteKeyCreateRequest(project_id=uuid.uuid4(), name="marketing", allowed_origins=[])


def test_duplicate_origins_collapse_and_order_is_kept() -> None:
    request = SiteKeyCreateRequest(
        project_id=uuid.uuid4(),
        name="marketing",
        allowed_origins=["https://b.example.com", "HTTPS://A.example.com", "https://b.example.com"],
    )

    assert request.allowed_origins == ["https://b.example.com", "https://a.example.com"]


# ------------------------------------------------------- the site key routes


async def test_the_site_key_route_mints_a_public_key_and_returns_it_whole(
    db: FakeControlPlane, acme: Tenant
) -> None:
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/site-keys",
            json={
                "project_id": str(acme.project.id),
                "name": "marketing",
                "allowed_origins": ["https://app.example.com"],
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "public"
    assert body["public_key"].startswith("nvp_live_")
    assert body["allowed_origins"] == ["https://app.example.com"]
    # No shown-once notice and no separate `key` field: the value above is the
    # key, and it is on every subsequent read as well.
    assert "notice" not in body
    assert "key" not in body


async def test_the_site_key_route_refuses_a_project_in_another_organization(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/site-keys",
            json={
                "project_id": str(globex.project.id),
                "name": "marketing",
                "allowed_origins": ["https://app.example.com"],
            },
        )

    assert response.status_code == 404
    assert db.minted_site_keys == []


async def test_the_site_key_route_refuses_an_origin_that_could_never_match(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """Refused at the door rather than stored and silently never matched."""
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/site-keys",
            json={
                "project_id": str(acme.project.id),
                "name": "marketing",
                "allowed_origins": ["example.com"],
            },
        )

    assert response.status_code == 422
    assert db.minted_site_keys == []


async def test_the_two_key_endpoints_answer_only_their_own_kind(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The end-to-end statement of the boundary.

    One project, one key of each kind, one signed-in caller. Each list endpoint
    returns exactly its own kind — there is no request either of them can serve
    with the other's rows, because they read different tables.
    """
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        secret = await client.get(
            f"{API_PREFIX}/api-keys", params={"project_id": str(acme.project.id)}
        )
        public = await client.get(
            f"{API_PREFIX}/site-keys", params={"project_id": str(acme.project.id)}
        )

    assert secret.status_code == 200
    assert public.status_code == 200

    secret_body = secret.json()["api_keys"]
    public_body = public.json()["site_keys"]

    assert [key["kind"] for key in secret_body] == ["secret"]
    assert [key["kind"] for key in public_body] == ["public"]

    # The secret list carries no readable key of either kind.
    assert all("public_key" not in key for key in secret_body)
    assert PUBLIC_SITE_KEY not in secret.text
    # And the public list carries no hint, hash or prefix of a secret key.
    assert all("key_hint" not in key for key in public_body)
    assert PLAINTEXT_KEY[:API_KEY_PREFIX_LENGTH] not in public.text


async def test_the_site_key_route_retires_the_callers_own_key(
    db: FakeControlPlane, acme: Tenant
) -> None:
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(f"{API_PREFIX}/site-keys/{acme.site_key.id}/revoke")

    assert response.status_code == 200
    assert response.json()["revoked_at"] is not None
    assert acme.site_key.revoked_at is not None


async def test_a_site_key_id_is_not_revocable_through_the_secret_route(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """A crossed id is a 404, because the secret route locks a different table."""
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(f"{API_PREFIX}/api-keys/{acme.site_key.id}/revoke")

    assert response.status_code == 404
    assert acme.site_key.revoked_at is None
