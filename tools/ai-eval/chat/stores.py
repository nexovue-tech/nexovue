"""The chat bed's stores: the seed written, the API served, a trial's rows read back
(F4 plan T4.1).

**Real stores, the real API.** Each trial gets its own world: two organizations with AI and
chat on, consented, as `/settings/ai` leaves them, and the seed of `seed.py` written at the
trial's anchor. The rows are committed, since the API (uvicorn, in this process) and the
loop (the AI account) use their own connections. Every organization made is deleted at the
end, and its ClickHouse rows with it.

**Run against stores of the bed's own,** with no scheduler or worker running: the detection
job would rewrite the seeded detection states, and a worker would take the questions. Each
question's job is deleted before its transaction commits all the same (`_hold_from_workers`),
as the live suites do.

No real key is stored here: a real option's key is given to the provider by the bed, never
sealed into a row.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import threading
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform.ai.connection import consent_fingerprint
from nexovue_platform.assistant_records import ANSWER_QUESTION_TASK, CHAT_DISCLOSURE_VERSION
from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.evidence.model_facts import SCHEMA_VERSION
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from chat import seed
from chat.seed import MINUTE, PROJECT_ZONE

FAKE_BASE_URL: Final = "https://provider.example/v1"
FAKE_MODEL: Final = "fake"
# Generous for a fake; a real option sets its own (`--call-seconds`).
DEFAULT_CALL_SECONDS: Final = 120
# The month a world may spend: far above any trial's, so the limit never ends one.
MONTHLY_TOKENS: Final = 100_000_000
DAILY_QUESTIONS: Final = 1_000
# ClickHouse tables a world writes; the run's end deletes each world's rows from them.
WRITTEN_TABLES: Final = (
    "endpoint_stats_1m",
    "service_edges_1m",
    "logs",
    "spans",
    "span_trace_lookup",
    "trace_summaries",
    "metrics_gauge",
    "metrics_sum",
)
_QUANTILES: Final = "quantilesState(0.5, 0.95, 0.99)"
# The API is given 20 seconds to start, polled every 50 ms.
STARTUP_POLLS: Final = 400
STARTUP_POLL_SECONDS: Final = 0.05


@dataclass(frozen=True, slots=True)
class Stores:
    """Where the bed's stores are, from the environment the live suites read."""

    install_dsn: str
    insight_password: str
    clickhouse_dsn: str
    reader_dsn: str

    @classmethod
    def from_environment(cls) -> Stores:
        plain = os.environ.get(
            "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
        )
        return cls(
            install_dsn=plain.replace("postgresql://", "postgresql+asyncpg://", 1),
            insight_password=os.environ.get("POSTGRES_INSIGHT_PASSWORD", "nexovue_insight_dev"),
            clickhouse_dsn=os.environ.get(
                "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
            ),
            reader_dsn=os.environ.get(
                "NEXOVUE_CLICKHOUSE_READER_DSN",
                "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
            ),
        )

    @property
    def insight_dsn(self) -> str:
        parts = urllib.parse.urlsplit(self.install_dsn)
        netloc = f"nexovue_insight:{self.insight_password}@{parts.hostname}:{parts.port or 5432}"
        return urllib.parse.urlunsplit(parts._replace(netloc=netloc))

    def reachable(self) -> bool:
        for dsn in (self.install_dsn, self.clickhouse_dsn):
            parts = urllib.parse.urlsplit(dsn)
            try:
                with socket.create_connection((parts.hostname or "localhost", parts.port or 0), 2):
                    pass
            except OSError:
                return False
        return True


@dataclass(frozen=True, slots=True)
class Connection:
    """The AI connection a world's organization holds. A fake's address by default; a real
    option's address and model, never its key."""

    wire_format: str = "openai_chat"
    base_url: str = FAKE_BASE_URL
    model: str = FAKE_MODEL
    call_seconds: int = DEFAULT_CALL_SECONDS


@dataclass(frozen=True, slots=True)
class Tenant:
    org_id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    name: str


@dataclass(frozen=True, slots=True)
class World:
    anchor: datetime
    main: Tenant
    empty_project_id: uuid.UUID
    other: Tenant
    finding_ids: Mapping[str, uuid.UUID] = field(default_factory=dict)

    @property
    def organizations(self) -> tuple[uuid.UUID, uuid.UUID]:
        return self.main.org_id, self.other.org_id

    def project_for(self, project: str) -> uuid.UUID:
        return self.empty_project_id if project == "empty" else self.main.project_id


def anchor_now() -> datetime:
    return datetime.now(UTC).replace(second=0, microsecond=0)


# --------------------------------------------------------------------------- Postgres


async def provision(stores: Stores) -> None:
    """The AI account made and granted, as `setup` does."""
    from nexovue_api import insight_account

    os.environ.setdefault(insight_account.PASSWORD_VARIABLE, stores.insight_password)
    said: list[str] = []
    if not await insight_account.provision(stores.install_dsn, say=said.append):
        raise RuntimeError("the AI account could not be provisioned: " + "; ".join(said))


async def _tenant(db: AsyncSession, name: str, connection: Connection, anchor: datetime) -> Tenant:
    from nexovue_api.data.postgres.models import AIConnection, AssistantSettings, UsageDaily
    from nexovue_api.signup.service import provision_tenant

    made = await provision_tenant(
        db,
        organization_name=name,
        email=f"bed-{uuid.uuid4().hex[:12]}@example.test",
        password=None,
        timezone=PROJECT_ZONE,
    )
    org_id, owner = made.organization.id, made.user.id
    fingerprint = consent_fingerprint(
        wire_format=connection.wire_format, base_url=connection.base_url, region=None, key_id=None
    )
    db.add(
        AIConnection(
            org_id=org_id,
            wire_format=connection.wire_format,
            base_url=connection.base_url,
            model=connection.model,
            call_seconds=connection.call_seconds,
            monthly_token_limit=MONTHLY_TOKENS,
            enabled=True,
            consented_by=owner,
            consented_at=anchor,
            consented_base_url=connection.base_url,
            consented_fingerprint=fingerprint,
            consented_schema=SCHEMA_VERSION,
        )
    )
    db.add(
        AssistantSettings(
            org_id=org_id,
            enabled=True,
            daily_question_limit=DAILY_QUESTIONS,
            data_level="facts_and_text",
            mask_emails=True,
            consented_by=owner,
            consented_at=anchor,
            consented_fingerprint=fingerprint,
            consented_disclosure_version=CHAT_DISCLOSURE_VERSION,
            consented_data_level="facts_and_text",
        )
    )
    db.add(UsageDaily(org_id=org_id, project_id=made.project.id, day=anchor.date()))
    await db.flush()
    return Tenant(org_id, owner, made.project.id, made.organization.name)


async def seed_world(
    install: AsyncEngine, stores: Stores, connection: Connection, anchor: datetime
) -> World:
    """Two organizations with chat on, the seed written to both stores at `anchor`."""
    from nexovue_api.data.postgres.models import (
        DetectionState,
        Finding,
        Project,
        Release,
        ReleasePeriod,
        UsageDaily,
    )

    suffix = uuid.uuid4().hex[:8]
    async with AsyncSession(install, expire_on_commit=False) as db, db.begin():
        main = await _tenant(db, f"Bed A {suffix}", connection, anchor)
        other = await _tenant(db, f"Bed B {suffix}", connection, anchor)
        empty = Project(org_id=main.org_id, slug="empty", name="empty", timezone=PROJECT_ZONE)
        db.add(empty)
        await db.flush()
        # Reported once, two days ago; its telemetry is gone.
        db.add(
            UsageDaily(
                org_id=main.org_id, project_id=empty.id, day=(anchor - 2 * 24 * 60 * MINUTE).date()
            )
        )
        starts = [anchor - row.offset * MINUTE for row in seed.RELEASES]
        for position, row in enumerate(seed.RELEASES):
            started = starts[position]
            ended = starts[position + 1] if position + 1 < len(starts) else None
            release = Release(
                org_id=main.org_id,
                project_id=main.project_id,
                service_name=row.service,
                version=row.version,
                first_seen_at=started,
                last_seen_at=ended or anchor,
            )
            db.add(release)
            await db.flush()
            db.add(
                ReleasePeriod(
                    org_id=main.org_id,
                    release_id=release.id,
                    project_id=main.project_id,
                    service_name=row.service,
                    started_at=started,
                    start_basis="seen",
                    ended_at=ended,
                )
            )
        findings: dict[str, uuid.UUID] = {}
        for found in seed.FINDINGS:
            finding = Finding(
                org_id=main.org_id,
                project_id=main.project_id,
                scope="service",
                service_name=found.service,
                severity=found.severity,
                state="open" if found.closed is None else "closed",
                closed_reason=found.closed_reason,
                title=found.title,
                signals=[],
                started_at=anchor - found.started * MINUTE,
                closed_at=None if found.closed is None else anchor - found.closed * MINUTE,
            )
            db.add(finding)
            await db.flush()
            findings[found.service] = finding.id
        for service in seed.DETECTED:
            db.add(
                DetectionState(
                    org_id=main.org_id,
                    project_id=main.project_id,
                    service_name=service,
                    last_judged_minute=anchor - 2 * MINUTE,
                    signals=seed.detection_signals(service, anchor),
                )
            )
    world = World(anchor, main, empty.id, other, findings)
    write_telemetry(stores, world)
    return world


async def remove(install: AsyncEngine, stores: Stores, worlds: Sequence[World]) -> None:
    """Every organization the run made, and their telemetry."""
    organizations = [str(org) for world in worlds for org in world.organizations]
    if not organizations:
        return
    async with install.begin() as db:
        for org in organizations:
            await db.execute(text("DELETE FROM jobs WHERE kwargs->>'org_id' = :o"), {"o": org})
            await db.execute(
                text("DELETE FROM organizations WHERE id = CAST(:o AS uuid)"), {"o": org}
            )
    listed = ", ".join(f"'{org}'" for org in organizations if _is_uuid(org))
    for table in WRITTEN_TABLES:
        _clickhouse(stores, f"ALTER TABLE {table} DELETE WHERE tenant_id IN ({listed})")


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


async def hold_from_workers(db: AsyncSession, message_id: uuid.UUID) -> None:
    """A question's job removed before its transaction commits: the bed is the worker."""
    removed = await db.scalars(
        text(
            "DELETE FROM jobs WHERE task = :task AND kwargs->>'message_id' = :message_id "
            "RETURNING id"
        ),
        {"task": ANSWER_QUESTION_TASK, "message_id": str(message_id)},
    )
    if len(removed.all()) != 1:
        raise RuntimeError("the question's job was not found")


async def counts(install: AsyncEngine, stores: Stores, world: World) -> dict[str, int]:
    """Rows a model could only add by writing: what a trial must leave as it found it."""
    organizations = list(world.organizations)
    found: dict[str, int] = {}
    async with install.connect() as db:
        for table in (
            "findings",
            "finding_notes",
            "runbooks",
            "knowledge_chunks",
            "releases",
            "projects",
            "api_tokens",
            "ai_connections",
            "assistant_settings",
            "users",
        ):
            found[table] = int(
                await db.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE org_id = ANY(:organizations)"),
                    {"organizations": organizations},
                )
                or 0
            )
    tenants = ", ".join(f"'{org}'" for org in organizations)
    for table in WRITTEN_TABLES:
        reply = _clickhouse(stores, f"SELECT count() FROM {table} WHERE tenant_id IN ({tenants})")
        found[f"clickhouse.{table}"] = int(reply.strip() or 0)
    return found


async def read_trial(install: AsyncEngine, message_id: uuid.UUID) -> dict[str, Any]:
    """The question as it ended: its row, its steps, its metered calls."""
    async with install.connect() as db:
        message = (
            await db.execute(
                text(
                    "SELECT state, answer, failure_reason, rejection_reason, abstain_reason, "
                    "used_tokens, created_at, finished_at FROM assistant_messages WHERE id = :id"
                ),
                {"id": message_id},
            )
        ).one()
        steps = (
            await db.execute(
                text(
                    "SELECT position, tool, arguments, from_time, to_time, state, row_count, "
                    "was_cut, failure, started_at, finished_at FROM assistant_steps "
                    "WHERE message_id = :id ORDER BY position"
                ),
                {"id": message_id},
            )
        ).all()
    return {
        "state": message.state,
        "answer": message.answer,
        "failure": message.failure_reason,
        "rejection": message.rejection_reason,
        "abstain": message.abstain_reason,
        "used_tokens": message.used_tokens,
        "steps": [
            {
                "position": step.position,
                "tool": step.tool,
                "arguments": step.arguments,
                "from_time": _iso(step.from_time),
                "to_time": _iso(step.to_time),
                "state": step.state,
                "row_count": step.row_count,
                "was_cut": step.was_cut,
                "failure": step.failure,
                "started_at": _iso(step.started_at),
            }
            for step in steps
        ],
    }


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.astimezone(UTC).isoformat()


# --------------------------------------------------------------------------- ClickHouse


def _clickhouse(stores: Stores, query: str, body: bytes = b"") -> str:
    """One statement over ClickHouse's HTTP interface, as the install's writer."""
    parts = urllib.parse.urlsplit(stores.clickhouse_dsn)
    params = urllib.parse.urlencode(
        {
            "database": (parts.path or "/").lstrip("/") or "default",
            "query": query,
            "date_time_input_format": "best_effort",
            "input_format_skip_unknown_fields": "0",
        }
    )
    request = urllib.request.Request(
        f"http://{parts.hostname}:{parts.port or 8123}/?{params}",
        data=body,
        method="POST",
    )
    token = base64.b64encode(f"{parts.username}:{parts.password}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=60) as reply:
        return str(reply.read().decode())


def _rows(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return "\n".join(json.dumps(row, default=_moment) for row in rows).encode()


def _moment(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    raise TypeError(type(value))


def _insert(stores: Stores, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
    if rows:
        _clickhouse(stores, f"INSERT INTO {table} FORMAT JSONEachRow", _rows(rows))


def write_telemetry(stores: Stores, world: World) -> None:
    """The seed's ClickHouse rows for both organizations, at the world's anchor."""
    anchor = world.anchor
    tenants = {
        "main": (str(world.main.org_id), str(world.main.project_id)),
        "other": (str(world.other.org_id), str(world.other.project_id)),
    }
    endpoint_rows = [
        {
            "tenant_id": tenants[row.project][0],
            "project_id": tenants[row.project][1],
            "service_name": row.service,
            "span_kind": row.span_kind,
            "span_name": row.span_name,
            "minute": row.minute,
            "request_count": row.requests,
            "error_count": row.errors,
            "durations": list(row.durations),
        }
        for row in seed.endpoint_minutes(anchor)
    ]
    # The latency state is folded by the server from each minute's durations, as the
    # pipeline's own insert does.
    _clickhouse(
        stores,
        "INSERT INTO endpoint_stats_1m (tenant_id, project_id, service_name, span_kind, "
        "span_name, minute, request_count, error_count, latency_quantiles) "
        "SELECT tenant_id, project_id, service_name, span_kind, span_name, minute, "
        f"any(request_count), any(error_count), {_QUANTILES}(d) "
        "FROM input('tenant_id String, project_id String, service_name String, "
        "span_kind String, span_name String, minute DateTime, request_count UInt64, "
        "error_count UInt64, durations Array(Float64)') ARRAY JOIN durations AS d "
        "GROUP BY tenant_id, project_id, service_name, span_kind, span_name, minute "
        "FORMAT JSONEachRow",
        _rows(endpoint_rows),
    )
    org, project = tenants["main"]
    edge_rows = [
        {
            "tenant_id": org,
            "project_id": project,
            "client_service": row.client,
            "server_service": row.server,
            "edge_kind": row.edge_kind,
            "server_peer_kind": row.peer_kind,
            "minute": row.minute,
            "call_count": row.calls,
            "error_count": row.errors,
            "durations": [150.0],
        }
        for row in seed.edge_minutes(anchor)
    ]
    _clickhouse(
        stores,
        "INSERT INTO service_edges_1m (tenant_id, project_id, client_service, server_service, "
        "edge_kind, server_peer_kind, minute, call_count, error_count, latency_quantiles) "
        "SELECT tenant_id, project_id, client_service, server_service, edge_kind, "
        f"server_peer_kind, minute, any(call_count), any(error_count), {_QUANTILES}(d) "
        "FROM input('tenant_id String, project_id String, client_service String, "
        "server_service String, edge_kind String, server_peer_kind String, minute DateTime, "
        "call_count UInt64, error_count UInt64, durations Array(Float64)') "
        "ARRAY JOIN durations AS d GROUP BY tenant_id, project_id, client_service, "
        "server_service, edge_kind, server_peer_kind, minute FORMAT JSONEachRow",
        _rows(edge_rows),
    )
    _insert(stores, "logs", [_log(org, project, line) for line in seed.log_lines(anchor)])
    write_trace(stores, org, project, anchor)
    gauges, sums = [], []
    for point in seed.metric_points(anchor):
        row = {
            "tenant_id": org,
            "project_id": project,
            "service_name": point.service,
            "metric_name": point.name,
            "metric_unit": point.unit,
            "timestamp": point.at,
            "start_timestamp": point.at - timedelta(hours=1),
            "received_at": point.at,
            "value": point.value,
            "resource_attrs": {"host.name": point.host} if point.host else {},
            "attrs": {"state": point.state, "mountpoint": "/", "device": "/dev/sda1"}
            if point.state
            else {},
        }
        if point.table == "gauge":
            gauges.append(row)
        else:
            sums.append(
                {
                    **row,
                    "aggregation_temporality": "AGGREGATION_TEMPORALITY_CUMULATIVE",
                    "is_monotonic": False,
                }
            )
    _insert(stores, "metrics_gauge", gauges)
    _insert(stores, "metrics_sum", sums)


def _log(org: str, project: str, line: seed.LogLine, **extra: Any) -> dict[str, Any]:
    return {
        "tenant_id": org,
        "project_id": project,
        "timestamp": line.at,
        "received_at": line.at,
        "service_name": line.service,
        "severity": line.severity,
        "severity_number": line.severity_number,
        "body": line.body,
        "exception_type": line.exception_type,
        "exception_message": line.exception_message,
        **extra,
    }


def write_log(
    stores: Stores, world: World, line: seed.LogLine, attributes: Mapping[str, str]
) -> None:
    """One more log line in the main project: where a planted payload is put."""
    _insert(
        stores,
        "logs",
        [
            _log(
                str(world.main.org_id), str(world.main.project_id), line, log_attrs=dict(attributes)
            )
        ],
    )


def write_endpoint(stores: Stores, world: World, service: str, at: datetime) -> None:
    """One minute of one more service in the main project: a planted service name."""
    _clickhouse(
        stores,
        "INSERT INTO endpoint_stats_1m (tenant_id, project_id, service_name, span_kind, "
        "span_name, minute, request_count, error_count, latency_quantiles) "
        "SELECT tenant_id, project_id, service_name, 'SPAN_KIND_SERVER', 'GET /', "
        f"toStartOfMinute(minute), 1, 0, {_QUANTILES}(toFloat64(50)) "
        "FROM input('tenant_id String, project_id String, service_name String, "
        "minute DateTime') GROUP BY tenant_id, project_id, service_name, minute "
        "FORMAT JSONEachRow",
        _rows(
            [
                {
                    "tenant_id": str(world.main.org_id),
                    "project_id": str(world.main.project_id),
                    "service_name": service,
                    "minute": at,
                }
            ]
        ),
    )


def write_trace(stores: Stores, org: str, project: str, anchor: datetime) -> None:
    spans = seed.trace_spans(anchor)
    _insert(
        stores,
        "spans",
        [
            {
                "tenant_id": org,
                "project_id": project,
                "trace_id": seed.TRACE_ID,
                "span_id": span.span_id,
                "parent_span_id": span.parent,
                "service_name": span.service,
                "span_name": span.span_name,
                "span_kind": "SPAN_KIND_SERVER",
                "timestamp": span.at,
                "received_at": span.at,
                "duration_ms": span.duration_ms,
                "status_code": "STATUS_CODE_UNSET",
            }
            for span in spans
        ],
    )
    root = spans[0]
    _insert(
        stores,
        "span_trace_lookup",
        [
            {
                "tenant_id": org,
                "trace_id": seed.TRACE_ID,
                "span_start": root.at,
                "span_end": root.at + timedelta(milliseconds=root.duration_ms),
                "received_at": root.at,
            }
        ],
    )
    _insert(
        stores,
        "trace_summaries",
        [
            {
                "tenant_id": org,
                "project_id": project,
                "start_time": root.at,
                "trace_start": root.at,
                "trace_id": seed.TRACE_ID,
                "received_at": root.at,
                "duration_ms": root.duration_ms,
                "span_count": len(spans),
                "error_count": 0,
                "worst_severity": 0,
                "root_service": root.service,
                "root_name": root.span_name,
                "span_services": sorted({span.service for span in spans}),
                "span_endpoints": sorted({span.span_name for span in spans}),
            }
        ],
    )


# --------------------------------------------------------------------------- the API


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def serve(stores: Stores, keys: ConfiguredKeys) -> Iterator[str]:
    """The real application under uvicorn on its own thread, reading the bed's stores with
    the API's own read account, holding the bed's own sealing key."""
    import uvicorn

    wanted = {
        "POSTGRES_DSN": stores.install_dsn,
        "CLICKHOUSE_DSN": stores.reader_dsn,
        "AI_SECRET_KEY": keys.current,
        "NEXOVUE_DEPLOYMENT": "self-hosted",
        "SCHEDULER_INSIGHT_REPLICAS": "1",
        "ENV": os.environ.get("ENV", "development"),
        "LOG_LEVEL": os.environ.get("LOG_LEVEL", "warn"),
    }
    # Put back afterwards: a test process goes on to run other suites.
    before = {name: os.environ.get(name) for name in wanted}
    os.environ.update(wanted)
    try:
        from nexovue_api.main import create_app
        from nexovue_api.settings import Settings

        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(create_app(Settings()), host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            waited = threading.Event()
            for _ in range(STARTUP_POLLS):
                if server.started:
                    break
                waited.wait(STARTUP_POLL_SECONDS)
            if not server.started:
                raise RuntimeError("the API did not start")
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
