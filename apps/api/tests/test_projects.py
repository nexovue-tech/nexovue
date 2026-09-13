"""Creating, renaming and closing a project, and the two configuration documents.

The store here answers from the values a statement *binds*, never from what the
caller meant. A lookup that stopped filtering `org_id` would no longer bind one
and would find nothing, so a query that loses its tenancy predicate fails these
tests rather than quietly reaching another organization's project.

**The grammar tests are the point of this file.** `config.py` and the Go worker
parse the same two documents, and the API's only reason to validate is that a
document the worker rejects must never be stored — `refresh.go` falls back to
defaults that name no custom keys on a cold start, so a bad document is a project
that silently stops masking at the next restart. The cases below are taken from
`mask/rules.go` and `sample/policy.go` line by line;
`test_cross_service_contracts.py` is what keeps the two name lists equal.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from environment import TEST_ENVIRONMENT

from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.auth.dependencies import get_current_identity as current_identity_dependency
from nexovue_api.data.postgres.models import (
    ApiKey,
    AuditLogEntry,
    Check,
    Integration,
    Organization,
    Project,
    ProjectWebhookSecret,
    SiteKey,
    User,
)
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.errors.exceptions import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
)
from nexovue_api.projects import config, service
from nexovue_api.projects.schemas import (
    DELETION_TELEMETRY_NOTICE,
    MASKING_NOTICE,
    ProjectCreateRequest,
    ProjectUpdateRequest,
)
from nexovue_api.settings import API_PREFIX, Settings


def _settings() -> Settings:
    return Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]


def _bound_values(statement: Any) -> set[Any]:
    """Every literal a SQLAlchemy statement carries into the driver."""
    return set(statement.compile().params.values())


class Tenant:
    """An organization with two projects, and one door of each kind on the first."""

    def __init__(self, slug: str) -> None:
        self.organization = Organization(slug=slug, name=slug.title())
        self.organization.id = uuid.uuid4()

        self.user = User(
            org_id=self.organization.id, email=f"owner@{slug}.test", role="owner", name=None
        )
        self.user.id = uuid.uuid4()

        self.project = self._project("production", "Production")
        self.second_project = self._project("staging", "Staging")
        self.projects = [self.project, self.second_project]

        self.api_keys = [
            ApiKey(
                org_id=self.organization.id,
                project_id=self.project.id,
                name="default",
                key_prefix=f"nvk_live_{slug}",
                key_hash="0" * 64,
            )
        ]
        self.site_keys = [
            SiteKey(
                org_id=self.organization.id,
                project_id=self.project.id,
                name="marketing site",
                public_key=f"nvp_live_{slug}",
                allowed_origins=[],
            )
        ]
        self.webhook_secrets = [
            ProjectWebhookSecret(
                org_id=self.organization.id, project_id=self.project.id, secret="s" * 32
            )
        ]
        for door in self.api_keys + self.site_keys + self.webhook_secrets:
            door.id = uuid.uuid4()
            door.revoked_at = None

        # Empty by default: the ordinary project has no outbound work scheduled,
        # and a test that wants the refusal adds one.
        self.checks: list[Check] = []
        self.integrations: list[Integration] = []

        # The days the ingest meter recorded for each project — `usage_daily`
        # reduced to the one column the project list reads. Empty by default,
        # which is a project nothing has ever reported for.
        self.metered_days: dict[uuid.UUID, list[date]] = {}

    def record_arrival(self, project: Project, day: date) -> None:
        """One day the meter counted something for this project."""
        self.metered_days.setdefault(project.id, []).append(day)

    def _project(self, slug: str, name: str) -> Project:
        project = Project(org_id=self.organization.id, slug=slug, name=name)
        project.id = uuid.uuid4()
        project.created_at = datetime.now(UTC) - timedelta(days=1)
        project.deleted_at = None
        project.retention_days = 30
        project.sampling_config = {}
        project.masking_config = {}
        return project

    def add_check(self) -> Check:
        check = Check(
            org_id=self.organization.id,
            project_id=self.project.id,
            type="http",
            target="https://example.test",
            interval_seconds=60,
            enabled=True,
        )
        check.id = uuid.uuid4()
        self.checks.append(check)
        return check

    def add_integration(self) -> Integration:
        integration = Integration(
            org_id=self.organization.id,
            project_id=self.project.id,
            kind="cloudwatch",
            status="active",
        )
        integration.id = uuid.uuid4()
        self.integrations.append(integration)
        return integration

    @property
    def live_projects(self) -> list[Project]:
        return [project for project in self.projects if project.deleted_at is None]

    @property
    def identity(self) -> Identity:
        return Identity(user=self.user, organization=self.organization)


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Changed:
    """What `AsyncSession.execute` returns for a DML statement: a row count."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class FakeControlPlane:
    """Projects and their doors, answered only from what a statement binds.

    Reads are dispatched on what the `select` names — the table it comes from and
    the column it returns — rather than on the caller, so a query that asked the
    wrong table answers wrongly here exactly as it would against Postgres.
    """

    def __init__(self, tenants: Sequence[Tenant]) -> None:
        self._tenants = list(tenants)
        self.written: list[Any] = []
        self._flushed_through = 0

    # -- what the session offers -------------------------------------------

    def add(self, entity: Any) -> None:
        self.written.append(entity)

    async def flush(self) -> None:
        """Only what a flush is relied on for: the server-generated id and time.

        Nothing else is stamped, and that is deliberate. A flush does not read a
        column's server default back into the row, so a fake that filled in
        `retention_days` or the two documents would hide the very thing that
        made `create` answer with nulls the first time it ran.
        """
        for row in self.written[self._flushed_through :]:
            if isinstance(row, Project):
                row.id = uuid.uuid4()
                row.created_at = datetime.now(UTC)
                row.deleted_at = None
                self._owner_of(row.org_id).projects.append(row)
        self._flushed_through = len(self.written)

    async def scalar(self, statement: Any) -> Any:
        values = _bound_values(statement)
        selected = statement.column_descriptions[0]["name"]

        if selected == "_no_label":  # select(exists(...)) — slug_is_taken
            return any(
                project.slug in values
                for tenant in self._tenants_binding(values)
                for project in tenant.projects
            )
        if selected == "count":
            return len(self._counted(statement, values))
        if selected == "Project":
            for tenant in self._tenants_binding(values):
                for project in tenant.projects:
                    if project.id in values and project.deleted_at is None:
                        return project
            return None
        raise AssertionError(f"the fake store has no answer for {selected!r}")

    async def scalars(self, statement: Any) -> _Rows:
        values = _bound_values(statement)
        selected = statement.column_descriptions[0]["name"]
        matched = [
            project for tenant in self._tenants_binding(values) for project in tenant.projects
        ]
        if selected == "slug":  # slugs_beginning_with
            prefix = next(value for value in values if isinstance(value, str)).rstrip("%")
            return _Rows([project.slug for project in matched if project.slug.startswith(prefix)])
        return _Rows([project for project in matched if project.deleted_at is None])

    async def execute(self, statement: Any) -> Any:
        """The three revocations, and the ingest meter the project list reads."""
        if statement.is_select:
            return _Rows(self._metered_days(_bound_values(statement)))

        values = _bound_values(statement)
        doors = {
            "ApiKey": "api_keys",
            "SiteKey": "site_keys",
            "ProjectWebhookSecret": "webhook_secrets",
        }
        attribute = doors[statement.entity_description["name"]]
        revoked_at = statement.compile().params["revoked_at"]

        changed = 0
        for tenant in self._tenants_binding(values):
            for door in getattr(tenant, attribute):
                if door.project_id in values and door.revoked_at is None:
                    door.revoked_at = revoked_at
                    changed += 1
        return _Changed(changed)

    # -- what the tests read ------------------------------------------------

    @property
    def entries(self) -> list[AuditLogEntry]:
        return [row for row in self.written if isinstance(row, AuditLogEntry)]

    @property
    def created_projects(self) -> list[Project]:
        return [row for row in self.written if isinstance(row, Project)]

    # -- internals ----------------------------------------------------------

    def _metered_days(self, values: set[Any]) -> list[tuple[uuid.UUID, date]]:
        """`first_reported_days`: the earliest metered day of each project.

        Answered from the tenant the statement bound, like everything else here,
        so a read that dropped its `org_id` predicate reports that nothing has
        ever arrived for anyone.
        """
        return [
            (project_id, min(days))
            for tenant in self._tenants_binding(values)
            for project_id, days in tenant.metered_days.items()
            if days
        ]

    def _tenants_binding(self, values: set[Any]) -> list[Tenant]:
        """Only tenants the statement actually named. This is what makes a query
        that dropped its `org_id` predicate find nothing."""
        return [tenant for tenant in self._tenants if tenant.organization.id in values]

    def _counted(self, statement: Any, values: set[Any]) -> list[Any]:
        table = statement.get_final_froms()[0].name
        rows: list[Any] = []
        for tenant in self._tenants_binding(values):
            if table == "projects":
                rows += tenant.live_projects
            elif table == "checks":
                rows += [
                    check for check in tenant.checks if check.project_id in values and check.enabled
                ]
            elif table == "integrations":
                rows += [
                    integration
                    for integration in tenant.integrations
                    if integration.project_id in values
                ]
            else:
                raise AssertionError(f"the fake store counts no table called {table!r}")
        return rows

    def _owner_of(self, org_id: uuid.UUID) -> Tenant:
        return next(tenant for tenant in self._tenants if tenant.organization.id == org_id)


@pytest.fixture
def acme() -> Tenant:
    return Tenant("acme")


@pytest.fixture
def globex() -> Tenant:
    return Tenant("globex")


@pytest.fixture
def db(acme: Tenant, globex: Tenant) -> FakeControlPlane:
    return FakeControlPlane([acme, globex])


def _store(db: FakeControlPlane) -> Any:
    """The fake, cast at one place rather than at every call site."""
    return db


# ---------------------------------------------------------------- creating


async def test_creating_a_project_derives_a_slug_from_its_name(
    db: FakeControlPlane, acme: Tenant
) -> None:
    created = await service.create(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        request=ProjectCreateRequest(name="EU Production"),
    )

    assert created.slug == "eu-production"
    assert created.name == "EU Production"
    # Empty documents mean the shipped defaults, which is the state a new
    # project should be in.
    assert created.sampling_config == {}
    assert created.masking_config == {}


async def test_a_derived_slug_that_collides_is_suffixed(db: FakeControlPlane, acme: Tenant) -> None:
    created = await service.create(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        request=ProjectCreateRequest(name="Production"),
    )

    assert created.slug == "production-2"


async def test_a_slug_the_customer_chose_is_refused_rather_than_suffixed(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """Silently suffixing what somebody typed gives them a project they did not name."""
    with pytest.raises(ConflictError):
        await service.create(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            request=ProjectCreateRequest(name="Production", slug="production"),
        )


async def test_a_created_project_belongs_to_the_session_tenant(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    """`org_id` is a value on an insert, so this is what tenancy means here."""
    await service.create(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        request=ProjectCreateRequest(name="Staging Two"),
    )

    (row,) = db.created_projects
    assert row.org_id == acme.organization.id
    assert row.org_id != globex.organization.id


async def test_another_organizations_slug_does_not_collide(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """`(org_id, slug)` is unique per organization, so every tenant may have a
    `production`. A uniqueness check that lost its tenant would refuse this."""
    created = await service.create(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        request=ProjectCreateRequest(name="Prod", slug="prod"),
    )
    assert created.slug == "prod"


async def test_creating_a_project_records_who_created_it(
    db: FakeControlPlane, acme: Tenant
) -> None:
    created = await service.create(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        request=ProjectCreateRequest(name="EU"),
    )

    (entry,) = db.entries
    assert entry.action == audit.PROJECT_CREATED
    assert entry.org_id == acme.organization.id
    assert entry.actor_user_id == acme.user.id
    assert entry.target == f"{audit.PROJECT_TARGET}:{created.id}"


# ---------------------------------------------------------------- renaming


async def test_renaming_a_project_changes_the_name_and_keeps_the_slug(
    db: FakeControlPlane, acme: Tenant
) -> None:
    updated = await service.update(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        project_id=acme.project.id,
        request=ProjectUpdateRequest(name="Live"),
    )

    assert updated.name == "Live"
    assert updated.slug == "production"


async def test_an_empty_patch_is_refused(db: FakeControlPlane, acme: Tenant) -> None:
    """A body naming nothing is a request nobody meant to send."""
    with pytest.raises(InvalidRequestError):
        await service.update(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            project_id=acme.project.id,
            request=ProjectUpdateRequest(),
        )


async def test_renaming_another_organizations_project_is_a_404(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    with pytest.raises(NotFoundError):
        await service.update(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            project_id=globex.project.id,
            request=ProjectUpdateRequest(name="Theirs"),
        )


async def test_taking_a_slug_another_project_holds_is_refused(
    db: FakeControlPlane, acme: Tenant
) -> None:
    with pytest.raises(ConflictError):
        await service.update(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            project_id=acme.project.id,
            request=ProjectUpdateRequest(slug="staging"),
        )


# ---------------------------------------------------------------- deleting


async def test_deleting_a_project_closes_every_door_it_holds(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The mark alone is not a deletion.

    The receiver resolves an ingest key on `revoked_at IS NULL` and never joins
    `projects`; the deploy webhook resolves its secret the same way. A project
    that was only marked would go on accepting telemetry under a name that has
    disappeared from every screen.
    """
    closed = await service.delete(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        project_id=acme.project.id,
    )

    assert closed.api_keys_revoked == 1
    assert closed.site_keys_revoked == 1
    assert closed.webhook_secrets_revoked == 1
    assert acme.project.deleted_at == closed.deleted_at
    assert all(key.revoked_at is not None for key in acme.api_keys)
    assert all(key.revoked_at is not None for key in acme.site_keys)
    assert all(secret.revoked_at is not None for secret in acme.webhook_secrets)


async def test_deleting_a_project_leaves_another_projects_keys_alone(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The revocations bind a project as well as a tenant; without the project
    predicate this would take the whole organization's ingest down."""
    other_key = ApiKey(
        org_id=acme.organization.id,
        project_id=acme.second_project.id,
        name="staging",
        key_prefix="nvk_live_other",
        key_hash="1" * 64,
    )
    other_key.id, other_key.revoked_at = uuid.uuid4(), None
    acme.api_keys.append(other_key)

    await service.delete(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        project_id=acme.project.id,
    )

    assert other_key.revoked_at is None


async def test_deleting_says_plainly_that_the_telemetry_is_still_there(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """Nothing drops partitions, so "deleted" must not imply the data is gone."""
    closed = await service.delete(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        project_id=acme.project.id,
    )

    assert closed.telemetry_notice == DELETION_TELEMETRY_NOTICE
    assert "not deleted" in closed.telemetry_notice


async def test_a_deleted_project_is_no_longer_readable(db: FakeControlPlane, acme: Tenant) -> None:
    """What makes the stored telemetry unreachable: every read is gated here."""
    await service.delete(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        project_id=acme.project.id,
    )

    with pytest.raises(NotFoundError):
        await service.require_project(
            _store(db), org_id=acme.organization.id, project_id=acme.project.id
        )


async def test_the_only_project_cannot_be_deleted(db: FakeControlPlane, acme: Tenant) -> None:
    """An organization always has one. Closing the account is a different endpoint."""
    acme.second_project.deleted_at = datetime.now(UTC)

    with pytest.raises(ConflictError, match="only project"):
        await service.delete(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            project_id=acme.project.id,
        )


async def test_a_project_still_running_checks_cannot_be_deleted(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The scheduler selects on `enabled` alone and never looks at
    `projects.deleted_at`, so a deleted project with an enabled check would go on
    fetching somebody's URL from our infrastructure for ever."""
    acme.add_check()

    with pytest.raises(ConflictError, match="enabled check"):
        await service.delete(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            project_id=acme.project.id,
        )


async def test_a_project_with_an_integration_cannot_be_deleted(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """Same reason: the poller selects on `status` and would go on assuming a role
    in the customer's cloud account."""
    acme.add_integration()

    with pytest.raises(ConflictError, match="integration"):
        await service.delete(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            project_id=acme.project.id,
        )


async def test_deleting_another_organizations_project_is_a_404(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    with pytest.raises(NotFoundError):
        await service.delete(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            project_id=globex.project.id,
        )
    assert globex.project.deleted_at is None
    assert all(key.revoked_at is None for key in globex.api_keys)


async def test_deleting_records_who_closed_it(db: FakeControlPlane, acme: Tenant) -> None:
    await service.delete(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        project_id=acme.project.id,
    )

    (entry,) = db.entries
    assert entry.action == audit.PROJECT_DELETED
    assert entry.target == f"{audit.PROJECT_TARGET}:{acme.project.id}"


# ------------------------------------------- has this project ever reported


async def _listed(db: FakeControlPlane, tenant: Tenant) -> dict[uuid.UUID, date | None]:
    listing = await service.list_for_organization(_store(db), tenant.organization.id)
    return {project.id: project.first_reported_on for project in listing.projects}


async def test_a_project_reports_the_first_day_anything_arrived_for_it(
    db: FakeControlPlane, acme: Tenant
) -> None:
    acme.record_arrival(acme.project, date(2026, 3, 2))
    acme.record_arrival(acme.project, date(2026, 3, 9))

    assert (await _listed(db, acme))[acme.project.id] == date(2026, 3, 2)


async def test_a_long_silence_does_not_erase_that_a_project_once_reported(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The whole reason this field exists.

    The screens used to read "nothing in the widest range" as "nothing ever",
    which tells a customer whose services went quiet a fortnight ago to go and
    instrument them again. The meter's rows outlive every window, so a project
    that reported once answers with a date for ever.
    """
    acme.record_arrival(acme.project, date(2025, 1, 1))

    assert (await _listed(db, acme))[acme.project.id] == date(2025, 1, 1)


async def test_a_project_nothing_ever_arrived_for_reports_no_day(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """Null, not a date in the past — the one state that may offer setup."""
    acme.record_arrival(acme.project, date(2026, 3, 2))

    assert (await _listed(db, acme))[acme.second_project.id] is None


async def test_another_organizations_arrivals_are_never_read_as_this_ones(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    globex.record_arrival(globex.project, date(2026, 3, 2))

    assert all(day is None for day in (await _listed(db, acme)).values())
    assert (await _listed(db, globex))[globex.project.id] == date(2026, 3, 2)


# ------------------------------------------------- the masking grammar


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"disable": ["cookies"]},
        {"hash": ["credential_keys"]},
        # `rules.go` trims before matching, so this is a valid line there.
        {"disable": [" cookies "]},
        {"mask_keys": ["x-internal-trace"], "allow_values": ["ok"]},
        {"mask_key_contains": ["session"], "hash_key_contains": ["user"]},
        {"hash_keys": ["account_id"]},
        # Absent and null are the same thing to `encoding/json`.
        {"disable": None},
        {name: [] for name in config.MASKING_DOCUMENT_KEYS},
    ],
)
def test_a_document_the_worker_accepts_is_stored_as_it_was_written(
    document: dict[str, Any],
) -> None:
    """Returned unchanged: what is stored is what the worker reads, so
    normalising here would store something nobody typed."""
    assert config.validate_masking_document(document) == document


@pytest.mark.parametrize(
    "document",
    [
        # `setFamily` already makes this an error; the API says so first.
        {"disable": ["credentials"]},
        {"hash": ["card_number"]},
        {"disable": ["CREDENTIAL_KEYS"]},
        # `json.Unmarshal` into `[]string` refuses both of these.
        {"disable": "credential_keys"},
        {"mask_keys": [1]},
        {"allow_values": {"a": "b"}},
        # A struct is not an array.
        [],
        "credential_keys",
        # Ignored by Go and refused here: a typo for `mask_keys` masks nothing,
        # and the operator who typed it believes it does.
        {"mask_key": ["password"]},
        {"disable": ["cookies"], "enabled": True},
    ],
)
def test_a_document_the_worker_would_reject_is_refused(document: Any) -> None:
    with pytest.raises(InvalidRequestError):
        config.validate_masking_document(document)


def test_the_refusal_names_the_rule_that_is_wrong() -> None:
    """A 422 that does not say which line is wrong is a log entry with a status code."""
    with pytest.raises(InvalidRequestError) as refusal:
        config.validate_masking_document({"disable": ["credentials"]})

    assert "credentials" in refusal.value.detail
    assert "credential_keys" in refusal.value.detail


# ------------------------------------------------- the sampling grammar


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"keep_errors": False},
        {"slow_threshold_ms": 0},
        {"healthy_percent": 0},
        {"healthy_percent": 100},
        {"keep_errors": True, "slow_threshold_ms": 2000, "healthy_percent": 25},
        {"healthy_percent": None},
    ],
)
def test_a_sampling_document_the_worker_accepts_is_stored(document: dict[str, Any]) -> None:
    assert config.validate_sampling_document(document) == document


@pytest.mark.parametrize(
    "document",
    [
        # `PolicyFromJSON` refuses these rather than clamping, because a project
        # believing it stores everything while it stores a tenth is worse.
        {"healthy_percent": 101},
        {"healthy_percent": -1},
        {"slow_threshold_ms": -1},
        # `*int` and `*bool` in Go: none of these unmarshal.
        {"healthy_percent": 10.0},
        {"healthy_percent": 10.5},
        {"keep_errors": 1},
        {"keep_errors": "true"},
        {"slow_threshold_ms": "2000"},
        [],
        {"percent": 10},
    ],
)
def test_a_sampling_document_the_worker_would_reject_is_refused(document: Any) -> None:
    with pytest.raises(InvalidRequestError):
        config.validate_sampling_document(document)


def test_the_sampling_refusal_repeats_the_workers_own_wording() -> None:
    with pytest.raises(InvalidRequestError) as refusal:
        config.validate_sampling_document({"healthy_percent": 120})

    assert "healthy_percent is 120, want 0 to 100" in refusal.value.detail


# ------------------------------------------------- writing the documents


async def test_a_valid_sampling_document_round_trips(db: FakeControlPlane, acme: Tenant) -> None:
    document = {"keep_errors": True, "healthy_percent": 50}

    written = await service.write_sampling_config(
        _store(db),
        org_id=acme.organization.id,
        project_id=acme.project.id,
        document=document,
    )

    assert written.config == document
    assert acme.project.sampling_config == document


async def test_an_invalid_sampling_document_never_reaches_the_row(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The whole reason the API validates: a stored document the worker cannot
    parse is a project running on defaults after the next restart."""
    with pytest.raises(InvalidRequestError):
        await service.write_sampling_config(
            _store(db),
            org_id=acme.organization.id,
            project_id=acme.project.id,
            document={"healthy_percent": 200},
        )

    assert acme.project.sampling_config == {}


async def test_sampling_takes_no_confirmation_and_leaves_no_audit_row(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """A dialogue on a reversible setting teaches people to click
    through the one that matters."""
    await service.write_sampling_config(
        _store(db),
        org_id=acme.organization.id,
        project_id=acme.project.id,
        document={"healthy_percent": 1},
    )

    assert db.entries == []


async def test_adding_masking_rules_needs_no_confirmation(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """Adding keys narrows what leaves the customer's process; only widening is guarded."""
    document = {"mask_keys": ["x-session"], "hash_keys": ["account_id"]}

    written = await service.write_masking_config(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        project_id=acme.project.id,
        document=document,
        confirm_disabling=[],
    )

    assert written.config == document
    assert written.notice == MASKING_NOTICE
    assert db.entries == []


async def test_switching_a_shipped_rule_off_needs_the_rule_named(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The one guarded action, and the refusal names what must be acknowledged."""
    with pytest.raises(ConflictError) as refusal:
        await service.write_masking_config(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            project_id=acme.project.id,
            document={"disable": ["credential_keys"]},
            confirm_disabling=[],
        )

    assert refusal.value.extensions["requires_confirmation"] == ["credential_keys"]
    assert acme.project.masking_config == {}


async def test_a_confirmed_off_switch_is_applied_and_audited(
    db: FakeControlPlane, acme: Tenant
) -> None:
    written = await service.write_masking_config(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        project_id=acme.project.id,
        document={"disable": ["credential_keys", "cookies"]},
        confirm_disabling=["credential_keys", "cookies"],
    )

    assert written.config == {"disable": ["credential_keys", "cookies"]}
    assert {entry.action for entry in db.entries} == {audit.PROJECT_MASKING_RULE_DISABLED}
    assert {entry.target for entry in db.entries} == {
        audit.masking_rule_target(acme.project.id, "cookies"),
        audit.masking_rule_target(acme.project.id, "credential_keys"),
    }
    assert all(entry.actor_user_id == acme.user.id for entry in db.entries)


async def test_a_rule_already_off_asks_for_no_new_confirmation(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """Re-sending a document that had the rule off widens nothing, so asking
    again would be a dialogue that means nothing."""
    acme.project.masking_config = {"disable": ["cookies"]}

    written = await service.write_masking_config(
        _store(db),
        org_id=acme.organization.id,
        actor_user_id=acme.user.id,
        project_id=acme.project.id,
        document={"disable": ["cookies"], "mask_keys": ["x-session"]},
        confirm_disabling=[],
    )

    assert written.config["mask_keys"] == ["x-session"]
    assert db.entries == []


async def test_confirming_a_rule_that_is_not_being_switched_off_is_refused(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """What keeps the guard real: a client that always sent every rule name would
    turn the confirmation into a constant, and a constant confirms nothing."""
    with pytest.raises(InvalidRequestError, match="does not switch off"):
        await service.write_masking_config(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            project_id=acme.project.id,
            document={"disable": ["cookies"]},
            confirm_disabling=["cookies", "credential_keys"],
        )


async def test_an_invalid_masking_document_never_reaches_the_row(
    db: FakeControlPlane, acme: Tenant
) -> None:
    acme.project.masking_config = {"mask_keys": ["good"]}

    with pytest.raises(InvalidRequestError):
        await service.write_masking_config(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            project_id=acme.project.id,
            document={"disable": ["nonsense"]},
            confirm_disabling=[],
        )

    assert acme.project.masking_config == {"mask_keys": ["good"]}


async def test_writing_config_for_another_organizations_project_is_a_404(
    db: FakeControlPlane, acme: Tenant, globex: Tenant
) -> None:
    with pytest.raises(NotFoundError):
        await service.write_masking_config(
            _store(db),
            org_id=acme.organization.id,
            actor_user_id=acme.user.id,
            project_id=globex.project.id,
            document={"mask_keys": ["theirs"]},
            confirm_disabling=[],
        )
    assert globex.project.masking_config == {}


# ---------------------------------------------------------------- routes


def _build_app(db: FakeControlPlane, identity: Identity) -> Any:
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


async def test_the_create_route_answers_201(db: FakeControlPlane, acme: Tenant) -> None:
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(f"{API_PREFIX}/projects", json={"name": "EU Production"})

    assert response.status_code == 201
    assert response.json()["slug"] == "eu-production"


async def test_the_masking_route_answers_422_naming_what_is_wrong(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """The status the masking contract asks for, at the moment somebody typed the line."""
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.put(
            f"{API_PREFIX}/projects/{acme.project.id}/masking-config",
            json={"config": {"disable": ["credentials"]}},
        )

    assert response.status_code == 422
    assert "credentials" in response.json()["detail"]


async def test_the_masking_route_answers_409_until_the_rule_is_confirmed(
    db: FakeControlPlane, acme: Tenant
) -> None:
    app = _build_app(db, acme.identity)
    body = {"config": {"disable": ["credential_keys"]}}

    async with _client(app) as client:
        refused = await client.put(
            f"{API_PREFIX}/projects/{acme.project.id}/masking-config", json=body
        )
        confirmed = await client.put(
            f"{API_PREFIX}/projects/{acme.project.id}/masking-config",
            json={**body, "confirm_disabling": ["credential_keys"]},
        )

    assert refused.status_code == 409
    assert refused.json()["requires_confirmation"] == ["credential_keys"]
    assert confirmed.status_code == 200
    assert confirmed.json()["notice"] == MASKING_NOTICE


async def test_the_delete_route_answers_200_with_what_it_closed(
    db: FakeControlPlane, acme: Tenant
) -> None:
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.delete(f"{API_PREFIX}/projects/{acme.project.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["api_keys_revoked"] == 1
    assert body["telemetry_notice"] == DELETION_TELEMETRY_NOTICE


async def test_the_routes_refuse_a_role_that_may_not_administer(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """Reading the list is everyone's; changing a project is not."""
    acme.user.role = "member"
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        listed = await client.get(f"{API_PREFIX}/projects")
        created = await client.post(f"{API_PREFIX}/projects", json={"name": "EU"})
        deleted = await client.delete(f"{API_PREFIX}/projects/{acme.project.id}")

    assert listed.status_code == 200
    assert created.status_code == 403
    assert deleted.status_code == 403


async def test_a_bad_slug_is_refused_before_it_reaches_the_service(
    db: FakeControlPlane, acme: Tenant
) -> None:
    """`Production` and `has spaces` are not identifiers; the pattern says so."""
    app = _build_app(db, acme.identity)

    async with _client(app) as client:
        response = await client.post(
            f"{API_PREFIX}/projects", json={"name": "EU", "slug": "Not A Slug"}
        )

    assert response.status_code == 422


def test_the_policy_that_guards_these_routes_refuses_a_member() -> None:
    """The dependency itself, so the refusal above is not an accident of wiring."""
    from nexovue_api.auth.policy import ROLES_THAT_MAY_ADMINISTER, require_role

    guard = require_role(*ROLES_THAT_MAY_ADMINISTER)
    member = Tenant("shell")
    member.user.role = "member"

    with pytest.raises(PermissionDeniedError):
        guard(member.identity)
