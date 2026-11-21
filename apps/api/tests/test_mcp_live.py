"""The MCP endpoint's door (F36, FR33, AC33), against real Postgres.

What it proves: the assistant endpoint takes the same token the read API takes
and nothing else; a refusal is one sentence a model can repeat; a request that
gets through carries the token's own organization and scope into the tools; and
the token's use is counted exactly as an HTTP read's is.

The tools themselves are proved by `test_mcp_tools.py` (they are reads, and
bounded) and by the read-surface suites they call into: a tool is a read-surface
request made in process, so proving the surface proves what a tool may see.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import httpx
import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexovue_api.apitokens import service as api_tokens
from nexovue_api.apitokens.usage import TokenUsage
from nexovue_api.assistant.auth import NO_TOKEN, READER_KEY, AssistantAuthentication
from nexovue_api.assistant.tools import _project_for
from nexovue_api.auth.dependencies import Reader
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.findings import service as findings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
PASSWORD: Final = "correct horse battery staple"
INGEST_KEY: Final = "nvk_live_devlocaldevlocaldevloc"


def _reachable(url: str, port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or port), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN, 5432),
    suite="apps/api/tests/test_mcp_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    made = create_async_engine(ASYNC_DSN)
    try:
        yield made
    finally:
        await made.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Committing, not rolled back: the wrapper opens its own session, so what
    this fixture writes has to be visible from another connection. What it wrote
    is deleted afterwards, by the organization it hangs from."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
        provisioned = getattr(session, "_provisioned", [])
    async with AsyncSession(engine) as cleanup:
        for tenant in provisioned:
            await cleanup.execute(
                text("DELETE FROM organizations WHERE id = :org_id"),
                {"org_id": tenant.organization.id},
            )
        await cleanup.commit()


async def _tenant(db: AsyncSession, name: str = "Acme") -> ProvisionedTenant:
    tenant = await provision_tenant(
        db,
        organization_name=f"{name} {uuid.uuid4().hex[:6]}",
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password=PASSWORD,
    )
    await db.commit()
    provisioned = getattr(db, "_provisioned", [])
    provisioned.append(tenant)
    db._provisioned = provisioned  # type: ignore[attr-defined]
    return tenant


async def _token(
    db: AsyncSession, tenant: ProvisionedTenant, *, project_id: uuid.UUID | None
) -> str:
    made = await api_tokens.create(
        db,
        org_id=tenant.organization.id,
        actor=tenant.user,
        name="Assistant",
        project_id=project_id,
        expires_in_days=90,
        now=datetime.now(UTC),
    )
    await db.commit()
    return made.token.get_secret_value()


class _Resources:
    """What the wrapper reads off the app: a session factory and the usage counter."""

    def __init__(self, engine: Any) -> None:
        self.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self.token_usage = TokenUsage(self.session_factory)


def _app(resources: _Resources, seen: list[dict[str, Any]]) -> Any:
    """The wrapper around an inner app that records what reached it."""

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["state"][READER_KEY])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    wrapped = AssistantAuthentication(inner)

    class _State:
        pass

    state = _State()
    state.resources = resources  # type: ignore[attr-defined]

    class _App:
        def __init__(self) -> None:
            self.state = state

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            scope["app"] = self
            await wrapped(scope, receive, send)

    return _App()


async def _call(app: Any, presented: str | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {presented}"} if presented else {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://assistant") as client:
        return await client.post("/api/mcp/", json={"jsonrpc": "2.0"}, headers=headers)


async def test_an_assistant_with_no_token_is_told_where_to_get_one(engine: AsyncEngine) -> None:
    seen: list[dict[str, Any]] = []
    app = _app(_Resources(engine), seen)

    answer = await _call(app, None)

    assert answer.status_code == 401
    assert answer.json()["error"] == NO_TOKEN
    assert "Bearer" in answer.headers["www-authenticate"]
    assert seen == []


@pytest.mark.parametrize(
    "presented",
    ["nvt_live_notarealtokenatallnotarealtoken", INGEST_KEY, "a-session-token"],
    ids=["invented token", "ingest key", "session token"],
)
async def test_nothing_but_a_live_api_token_reaches_the_tools(
    engine: AsyncEngine, presented: str
) -> None:
    seen: list[dict[str, Any]] = []
    app = _app(_Resources(engine), seen)

    answer = await _call(app, presented)

    assert answer.status_code == 401
    assert seen == []


async def test_a_live_token_carries_its_organization_and_scope_to_the_tools(
    db: AsyncSession, engine: AsyncEngine
) -> None:
    """AC33: a tool cannot see what its endpoint would refuse, because it is
    handed the same organization and the same project scope."""
    acme = await _tenant(db)
    presented = await _token(db, acme, project_id=acme.project.id)
    seen: list[dict[str, Any]] = []
    app = _app(_Resources(engine), seen)

    answer = await _call(app, presented)

    assert answer.status_code == 200
    assert seen == [
        {
            "org_id": acme.organization.id,
            "project_scope": acme.project.id,
            "token_id": seen[0]["token_id"],
        }
    ]


async def test_an_assistant_request_counts_against_the_token(
    db: AsyncSession, engine: AsyncEngine
) -> None:
    """FR33: it updates the token's last used, exactly as an HTTP read does."""
    acme = await _tenant(db)
    presented = await _token(db, acme, project_id=None)
    resources = _Resources(engine)
    app = _app(resources, [])

    await _call(app, presented)
    # Counted in memory; the once-a-minute write is what the list reads.
    await resources.token_usage.flush()

    listed = await api_tokens.list_for_organization(
        db, org_id=acme.organization.id, now=datetime.now(UTC)
    )
    assert listed.api_tokens[0].last_used_at is not None
    assert listed.api_tokens[0].requests_today >= 1


async def test_a_revoked_token_is_refused_in_a_sentence_a_model_can_repeat(
    db: AsyncSession, engine: AsyncEngine
) -> None:
    acme = await _tenant(db)
    presented = await _token(db, acme, project_id=None)
    live = await api_tokens.token_for(db, presented, now=datetime.now(UTC))
    await api_tokens.revoke(
        db,
        org_id=acme.organization.id,
        actor=acme.user,
        token_id=live.id,
        now=datetime.now(UTC),
    )
    await db.commit()
    seen: list[dict[str, Any]] = []
    app = _app(_Resources(engine), seen)

    answer = await _call(app, presented)

    assert answer.status_code == 401
    assert json.loads(answer.text)["error"] == api_tokens.TOKEN_UNKNOWN
    assert seen == []


# ------------------------------------------------- what the tools pass down (F4)

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)

require_stack(
    reachable=_reachable(CLICKHOUSE_DSN, 8123),
    suite="apps/api/tests/test_mcp_live.py",
    remedy="ClickHouse is not reachable — run `just up`",
)


@pytest.fixture
async def clickhouse() -> AsyncIterator[AsyncClient]:
    parts = urlsplit(CLICKHOUSE_DSN)
    client = await clickhouse_connect.get_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "nexovue",
        password=parts.password or "nexovue_dev",
        database=(parts.path or "/nexovue").lstrip("/"),
    )
    try:
        yield client
    finally:
        await client.close()


async def test_a_malformed_project_id_is_refused_in_a_sentence(db: AsyncSession) -> None:
    """FR35: `uuid.UUID('not-an-id')` used to raise a bare ValueError, which the
    tool wrapper hides as "error executing tool" — an answer a model cannot act
    on. The refusal must say what a project_id is and where to get one."""
    acme = await _tenant(db)
    reader = Reader(org_id=acme.organization.id, project_scope=None, token_id=uuid.uuid4())

    with pytest.raises(InvalidRequestError) as refused:
        await _project_for(db, reader, "not-a-project-id")

    assert "list_projects" in refused.value.detail


async def test_get_finding_as_a_token_has_no_person_fields_and_keeps_scope(
    db: AsyncSession, clickhouse: AsyncClient
) -> None:
    """AC46: the view `get_finding` reads with `for_member=False` names nobody
    and quotes nothing a person wrote, and a finding outside the token's scope
    answers byte-identically to one that does not exist."""
    acme = await _tenant(db)
    row = FindingRow(
        org_id=acme.organization.id,
        project_id=acme.project.id,
        scope="service",
        service_name="checkout",
        severity="warning",
        state="open",
        title="checkout is 3 times slower than normal since 14:02 UTC",
        signals=[
            {
                "signal": "slower",
                "normal": 110.0,
                "current": 330.0,
                "worst": 412.0,
                "comparison": "Tuesdays around 15:00, last 3 weeks",
                "endpoint": None,
            }
        ],
        started_at=datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    repository = TenantScopedRepository(clickhouse, tenant_id=str(acme.organization.id))

    finding = await findings.get_finding(
        db,
        repository,
        org_id=acme.organization.id,
        finding_id=row.id,
        project_scope=acme.project.id,
        for_member=False,
        now=datetime.now(UTC),
    )

    dumped = finding.model_dump()
    for person_field in (
        "acknowledged_by",
        "acknowledged_by_name",
        "feedback_reason",
        "new_normal",
    ):
        assert person_field not in dumped, f"a token's finding carries {person_field}"

    with pytest.raises(NotFoundError) as out_of_scope:
        await findings.get_finding(
            db,
            repository,
            org_id=acme.organization.id,
            finding_id=row.id,
            project_scope=uuid.uuid4(),
            for_member=False,
            now=datetime.now(UTC),
        )
    with pytest.raises(NotFoundError) as never_existed:
        await findings.get_finding(
            db,
            repository,
            org_id=acme.organization.id,
            finding_id=uuid.uuid4(),
            project_scope=acme.project.id,
            for_member=False,
            now=datetime.now(UTC),
        )
    assert out_of_scope.value.detail == never_existed.value.detail
