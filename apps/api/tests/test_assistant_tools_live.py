"""F4 plan T3.1, T3.1a and T2.8's live arm: every catalog tool over real HTTP, as a ticket
(AC53, AC8, AC10).

The real application is served by uvicorn; reads go through the worker's own client
(`CurlApiClient`) with a live question's ticket, exactly as the loop sends them.

- **AC53.** Each tool, with valid arguments, answers 200 in its route's shape (`rows_of`
  reads it), and each answers the byte-identical 404 for another project: the other
  organization's project, or its item, reads as one that does not exist.
- **AC10, through real uvicorn.** Path tricks in every argument of every tool are refused
  before any read, and the server sees no request for them; and the danger is real on this
  server: sent raw, `x%2Fdetection` reaches another tool's handler.

Rows are committed, since the server uses its own connections; each organization made here
is deleted after, and the ClickHouse rows it wrote are deleted by its own tenant id.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import environment  # noqa: F401 — sets what importing the app reads
import pycurl
import pytest
from assistant_live import AVAILABLE, KEYS, held_from_the_stack, require_postgres
from assistant_served import (
    committed_tenant,
    paths_seen,
    provisioned_install,
    serve,
    tenants_for,
)
from nexovue_platform.assistant_records import TICKET_PURPOSE
from nexovue_platform.assistant_tools import CATALOG, TOOLS_BY_NAME, ChatTool, rows_of
from nexovue_platform.checks.sealing import SealedBox, keyring_from
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from nexovue_api.assistant import service
from nexovue_api.assistant.schemas import AskContext
from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.postgres.models import (
    AssistantMessage,
    Finding,
    Incident,
    Project,
    Release,
    ReleasePeriod,
)
from nexovue_api.signup.service import ProvisionedTenant
from nexovue_scheduler.insight.api_client import CurlApiClient
from nexovue_scheduler.insight.assistant_reads import ArgumentsRefused, check_arguments

require_postgres("chat's tool suite")

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
SERVICE: Final = "tools-checkout"
TRACE: Final = "a1" * 16
SPAN: Final = "b2" * 8
EXCEPTION: Final = "java.net.ConnectException"
METRIC: Final = "http.server.duration"
# The tricks of AC10: each would move a route if it reached a path segment as it is.
PATH_TRICKS: Final = (
    "../../audit",
    "x/detection",
    "abc%2Fdetection",
    "..",
    ".",
    "a\\b",
    "a?b=1",
    "a#b",
    "a\x00b",
)


@pytest.fixture
async def install() -> AsyncIterator[AsyncEngine]:
    engine = await provisioned_install()
    yield engine
    await engine.dispose()


@pytest.fixture
async def tenants(install: AsyncEngine) -> AsyncIterator[list[ProvisionedTenant]]:
    async for made in tenants_for(install):
        yield made


@pytest.fixture
def seen() -> list[str]:
    return []


@pytest.fixture
def api_url(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> Iterator[str]:
    with serve(monkeypatch, lambda app: paths_seen(app, seen)) as url:
        yield url


def _clickhouse() -> Any:
    parts = urlsplit(CLICKHOUSE_DSN)
    return clickhouse_connect.get_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=(parts.path or "/").lstrip("/"),
    )


@pytest.fixture
def clickhouse() -> Iterator[Any]:
    client = _clickhouse()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def written(clickhouse: Any) -> Iterator[list[str]]:
    """The tenant ids this test wrote telemetry under, deleted after."""
    tenants: list[str] = []
    yield tenants
    for tenant in tenants:
        for table in ("spans", "span_trace_lookup", "trace_summaries", "logs"):
            clickhouse.command(
                f"DELETE FROM {table} WHERE tenant_id = {{tenant:String}}",
                parameters={"tenant": tenant},
            )


def _telemetry(clickhouse: Any, written: list[str], tenant: ProvisionedTenant) -> None:
    """One failing request in the last few minutes: a span with an exception, its trace's
    lookup and summary, and an error log."""
    org, project = str(tenant.organization.id), str(tenant.project.id)
    written.append(org)
    started = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    clickhouse.insert(
        "spans",
        [
            [
                org,
                project,
                TRACE,
                SPAN,
                "",
                SERVICE,
                "POST /pay",
                started,
                started,
                20.0,
                "STATUS_CODE_ERROR",
                EXCEPTION,
                "refused",
            ]
        ],
        column_names=[
            "tenant_id",
            "project_id",
            "trace_id",
            "span_id",
            "parent_span_id",
            "service_name",
            "span_name",
            "timestamp",
            "received_at",
            "duration_ms",
            "status_code",
            "exception_type",
            "exception_message",
        ],
    )
    clickhouse.insert(
        "span_trace_lookup",
        [[org, TRACE, started, started + timedelta(milliseconds=20), started]],
        column_names=["tenant_id", "trace_id", "span_start", "span_end", "received_at"],
    )
    clickhouse.insert(
        "trace_summaries",
        [
            [
                org,
                project,
                started,
                TRACE,
                started,
                20.0,
                1,
                1,
                2,
                SERVICE,
                "POST /pay",
                [SERVICE],
                [],
            ]
        ],
        column_names=[
            "tenant_id",
            "project_id",
            "start_time",
            "trace_id",
            "received_at",
            "duration_ms",
            "span_count",
            "error_count",
            "worst_severity",
            "root_service",
            "root_name",
            "span_services",
            "span_endpoints",
        ],
    )
    clickhouse.insert(
        "logs",
        [[org, project, started, started, SERVICE, "ERROR", 17, "payment refused"]],
        column_names=[
            "tenant_id",
            "project_id",
            "timestamp",
            "received_at",
            "service_name",
            "severity",
            "severity_number",
            "body",
        ],
    )


async def _records(install: AsyncEngine, tenant: ProvisionedTenant) -> dict[str, str]:
    """A finding, an incident and a release with a period, in the tenant's project."""
    now = datetime.now(UTC)
    org, project = tenant.organization.id, tenant.project.id
    async with AsyncSession(install, expire_on_commit=False) as db, db.begin():
        finding = Finding(
            org_id=org,
            project_id=project,
            scope="service",
            service_name=SERVICE,
            severity="warning",
            title=f"{SERVICE} is slower than normal",
            signals=[],
            started_at=now - timedelta(hours=1),
        )
        incident = Incident(
            org_id=org,
            project_id=project,
            rule_name="Checkout errors",
            condition_kind="service_error_rate",
            subject_key=SERVICE,
            subject_label=SERVICE,
            severity="warning",
            opened_at=now - timedelta(minutes=30),
        )
        release = Release(
            org_id=org,
            project_id=project,
            service_name=SERVICE,
            version="2.1",
            first_seen_at=now - timedelta(minutes=40),
            last_seen_at=now,
        )
        db.add_all([finding, incident, release])
        await db.flush()
        db.add(
            ReleasePeriod(
                org_id=org,
                release_id=release.id,
                project_id=project,
                service_name=SERVICE,
                started_at=now - timedelta(minutes=40),
                start_basis="seen",
            )
        )
    return {
        "finding_id": str(finding.id),
        "incident_id": str(incident.id),
        "release_id": str(release.id),
    }


async def _second_project(install: AsyncEngine, tenant: ProvisionedTenant) -> str:
    async with AsyncSession(install, expire_on_commit=False) as db, db.begin():
        project = Project(org_id=tenant.organization.id, slug="staging", name="staging")
        db.add(project)
        await db.flush()
        return str(project.id)


async def _ticket(install: AsyncEngine, tenant: ProvisionedTenant) -> str:
    """A live question's ticket, as the worker opens it."""
    async with AsyncSession(install, expire_on_commit=False) as db, db.begin():
        view = await service.ask(
            db,
            identity=Identity(user=tenant.user, organization=tenant.organization),
            project_id=tenant.project.id,
            conversation_id=None,
            question="What is failing right now?",
            context=AskContext(),
            ai=AVAILABLE,
            keys=KEYS,
            now=datetime.now(UTC),
        )
        await held_from_the_stack(db, view.id)
        message = await db.get(AssistantMessage, view.id)
        assert message is not None and message.sealed_ticket is not None
        box = SealedBox(keyring_from(KEYS.current, KEYS.previous), purpose=TICKET_PURPOSE)
        return box.open(message.sealed_ticket, owner=str(message.id))


def _arguments(tool: ChatTool, ids: Mapping[str, str], window: Mapping[str, str]) -> dict[str, Any]:
    """Valid arguments for a tool: the window, a service, and the ids its slots need."""
    given: dict[str, Any] = {}
    for name, rule in tool.arguments.items():
        if rule.kind == "time":
            given[name] = window[name]
        elif rule.kind == "service_name" and rule.required:
            given[name] = SERVICE
        elif rule.kind == "metric_name":
            given[name] = METRIC
        elif rule.kind == "search_text" and rule.required:
            # Knowledge search (Step 5) needs its keywords.
            given[name] = "restart"
        elif rule.in_path:
            given[name] = ids[name]
    return given


def _get(
    client: CurlApiClient,
    tool: ChatTool,
    arguments: Mapping[str, Any],
    *,
    ticket: str,
    project: str,
) -> tuple[int | None, bytes]:
    call = check_arguments(tool, arguments, level="facts_and_text", now=datetime.now(UTC))
    query = {**call.query, "project_id": project} if tool.project_in_query else call.query
    answer = client.get(tool, query, ticket=ticket, path=call.path)
    return answer.status, answer.body


async def test_every_tool_reads_as_the_question_s_project_and_never_another(
    api_url: str,
    install: AsyncEngine,
    tenants: list[ProvisionedTenant],
    clickhouse: Any,
    written: list[str],
) -> None:
    """AC53, AC8, T3.1a: 200 in the route's shape for every tool, through a ticket scoped to
    one project; another organization's project or item is the byte-identical 404."""
    own = await committed_tenant(install, tenants, "ToolsA")
    other = await committed_tenant(install, tenants, "ToolsB")
    _telemetry(clickhouse, written, own)
    ids = await _records(install, own)
    others = await _records(install, other)
    sibling = await _second_project(install, own)
    ticket = await _ticket(install, own)
    client = CurlApiClient(api_url)
    now = datetime.now(UTC)
    window = {"from": (now - timedelta(hours=1)).isoformat(), "to": now.isoformat()}
    project = str(own.project.id)

    # Ids a model would take from earlier reads: the trace and the error group.
    status, body = _get(
        client, TOOLS_BY_NAME["list_traces"], window, ticket=ticket, project=project
    )
    assert status == 200, body
    ids["trace_id"] = json.loads(body)["traces"][0]["trace_id"]
    status, body = _get(
        client, TOOLS_BY_NAME["list_error_groups"], window, ticket=ticket, project=project
    )
    assert status == 200, body
    ids["group_id"] = json.loads(body)["groups"][0]["group_id"]

    for tool in CATALOG:
        arguments = _arguments(tool, ids, window)
        status, body = _get(client, tool, arguments, ticket=ticket, project=project)
        assert status == 200, (tool.name, body[:300])
        assert rows_of(tool, json.loads(body)) is not None, tool.name

        # AC8: another organization's project, or its item, answers as one that never was.
        if tool.project_in_query:
            outside = _get(client, tool, arguments, ticket=ticket, project=str(other.project.id))
            never = _get(client, tool, arguments, ticket=ticket, project=str(uuid.uuid4()))
            # The ticket reads one project: its organization's other project is not it.
            beside = _get(client, tool, arguments, ticket=ticket, project=sibling)
            assert _without_request_id(beside[1]) == _without_request_id(never[1]), tool.name
        else:
            slots = {name: value for name, value in arguments.items() if name in others}
            invented = {name: str(uuid.uuid4()) for name in slots}
            outside = _get(
                client,
                tool,
                {**arguments, **{n: others[n] for n in slots}},
                ticket=ticket,
                project=project,
            )
            never = _get(client, tool, {**arguments, **invented}, ticket=ticket, project=project)
        assert outside[0] == never[0] == 404, (tool.name, outside, never)
        # Byte-identical but for each request's own reference.
        assert _without_request_id(outside[1]) == _without_request_id(never[1]), tool.name


async def test_path_tricks_are_refused_before_any_read_and_would_move_the_route(
    api_url: str,
    seen: list[str],
    install: AsyncEngine,
    tenants: list[ProvisionedTenant],
) -> None:
    """AC10, through real uvicorn: no trick in any argument of any tool reaches the server,
    and sent raw, one does reach another tool's handler, so the rule is what closes it."""
    own = await committed_tenant(install, tenants, "TricksA")
    ticket = await _ticket(install, own)
    client = CurlApiClient(api_url)
    now = datetime.now(UTC)
    window = {"from": (now - timedelta(hours=1)).isoformat(), "to": now.isoformat()}
    ids = {name: str(uuid.uuid4()) for name in ("finding_id", "incident_id", "release_id")}
    ids |= {"trace_id": TRACE, "group_id": "ab12"}
    tried = 0
    for tool in CATALOG:
        for name, rule in tool.arguments.items():
            if rule.kind in ("time", "closed", "severity", "search_text"):
                continue
            for trick in PATH_TRICKS:
                arguments = {**_arguments(tool, ids, window), name: trick}
                with pytest.raises(ArgumentsRefused):
                    check_arguments(tool, arguments, level="facts_and_text", now=now)
                tried += 1
    # The client refuses a trick handed to it directly, too, before any transfer.
    detection = TOOLS_BY_NAME["get_service_detection"]
    refused = client.get(
        detection,
        {"from": window["from"], "to": window["to"], "project_id": str(own.project.id)},
        ticket=ticket,
        path={"service_name": "x/../../findings"},
    )
    assert refused.failure == "not_allowed" and refused.status is None
    assert seen == [], seen
    assert tried >= 10 * len(PATH_TRICKS)

    # The danger is real: sent raw, an encoded slash in `{service_name}` reaches the
    # detection handler through the plain service route's slot.
    raw = (
        f"{api_url}/api/v1/services/x%2Fdetection?project_id={own.project.id}"
        f"&from={window['from'].replace('+', '%2B')}&to={window['to'].replace('+', '%2B')}"
    )
    answered = _raw_get(raw, ticket)
    assert '"signals"' in answered, answered[:300]


def _without_request_id(body: bytes) -> bytes:
    problem = json.loads(body)
    for key in ("instance", "correlation_id"):
        problem.pop(key, None)
    return json.dumps(problem).encode()


def _raw_get(url: str, ticket: str) -> str:
    from io import BytesIO

    body = BytesIO()
    curl = pycurl.Curl()
    try:
        curl.setopt(pycurl.URL, url)
        curl.setopt(pycurl.HTTPHEADER, [f"Authorization: Bearer {ticket}"])
        curl.setopt(pycurl.WRITEFUNCTION, body.write)
        curl.perform()
    finally:
        curl.close()
    return body.getvalue().decode()
