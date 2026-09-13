"""The suite that tries to walk around tenant isolation (P1-25).

`test_tenant_scope.py` proves the chokepoint exists and works on the happy
path. This module is the adversarial half: every test here is something an
attacker or a careless change would actually do, and each one is named after
the thing it forbids.

Three layers are attacked, in the order an intruder meets them:

* the repository — can a query reach the driver unscoped, or a caller name a
  tenant that is not theirs;
* the HTTP surface — can one organization's session observe that another
  organization's project, trace or page cursor exists;
* the values in between — can a crafted `project_id` or `trace_id` break out
  of its parameter binding.

Postgres, Redis and ClickHouse are replaced by fakes that enforce the same
predicates the real stores do, so a query that stops filtering by tenant fails
here rather than passing quietly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from fake_stores import use_fake_second_factors

from nexovue_api.auth.clients import ClientDescription
from nexovue_api.auth.credentials import hash_password
from nexovue_api.auth.sessions import Session
from nexovue_api.data.clickhouse.base import (
    TENANT_BINDING,
    TenantFilterMissingError,
    TenantScopedRepository,
)
from nexovue_api.data.postgres.models import Organization, Project, User
from nexovue_api.deps import get_app_settings, get_clickhouse, get_db_session, get_session_store
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.settings import API_PREFIX, Settings
from nexovue_api.traces import queries, service
from nexovue_api.traces.cursor import TracePageCursor

PASSWORD: Final = "correct-horse-battery"

# Fields that legitimately differ between two responses to the same question.
# Everything else in a 404 body must match, or the difference is an oracle.
_PER_REQUEST_FIELDS: Final = frozenset({"instance", "correlation_id"})

_START_NANOS: Final = 1_787_825_704_123_000_000
_SPAN_DURATION_MS: Final = 412.7

TRACE_OWNED_BY_A: Final = "a" * 32
TRACE_OWNED_BY_B: Final = "b" * 32
TRACE_THAT_NEVER_EXISTED: Final = "c" * 32

# A trace id shaped to close the string literal the column is compared against
# and comment out the rest of the statement. It must never reach ClickHouse.
INJECTED_TRACE_ID: Final = "' OR 1=1 --"
INJECTED_PROJECT_ID: Final = "' OR tenant_id != '"


# --------------------------------------------------------------- the two tenants


@dataclass(frozen=True, slots=True)
class Tenant:
    """One organization, its owner, its project and the trace it owns."""

    organization: Organization
    user: User
    project: Project
    trace_id: str

    @property
    def org_id(self) -> uuid.UUID:
        return self.organization.id


def _tenant(slug: str, email: str, trace_id: str) -> Tenant:
    organization = Organization(slug=slug, name=slug.title())
    organization.id = uuid.uuid4()
    organization.deleted_at = None

    user = User(
        org_id=organization.id,
        email=email,
        name=None,
        password_hash=hash_password(PASSWORD),
        role="owner",
    )
    user.id = uuid.uuid4()

    project = Project(org_id=organization.id, slug="production", name="Production")
    project.id = uuid.uuid4()
    project.deleted_at = None

    return Tenant(organization=organization, user=user, project=project, trace_id=trace_id)


@pytest.fixture
def tenant_a() -> Tenant:
    return _tenant("acme", "owner@acme.test", TRACE_OWNED_BY_A)


@pytest.fixture
def tenant_b() -> Tenant:
    return _tenant("initech", "owner@initech.test", TRACE_OWNED_BY_B)


# ------------------------------------------------------------------- the fakes


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


@dataclass(frozen=True, slots=True)
class SpanRow:
    """One stored span, reduced to the three columns tenancy turns on."""

    tenant_id: str
    project_id: str
    trace_id: str


class RecordingTelemetryStore:
    """A ClickHouse stand-in that honours the predicates the real SQL carries.

    It filters on the bound parameters rather than on what the caller meant, so
    a statement that stops naming `tenant_id` returns another tenant's rows
    here exactly as it would in production — which is what makes these tests
    able to fail.
    """

    def __init__(self, rows: list[SpanRow]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.calls.append((sql, bound))

        if "span_trace_lookup" in sql:
            return self._trace_bounds(sql, bound)
        # The list is two statements over the per-trace summary: the page in
        # sort-key order, then the whole of each id on that page. Checked before
        # the `spans` branches, which the same markers would otherwise catch.
        if "FROM trace_summaries" in sql:
            if "{trace_ids:Array(String)}" in sql:
                return self._summary_values(sql, bound)
            return self._summary_page(sql, bound)
        # The fallback, for a request naming an arbitrary attribute: the page of
        # trace ids, then the root span of each. The second is recognised by the
        # array it binds and must be checked first — both group by `trace_id`.
        if "{trace_ids:Array(String)}" in sql:
            return self._trace_roots(sql, bound)
        if "GROUP BY trace_id" in sql:
            return self._trace_list(sql, bound)
        return self._trace_spans(sql, bound)

    def _matching(self, sql: str, bound: dict[str, Any], *, by_project: bool) -> list[SpanRow]:
        """Rows a statement selects, applying only the filters it actually names."""
        selected = self._rows
        if TENANT_BINDING in sql:
            selected = [row for row in selected if row.tenant_id == bound.get("tenant_id")]
        if by_project and "{project_id:String}" in sql:
            selected = [row for row in selected if row.project_id == bound.get("project_id")]
        if "{trace_id:String}" in sql:
            selected = [row for row in selected if row.trace_id == bound.get("trace_id")]
        return selected

    def _trace_bounds(self, sql: str, bound: dict[str, Any]) -> FakeQueryResult:
        matches = len(self._matching(sql, bound, by_project=False))
        return FakeQueryResult(
            ["matches", "min_nanos", "max_nanos"],
            [(matches, _START_NANOS, _START_NANOS + 500_000_000)],
        )

    def _trace_list(self, sql: str, bound: dict[str, Any]) -> FakeQueryResult:
        rows = self._matching(sql, bound, by_project=True)
        if "cursor_trace_id" in bound:
            # The keyset predicate the cursor drives, as ClickHouse applies it.
            rows = [
                row
                for row in rows
                if (_START_NANOS, row.trace_id) < (bound["cursor_nanos"], bound["cursor_trace_id"])
            ]
        return FakeQueryResult(
            [
                "trace_id",
                "start_time_nanos",
                "duration_ms",
                "span_count",
                "error_count",
                "worst_severity",
            ],
            [(row.trace_id, _START_NANOS, _SPAN_DURATION_MS, 1, 0, 1) for row in rows],
        )

    def _summary_page(self, sql: str, bound: dict[str, Any]) -> FakeQueryResult:
        """The page, in the sort key's order and scoped exactly as the statement is."""
        rows = self._matching(sql, bound, by_project=True)
        if "cursor_trace_id" in bound:
            rows = [
                row
                for row in rows
                if (_START_NANOS, row.trace_id) < (bound["cursor_nanos"], bound["cursor_trace_id"])
            ]
        return FakeQueryResult(
            ["trace_id", "start_time_nanos"], [(row.trace_id, _START_NANOS) for row in rows]
        )

    def _summary_values(self, sql: str, bound: dict[str, Any]) -> FakeQueryResult:
        """The whole of each trace on the page, scoped exactly as the statement is."""
        rows = self._matching(sql, bound, by_project=True)
        wanted = set(bound.get("trace_ids", []))
        return FakeQueryResult(
            [
                "trace_id",
                "start_time_nanos",
                "duration_ms",
                "span_count",
                "error_count",
                "worst_severity",
                "root_service",
                "root_name",
                "span_services",
                "span_endpoints",
            ],
            [
                (
                    row.trace_id,
                    _START_NANOS,
                    _SPAN_DURATION_MS,
                    1,
                    0,
                    1,
                    "storefront",
                    "GET /checkout",
                    ["storefront"],
                    # Packed, the way the worker writes them.
                    [f"storefront{queries.ENDPOINT_SEPARATOR}GET /checkout"],
                )
                for row in rows
                if row.trace_id in wanted
            ],
        )

    def _trace_roots(self, sql: str, bound: dict[str, Any]) -> FakeQueryResult:
        """The root span of each id on the page, scoped exactly as the statement is."""
        rows = self._matching(sql, bound, by_project=True)
        wanted = set(bound.get("trace_ids", []))
        return FakeQueryResult(
            ["trace_id", "root_service", "root_name", "span_services", "span_endpoints"],
            [
                (
                    row.trace_id,
                    "storefront",
                    "GET /checkout",
                    ["storefront"],
                    # Read from `spans`, so a pair arrives as a tuple.
                    [("storefront", "GET /checkout")],
                )
                for row in rows
                if row.trace_id in wanted
            ],
        )

    def _trace_spans(self, sql: str, bound: dict[str, Any]) -> FakeQueryResult:
        rows = self._matching(sql, bound, by_project=True)
        return FakeQueryResult(
            [
                "span_id",
                "parent_span_id",
                "service_name",
                "span_name",
                "span_kind",
                "start_time_nanos",
                "duration_ms",
                "status_code",
                "status_message",
                "http_method",
                "http_route",
                "http_status",
                "rpc_system",
                "rpc_method",
                "rpc_status",
                "attributes_json",
            ],
            [
                (
                    b"00f067aa0ba902b7",
                    "",
                    "storefront",
                    "GET /checkout",
                    "SERVER",
                    _START_NANOS,
                    _SPAN_DURATION_MS,
                    "OK",
                    "",
                    "GET",
                    "/checkout",
                    200,
                    "",
                    "",
                    "",
                    "{}",
                )
                for _ in rows
            ],
        )


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Row:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def first(self) -> tuple[Any, ...] | None:
        return self._row


class FakeControlPlane:
    """Postgres reduced to the rows these tests need.

    Every lookup is answered from the statement's *bound values*, never from
    what the caller intended: a `find_project` that stopped filtering on
    `org_id` would no longer bind one, and the project would not be found.
    A query that drops its tenancy predicate therefore fails here.
    """

    def __init__(
        self, tenants: list[Tenant], *, deleted_orgs: set[uuid.UUID] | None = None
    ) -> None:
        self._tenants = tenants
        self._deleted = deleted_orgs or set()
        self.written: list[Any] = []

    def add(self, entity: Any) -> None:
        """Rows the request wrote — audit entries here. Kept so nothing is lost silently."""
        self.written.append(entity)

    async def execute(self, statement: Any) -> Any:
        """`load_identity` and `find_active_user_by_email`: a (user, org) pair.

        `first_reported_days` also arrives here. These tenants have no metered
        rows, so every project reads as one nothing has ever arrived for — which
        is what the listing test asserts about, and it asserts about ids.
        """
        values = _bound_values(statement)
        if "usage_daily" in str(statement.get_final_froms()[0]):
            return _Rows([])
        for tenant in self._tenants:
            if tenant.organization.id in self._deleted:
                continue
            by_email = tenant.user.email in values
            # A session names both ids, and both must agree for it to resolve.
            by_session = tenant.user.id in values and tenant.organization.id in values
            if by_email or by_session:
                return _Row((tenant.user, tenant.organization))
        return _Row(None)

    async def scalar(self, statement: Any) -> Any:
        """`find_project`: a project only when the statement binds its owner too."""
        values = _bound_values(statement)
        for tenant in self._tenants:
            if tenant.project.id in values and tenant.organization.id in values:
                return tenant.project
        return None

    async def scalars(self, statement: Any) -> _Rows:
        """`list_projects`: whichever organization the statement binds."""
        values = _bound_values(statement)
        return _Rows(
            [tenant.project for tenant in self._tenants if tenant.organization.id in values]
        )


def _bound_values(statement: Any) -> set[Any]:
    """Every literal a SQLAlchemy statement carries into the driver."""
    return set(statement.compile().params.values())


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


# ------------------------------------------------------------------ the harness


@dataclass
class Stack:
    """The app, the stores behind it, and a client per tenant."""

    app: Any
    telemetry: RecordingTelemetryStore
    sessions: FakeSessionStore


def _settings() -> Settings:
    return Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]


def _build_stack(tenants: list[Tenant], *, deleted_orgs: set[uuid.UUID] | None = None) -> Stack:
    # Imported here so `environment` has already populated the configuration
    # `main` reads when it builds its module-level app.
    from nexovue_api.main import create_app

    settings = _settings()
    app = create_app(settings)

    telemetry = RecordingTelemetryStore(
        [SpanRow(str(t.org_id), str(t.project.id), t.trace_id) for t in tenants]
    )
    sessions = FakeSessionStore()
    control_plane = FakeControlPlane(tenants, deleted_orgs=deleted_orgs)

    async def db_override() -> AsyncIterator[Any]:
        yield control_plane

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_session_store] = lambda: sessions
    # Sign-in asks whether a second factor is required, so every suite that
    # drives it needs that store too.
    use_fake_second_factors(app)
    app.dependency_overrides[get_clickhouse] = lambda: telemetry
    return Stack(app=app, telemetry=telemetry, sessions=sessions)


def _client(stack: Stack) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stack.app), base_url="http://testserver"
    )


@asynccontextmanager
async def signed_in(stack: Stack, tenant: Tenant) -> AsyncIterator[httpx.AsyncClient]:
    """A client carrying a real session for this tenant, minted by logging in."""
    async with _client(stack) as client:
        response = await client.post(
            f"{API_PREFIX}/auth/login", json={"email": tenant.user.email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        yield client


def _comparable(body: dict[str, Any]) -> dict[str, Any]:
    """A problem body with the per-request fields removed, so two can be equal."""
    return {key: value for key, value in body.items() if key not in _PER_REQUEST_FIELDS}


@pytest.fixture
def stack(tenant_a: Tenant, tenant_b: Tenant) -> Iterator[Stack]:
    yield _build_stack([tenant_a, tenant_b])


# ================================================== against the repository layer


@pytest.mark.parametrize(
    ("hiding_place", "sql"),
    [
        # ClickHouse ignores a parameter the statement never uses, so a binding
        # inside a comment costs nothing and scopes nothing.
        ("a comment", f"/* {TENANT_BINDING} */ SELECT trace_id FROM spans"),
        # ClickHouse does not substitute parameters inside string literals: the
        # text stays literal and the statement is unfiltered.
        ("a string literal", f"SELECT trace_id FROM spans WHERE span_name = '{TENANT_BINDING}'"),
        # Substituted, but as a projected column — a value, not a predicate.
        ("the select list", f"SELECT {TENANT_BINDING} AS bound, trace_id FROM spans"),
    ],
)
async def test_a_tenant_binding_that_filters_nothing_must_not_satisfy_the_guard(
    hiding_place: str, sql: str
) -> None:
    """The guard is a substring test, so a binding anywhere in the text passes it.

    Each of these reads every tenant's spans on a real server. Verified against
    ClickHouse 26.3: an unused parameter is accepted, and `{tenant_id:String}`
    inside a single-quoted literal is returned as text rather than substituted.
    """
    store = RecordingTelemetryStore([])
    repository = TenantScopedRepository(store, tenant_id="org-a")  # type: ignore[arg-type]

    with pytest.raises(TenantFilterMissingError):
        await repository.query(sql)

    assert not store.calls, f"a query scoped only by {hiding_place} reached the driver"


async def test_a_caller_supplied_project_id_cannot_override_a_repository_bound_to_one() -> None:
    """A repository given a project must not let the caller widen it to another."""
    store = RecordingTelemetryStore([])
    repository = TenantScopedRepository(
        store,  # type: ignore[arg-type]
        tenant_id="org-a",
        project_id="project-a",
    )

    await repository.query(
        f"SELECT trace_id FROM spans WHERE tenant_id = {TENANT_BINDING}",
        {"project_id": "project-b", "tenant_id": "org-b"},
    )

    _, bound = store.calls[-1]
    assert bound["project_id"] == "project-a"
    assert bound["tenant_id"] == "org-a"


async def test_a_hostile_parameter_value_is_bound_and_never_spliced_into_the_sql() -> None:
    """Injection would need the value in the statement text; it goes beside it."""
    store = RecordingTelemetryStore([])
    repository = TenantScopedRepository(store, tenant_id="org-a")  # type: ignore[arg-type]

    await repository.query(
        queries.TRACE_SPANS_SQL,
        {
            "project_id": INJECTED_PROJECT_ID,
            "trace_id": INJECTED_TRACE_ID,
            "window_start": datetime.now(UTC),
            "window_end": datetime.now(UTC),
            "limit": 1,
        },
    )

    sql, bound = store.calls[-1]
    assert sql == queries.TRACE_SPANS_SQL, "the statement text was rebuilt around a caller's value"
    assert bound["trace_id"] == INJECTED_TRACE_ID
    assert INJECTED_TRACE_ID not in sql
    assert INJECTED_PROJECT_ID not in sql


async def test_a_trace_id_that_is_not_32_hex_characters_never_reaches_the_store() -> None:
    """The shape check runs before the query, so a crafted id costs no round trip."""
    store = RecordingTelemetryStore([])
    repository = TenantScopedRepository(store, tenant_id="org-a")  # type: ignore[arg-type]

    with pytest.raises(NotFoundError):
        await service.get_trace(repository, project_id="project-a", trace_id=INJECTED_TRACE_ID)

    assert not store.calls


async def test_a_trace_the_tenant_does_not_own_costs_the_same_queries_as_a_missing_one() -> None:
    """A different number of round trips is a timing oracle even with the same body."""
    rows = [SpanRow("org-a", "project-a", TRACE_OWNED_BY_A)]

    other_tenant = RecordingTelemetryStore([*rows, SpanRow("org-b", "project-b", TRACE_OWNED_BY_B)])
    never_existed = RecordingTelemetryStore(rows)
    attempts = ((other_tenant, TRACE_OWNED_BY_B), (never_existed, TRACE_THAT_NEVER_EXISTED))

    for store, trace_id in attempts:
        repository = TenantScopedRepository(store, tenant_id="org-a")  # type: ignore[arg-type]
        with pytest.raises(NotFoundError):
            await service.get_trace(repository, project_id="project-a", trace_id=trace_id)

    assert [sql for sql, _ in other_tenant.calls] == [sql for sql, _ in never_existed.calls]


# ==================================================== against the HTTP surface


async def test_another_organizations_project_is_indistinguishable_from_one_that_never_existed(
    stack: Stack, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    """A 404 that differs would confirm the other organization's project exists."""
    async with signed_in(stack, tenant_a) as client:
        theirs = await client.get(
            f"{API_PREFIX}/traces", params={"project_id": str(tenant_b.project.id)}
        )
        nobodys = await client.get(f"{API_PREFIX}/traces", params={"project_id": str(uuid.uuid4())})

    assert theirs.status_code == nobodys.status_code == 404
    assert _comparable(theirs.json()) == _comparable(nobodys.json())


async def test_another_organizations_project_never_reaches_a_telemetry_query(
    stack: Stack, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    """`require_project` is the gate; a rejected project must not touch ClickHouse."""
    async with signed_in(stack, tenant_a) as client:
        await client.get(f"{API_PREFIX}/traces", params={"project_id": str(tenant_b.project.id)})

    assert not stack.telemetry.calls


async def test_another_organizations_trace_is_a_404_identical_to_a_nonexistent_one(
    stack: Stack, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    """Same status, same problem type, same detail — the id is not confirmed."""
    async with signed_in(stack, tenant_a) as client:
        theirs = await client.get(
            f"{API_PREFIX}/traces/{tenant_b.trace_id}",
            params={"project_id": str(tenant_a.project.id)},
        )
        nobodys = await client.get(
            f"{API_PREFIX}/traces/{TRACE_THAT_NEVER_EXISTED}",
            params={"project_id": str(tenant_a.project.id)},
        )

    assert theirs.status_code == nobodys.status_code == 404
    assert _comparable(theirs.json()) == _comparable(nobodys.json())


async def test_every_telemetry_query_binds_the_tenant_from_the_session(
    stack: Stack, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    """Not one query may run as anything but the signed-in organization."""
    async with signed_in(stack, tenant_a) as client:
        await client.get(f"{API_PREFIX}/traces", params={"project_id": str(tenant_a.project.id)})
        await client.get(
            f"{API_PREFIX}/traces/{tenant_b.trace_id}",
            params={"project_id": str(tenant_a.project.id)},
        )

    assert stack.telemetry.calls, "no query ran, so this proves nothing"
    assert {bound["tenant_id"] for _, bound in stack.telemetry.calls} == {str(tenant_a.org_id)}


async def test_a_project_id_that_is_not_a_uuid_never_reaches_a_query(
    stack: Stack, tenant_a: Tenant
) -> None:
    """The router types it as a UUID, so a crafted value is rejected at the edge."""
    async with signed_in(stack, tenant_a) as client:
        response = await client.get(
            f"{API_PREFIX}/traces", params={"project_id": INJECTED_PROJECT_ID}
        )

    assert response.status_code == 422
    assert not stack.telemetry.calls


async def test_a_session_for_a_deleted_organization_is_refused(
    tenant_a: Tenant, tenant_b: Tenant
) -> None:
    """The token outlives the row in Redis; the identity lookup must not."""
    stack = _build_stack([tenant_a, tenant_b])

    async with signed_in(stack, tenant_a) as client:
        # The organization is deleted while the session is still live.
        stack.app.dependency_overrides[get_db_session] = _control_plane_without(
            [tenant_a, tenant_b], deleted={tenant_a.org_id}
        )
        identity = await client.get(f"{API_PREFIX}/auth/me")
        traces = await client.get(
            f"{API_PREFIX}/traces", params={"project_id": str(tenant_a.project.id)}
        )

    assert identity.status_code == 401
    assert traces.status_code == 401
    assert not stack.telemetry.calls


def _control_plane_without(tenants: list[Tenant], *, deleted: set[uuid.UUID]) -> Any:
    async def override() -> AsyncIterator[Any]:
        yield FakeControlPlane(tenants, deleted_orgs=deleted)

    return override


async def test_a_projects_listing_shows_only_the_signed_in_organizations_projects(
    stack: Stack, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    async with signed_in(stack, tenant_a) as client:
        response = await client.get(f"{API_PREFIX}/projects")

    assert [project["id"] for project in response.json()["projects"]] == [str(tenant_a.project.id)]


# ============================================================ cursor tampering


async def test_a_cursor_forged_from_another_tenants_row_cannot_cross_the_boundary(
    stack: Stack, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    """The cursor is opaque but not trusted: it drives a keyset predicate inside
    an already tenant-scoped subquery, so forging one can only move the page."""
    forged = TracePageCursor(start_time_nanos=_START_NANOS + 1, trace_id=tenant_b.trace_id).encode()

    async with signed_in(stack, tenant_a) as client:
        response = await client.get(
            f"{API_PREFIX}/traces",
            params={"project_id": str(tenant_a.project.id), "cursor": forged},
        )

    assert response.status_code == 200
    assert [trace["trace_id"] for trace in response.json()["traces"]] == [tenant_a.trace_id]

    paged = [call for call in stack.telemetry.calls if "cursor_trace_id" in call[1]]
    assert len(paged) == 1, "the keyset predicate did not reach exactly one statement"
    sql, bound = paged[0]
    assert bound["tenant_id"] == str(tenant_a.org_id)
    assert bound["cursor_trace_id"] == tenant_b.trace_id, "the forged id was not the one bound"
    assert all(tenant_b.trace_id not in text for text, _ in stack.telemetry.calls), (
        "a cursor value was spliced into a statement's text"
    )
    assert tenant_b.trace_id not in sql


async def test_a_cursor_cannot_widen_the_page_beyond_the_projects_own_rows(
    stack: Stack, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    """Even a cursor claiming the far future pages only this tenant's rows."""
    far_future = TracePageCursor(start_time_nanos=_START_NANOS * 2, trace_id="f" * 32).encode()

    async with signed_in(stack, tenant_a) as client:
        response = await client.get(
            f"{API_PREFIX}/traces",
            params={"project_id": str(tenant_a.project.id), "cursor": far_future},
        )

    assert [trace["trace_id"] for trace in response.json()["traces"]] == [tenant_a.trace_id]


@pytest.mark.parametrize("forged", ["not-base64!!", "Zm9v", "MTIz", "%%%"])
async def test_a_cursor_this_endpoint_did_not_issue_is_rejected_rather_than_paged(
    stack: Stack, tenant_a: Tenant, forged: str
) -> None:
    async with signed_in(stack, tenant_a) as client:
        response = await client.get(
            f"{API_PREFIX}/traces",
            params={"project_id": str(tenant_a.project.id), "cursor": forged},
        )

    assert response.status_code == 422


# ============================================== the window the caller chooses


async def test_a_window_a_caller_widens_still_reads_only_their_own_tenant(
    stack: Stack, tenant_a: Tenant
) -> None:
    """A caller controls `from` and `to`; neither may reach outside the tenant."""
    far_past = (datetime.now(UTC) - timedelta(days=3650)).isoformat()
    far_future = (datetime.now(UTC) + timedelta(days=3650)).isoformat()

    async with signed_in(stack, tenant_a) as client:
        response = await client.get(
            f"{API_PREFIX}/traces",
            params={
                "project_id": str(tenant_a.project.id),
                "from": far_past,
                "to": far_future,
            },
        )

    assert response.status_code == 200
    assert [trace["trace_id"] for trace in response.json()["traces"]] == [tenant_a.trace_id]
    assert {bound["tenant_id"] for _, bound in stack.telemetry.calls} == {str(tenant_a.org_id)}
