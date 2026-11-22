"""Tracker connections end to end, with the scheduler's real workers (F22 plan T8.5; AC36, AC50).

**What runs.** Two real worker processes, started as their containers start them:
`scheduler-connections` (`worker --queues connections,connections-interactive --concurrency 3
--tasks connections`, `SCHEDULER_ROLE=connections`, signed in as `nexovue_connections`), and a
`full` worker on `deliveries,detection`, as `scheduler-deliveries` and `scheduler-light` hold
them. GitHub is the simulated one (foundation item 28): the connections worker's children reach
it through `e2e_worker.py`, which changes nothing but GitHub's address. The API's half runs as the
routes run it: their service functions, on the install's account, in this process.

**AC36.** A full connect (setup, GitHub's return, the conversion job, the installation, the
refresh, the test), an issue created by a person's click and a disconnect. Then every log line
both workers wrote, every API log line, every job row seen while they ran, every result, every
timeline event, every audit row and what the connections route returns are searched for every
credential this run held: the App key, its webhook and client secrets, the manifest code, each
installation token and App sign-in GitHub was sent, the install's connection key and the
account's password.

**AC50's live half.** GitHub stops answering (each token mint hangs past the client's limit)
while the connections worker holds jobs; an alert delivery and a detection job, on the other
worker, each finish inside their soft limit. Sharing Postgres and beat is not proved by this.

**Not proved here:** real GitHub (the founder's, after Phase 2), and a browser.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
import structlog
from alert_fixtures import ASYNC_DSN, AlertTenant, add_finding, postgres_reachable
from connections_fixtures import CONNECTIONS_ASYNC_DSN
from connections_login import DEV_CONNECTIONS_PASSWORD
from live import require_stack
from nexovue_platform.alerts.tasks import DELIVER_TASK
from nexovue_platform.checks.sealing import ConfiguredKeys, generate_key
from nexovue_platform.jobs import ENQUEUE, JobRequest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tracker_fake import CLIENT_SECRET, WEBHOOK_SECRET, FakeGitHub, serve_fake_github

from nexovue_api.connections import service as connections_service
from nexovue_api.connections.schemas import Availability
from nexovue_api.data.postgres.models import User
from nexovue_api.findings import fix_takeback, links
from nexovue_scheduler.detection import DETECTION_QUEUE, JUDGE_PROJECT_TASK
from nexovue_scheduler.detection.project_job import (
    PROJECT_HARD_LIMIT_SECONDS,
    PROJECT_SOFT_LIMIT_SECONDS,
    judge_request_kwargs,
)

require_stack(
    reachable=postgres_reachable(),
    suite="the connections end-to-end suite",
    remedy="start the dev stack so Postgres answers (`just dev`)",
)

TESTS: Final = Path(__file__).resolve().parent
HELPERS: Final = TESTS.parents[2] / "tests" / "helpers"
# The generated envelope bindings the `full` role's preload imports, as pytest's path has them.
CONTRACTS: Final = TESTS.parents[2] / "packages" / "contracts" / "gen" / "python"
CLICKHOUSE_READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
CLICKHOUSE_DETECTION_WRITER_DSN: Final = CLICKHOUSE_READER_DSN.replace(
    "nexovue_reader:nexovue_reader_dev", "nexovue_detection_writer:nexovue_detection_writer_dev"
)
APP_BASE_URL: Final = "https://nexovue.acme.test"
INSTALLATION_ID: Final = 3_700_001
REPOSITORY_ID: Final = 4_700_001
REPOSITORY: Final = "acme/api"
SIGNAL: Final = {"signal": "slower", "current": 812.0, "normal": 203.0, "comparison": "x"}
# How long a worker has to do what a person waits on; a guess, generous for a shared VM.
SETTLES_SECONDS: Final = 60.0
POLL_SECONDS: Final = 0.2
# Longer than GitHub's client limit (`github.TIMEOUT_SECONDS`, 10), so every call times out.
HANG_SECONDS: Final = 25.0
# The deliveries queue's job (`alerts/tasks.py`): its own soft limit, read from the row.
DELIVERY_SOFT_LIMIT_FALLBACK: Final = 60


@dataclass
class Workers:
    """The two worker processes, and where each one's output went."""

    logs: list[Path]
    processes: list[subprocess.Popen[bytes]] = field(default_factory=list)

    def output(self) -> str:
        return "\n".join(path.read_text(errors="replace") for path in self.logs)

    def alive(self) -> bool:
        return all(process.poll() is None for process in self.processes)


@dataclass
class Seen:
    """Everything this run wrote that a credential must never be in."""

    job_rows: list[str] = field(default_factory=list)
    api_logs: list[MutableMapping[str, Any]] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start(arguments: list[str], environment: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    with log.open("wb") as written:
        return subprocess.Popen(
            [sys.executable, *arguments],
            cwd=TESTS,
            env={**_base_environment(), **environment},
            stdout=written,
            stderr=subprocess.STDOUT,
        )


def _base_environment() -> dict[str, str]:
    """This process's environment without the stores' variables, which each worker is given."""
    kept = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POSTGRES", "CLICKHOUSE", "NEXOVUE_", "KAFKA"))
    }
    return {**kept, "PYTHONPATH": os.pathsep.join([str(TESTS), str(HELPERS), str(CONTRACTS)])}


@contextmanager
def _workers(tmp_path: Path, github: FakeGitHub, key: str) -> Iterator[Workers]:
    connections_log, full_log = tmp_path / "connections.log", tmp_path / "full.log"
    common = {
        "ENV": "development",
        "LOG_FORMAT": "json",
        # Everything a worker says, so the search covers its most talkative level.
        "LOG_LEVEL": "debug",
        "APP_BASE_URL": APP_BASE_URL,
        "ALLOW_PRIVATE_PROBE_TARGETS": "true",
    }
    workers = Workers(logs=[connections_log, full_log])
    workers.processes.append(
        _start(
            [
                "e2e_worker.py",
                "worker",
                *("--queues", "connections,connections-interactive"),
                *("--concurrency", "3", "--tasks", "connections"),
            ],
            {
                **common,
                "SCHEDULER_ROLE": "connections",
                "POSTGRES_DSN": CONNECTIONS_ASYNC_DSN,
                "CONNECTION_SECRET_KEY": key,
                "HEALTH_ADDR": f"127.0.0.1:{_free_port()}",
                "E2E_GITHUB_API": github.base_url,
            },
            connections_log,
        )
    )
    workers.processes.append(
        _start(
            [
                *("-m", "nexovue_scheduler", "worker"),
                *("--queues", f"deliveries,{DETECTION_QUEUE}", "--concurrency", "2"),
            ],
            {
                **common,
                "SCHEDULER_ROLE": "full",
                "POSTGRES_DSN": ASYNC_DSN,
                # Required of the role, and never reached by a delivery or a detection job.
                "KAFKA_BROKERS": "127.0.0.1:9",
                "CLICKHOUSE_DSN": CLICKHOUSE_READER_DSN,
                "CLICKHOUSE_DETECTION_WRITER_DSN": CLICKHOUSE_DETECTION_WRITER_DSN,
                "EMAIL_PROVIDER": "console",
                "HEALTH_ADDR": f"127.0.0.1:{_free_port()}",
            },
            full_log,
        )
    )
    try:
        yield workers
    finally:
        for process in workers.processes:
            process.terminate()
        for process in workers.processes:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


assert fix_takeback.on_disconnect in connections_service.DISCONNECT_HOOKS


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    with serve_fake_github() as fake:
        fake.app_id = 2_700_000 + uuid.uuid4().int % 10**5
        yield fake


@pytest.fixture
async def sessions(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield async_sessionmaker(engine, expire_on_commit=False)


async def _until(
    check: Callable[[], Any], what: str, workers: Workers, seconds: float = SETTLES_SECONDS
) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = await check()
        if found:
            return found
        assert workers.alive(), f"a worker died while waiting for {what}:\n{workers.output()}"
        await asyncio.sleep(POLL_SECONDS)
    raise AssertionError(f"{what} did not happen in {seconds:.0f}s:\n{workers.output()[-4000:]}")


async def _one(engine: AsyncEngine, sql: str, **values: Any) -> Any:
    async with engine.connect() as connection:
        return (await connection.execute(text(sql), values)).first()


async def _watch_jobs(engine: AsyncEngine, org_id: uuid.UUID, seen: Seen) -> None:
    """Every job row of the tenant's, read while the workers run: a finished job is deleted."""
    while True:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT task, kwargs::text AS kwargs, last_error FROM jobs WHERE org_id = :org"
                ),
                {"org": org_id},
            )
            seen.job_rows.extend(json.dumps(list(row)) for row in rows)
        await asyncio.sleep(POLL_SECONDS)


def _availability() -> Availability:
    return Availability(deployment="self-hosted", enabled=True, app_base_url=APP_BASE_URL)


async def _route_view(
    sessions: async_sessionmaker[AsyncSession], tenant: AlertTenant, seen: Seen
) -> Any:
    """What `GET /connections` answers, as the route builds it."""
    async with sessions() as session:
        view = await connections_service.overview(
            session,
            org_id=tenant.org_id,
            support_view=False,
            availability=_availability(),
            email_available=True,
        )
    seen.routes.append(view.model_dump_json())
    return view


async def _owner(session: AsyncSession, tenant: AlertTenant) -> User:
    owner = await session.scalar(
        text("SELECT id FROM users WHERE org_id = :org").bindparams(org=tenant.org_id)
    )
    found = await session.get(User, owner)
    assert found is not None
    return found


async def _connect(
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    tenant: AlertTenant,
    github: FakeGitHub,
    keys: ConfiguredKeys,
    workers: Workers,
    seen: Seen,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Setup, GitHub's return with a code, the conversion, the installation, the refresh and
    the test, each through the job the route adds. Returns the connection, its repository and
    the manifest code GitHub handed back."""
    now = datetime.now(UTC)
    async with sessions() as session:
        owner = await _owner(session, tenant)
        started = await connections_service.start_github(
            session,
            org_id=tenant.org_id,
            actor_user_id=owner.id,
            availability=_availability(),
            reachable=False,
            github_organization=None,
            now=now,
        )
        await session.commit()
    code = github.add_code()
    async with sessions() as session:
        await connections_service.complete_github(
            session,
            org_id=tenant.org_id,
            actor_user_id=owner.id,
            state=started.state,
            code=code,
            keys=keys,
            availability=_availability(),
            now=datetime.now(UTC),
        )
        await session.commit()

    async def converted() -> bool:
        row = await _one(
            engine,
            "SELECT github_app_id FROM connections WHERE id = :id AND sealed_credential IS NOT "
            "NULL AND sealed_manifest_code IS NULL",
            id=started.connection_id,
        )
        return row is not None and row.github_app_id == github.app_id

    await _until(converted, "the manifest conversion", workers)

    github.install(INSTALLATION_ID)
    github.add_repository(REPOSITORY_ID, REPOSITORY)
    async with sessions() as session:
        await connections_service.refresh_github(
            session, org_id=tenant.org_id, availability=_availability()
        )
        await session.commit()

    async def refreshed() -> Any:
        return await _one(
            engine,
            "SELECT place.id FROM connection_repositories AS place JOIN connections AS c "
            "ON c.id = place.connection_id WHERE place.connection_id = :id "
            "AND place.key = :key AND c.github_installation_id = :installation",
            id=started.connection_id,
            key=REPOSITORY,
            installation=INSTALLATION_ID,
        )

    place = await _until(refreshed, "the repository refresh", workers)

    async with sessions() as session:
        accepted = await connections_service.dispatch_test(
            session,
            org_id=tenant.org_id,
            connection_id=started.connection_id,
            availability=_availability(),
        )
        await session.commit()

    async def tested() -> Any:
        async with sessions() as session:
            result = await connections_service.test_result(
                session, org_id=tenant.org_id, request_id=accepted.request_id
            )
        if result.ok is None:
            return None
        seen.routes.append(result.model_dump_json())
        return result

    result = await _until(tested, "the connection test", workers)
    assert result.ok is True
    await _route_view(sessions, tenant, seen)
    return started.connection_id, place.id, code


async def _create_issue(
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    tenant: AlertTenant,
    place_id: uuid.UUID,
    workers: Workers,
    github: FakeGitHub,
) -> uuid.UUID:
    async with engine.begin() as connection:
        finding = await add_finding(
            connection, tenant, severity="critical", started_at=datetime.now(UTC)
        )
        # What moved, as the project job stores it: the issue is written from it.
        await connection.execute(
            text("UPDATE findings SET signals = CAST(:signals AS jsonb) WHERE id = :id"),
            {"signals": json.dumps([SIGNAL]), "id": finding},
        )
    async with sessions() as session:
        owner = await _owner(session, tenant)
        _, link_id = await links.create(
            session,
            actor=owner,
            finding_id=finding,
            place_id=place_id,
            base_url=APP_BASE_URL,
            now=datetime.now(UTC),
        )

    async def synced() -> Any:
        return await _one(
            engine,
            "SELECT external_key FROM finding_links WHERE id = :id AND sync_status = 'in_sync'",
            id=link_id,
        )

    row = await _until(synced, "the issue's creation", workers)
    assert len(github.issues_in(REPOSITORY)) == 1
    assert row.external_key.startswith(f"{REPOSITORY}#")
    return finding


def _credentials(github: FakeGitHub, key: str, code: str, sealed: list[bytes]) -> dict[str, str]:
    """Every credential this run held, by name. A PEM is searched by a run from its middle,
    since a log escapes its line breaks."""
    pem_body = "".join(
        line for line in github.private_key_pem.splitlines() if not line.startswith("-----")
    )
    sent = {
        request.headers.get("Authorization", "").split(" ", 1)[-1]
        for request in github.calls()
        if request.headers.get("Authorization")
    }
    found = {
        "the App key": pem_body[100:160],
        "the webhook secret": WEBHOOK_SECRET,
        "the client secret": CLIENT_SECRET,
        "the manifest code": code,
        "the connection key": key,
        "the account's password": DEV_CONNECTIONS_PASSWORD,
    }
    found |= {f"installation token {n}": token for n, token in enumerate(github.tokens)}
    found |= {f"a credential sent to GitHub {n}": value for n, value in enumerate(sent) if value}
    for n, value in enumerate(sealed):
        # Stored as bytes; a leak would show it as text, base64 or hex.
        found |= {
            f"a sealed column {n} ({form})": written
            for form, written in (
                ("text", value.decode("ascii", errors="replace")),
                ("base64", base64.b64encode(value).decode()),
                ("hex", value.hex()),
            )
        }
    return found


async def _written(engine: AsyncEngine, tenant: AlertTenant) -> dict[str, str]:
    """What stays in Postgres for the tenant: results, timeline events and audit rows."""
    out: dict[str, str] = {}
    for name, sql in (
        ("job results", "SELECT payload::text FROM job_results WHERE org_id = :org"),
        ("timeline events", "SELECT detail::text FROM finding_events WHERE org_id = :org"),
        (
            "audit rows",
            "SELECT target || ' ' || coalesce(detail::text, '') FROM audit_log WHERE org_id = :org",
        ),
        (
            "jobs left",
            "SELECT kwargs::text || coalesce(last_error, '') FROM jobs WHERE org_id = :org",
        ),
    ):
        async with engine.connect() as connection:
            out[name] = "\n".join(
                str(value)
                for value in (await connection.execute(text(sql), {"org": tenant.org_id})).scalars()
            )
    return out


async def test_ac36_a_full_connect_create_and_disconnect_leaves_no_credential_anywhere(
    engine: AsyncEngine,
    connections_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    tenant: AlertTenant,
    github: FakeGitHub,
    tmp_path: Path,
) -> None:
    key = generate_key()
    keys = ConfiguredKeys(key)
    seen = Seen()
    watcher = asyncio.create_task(_watch_jobs(engine, tenant.org_id, seen))
    try:
        with (
            structlog.testing.capture_logs() as api_logs,
            _workers(tmp_path, github, key) as workers,
        ):
            connection_id, place_id, code = await _connect(
                engine, sessions, tenant, github, keys, workers, seen
            )
            await _create_issue(engine, sessions, tenant, place_id, workers, github)
            sealed_row = await _one(
                engine,
                "SELECT sealed_credential, sealed_webhook_secret FROM connections WHERE id = :id",
                id=connection_id,
            )
            async with sessions() as session:
                owner = await _owner(session, tenant)
                await connections_service.disconnect(
                    session,
                    org_id=tenant.org_id,
                    actor_user_id=owner.id,
                    connection_id=connection_id,
                    # As the route passes them: F5's takeback registers itself on import.
                    hooks=tuple(connections_service.DISCONNECT_HOOKS),
                )
                await session.commit()
            await _route_view(sessions, tenant, seen)
            # Long enough for a job the disconnect left to be claimed and to end.
            await asyncio.sleep(2)
            assert workers.alive(), workers.output()
            worker_logs = workers.output()
    finally:
        watcher.cancel()
    seen.api_logs.extend(api_logs)

    gone = await _one(
        engine,
        "SELECT state, sealed_credential, sealed_webhook_secret FROM connections WHERE id = :id",
        id=connection_id,
    )
    assert (gone.state, gone.sealed_credential, gone.sealed_webhook_secret) == (
        "disconnected",
        None,
        None,
    )
    # The workers really ran the jobs, and said so in their logs.
    assert '"queues": ["connections", "connections-interactive"]' in worker_logs
    assert "a GitHub App was created for a connection" in worker_logs
    assert seen.job_rows, "no job row was seen while the workers ran"
    places = {
        "worker logs": worker_logs,
        "API logs": json.dumps(seen.api_logs, default=str),
        "job rows while running": "\n".join(seen.job_rows),
        "route answers": "\n".join(seen.routes),
        **await _written(engine, tenant),
    }
    credentials = _credentials(
        github, key, code, [sealed_row.sealed_credential, sealed_row.sealed_webhook_secret]
    )
    assert len(credentials) >= 8, sorted(credentials)
    leaks = [
        f"{credential} in {place}"
        for credential, value in credentials.items()
        for place, written in places.items()
        if value in written
    ]
    assert leaks == []


async def test_ac50_a_hanging_tracker_holds_up_no_delivery_or_detection_job(
    engine: AsyncEngine,
    connections_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    tenant: AlertTenant,
    github: FakeGitHub,
    tmp_path: Path,
) -> None:
    key = generate_key()
    seen = Seen()
    with _workers(tmp_path, github, key) as workers:
        connection_id, _, _ = await _connect(
            engine, sessions, tenant, github, ConfiguredKeys(key), workers, seen
        )
        # GitHub stops answering: every run's first call, minting a token, outlasts the client.
        github.fail("access_token", 201, delay=HANG_SECONDS)
        async with sessions() as session:
            for _ in range(3):
                await connections_service.dispatch_test(
                    session,
                    org_id=tenant.org_id,
                    connection_id=connection_id,
                    availability=_availability(),
                )
                await connections_service.refresh_github(
                    session, org_id=tenant.org_id, availability=_availability()
                )
            await session.commit()

        async def held() -> bool:
            row = await _one(
                engine,
                "SELECT count(*) AS n FROM jobs WHERE org_id = :org AND leased_by IS NOT NULL "
                "AND queue LIKE 'connections%'",
                org=tenant.org_id,
            )
            return bool(row.n)

        await _until(held, "the connections worker holding a hanging job", workers)

        settled = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=3)
        async with engine.begin() as connection:
            finding = await add_finding(
                connection, tenant, severity="critical", started_at=datetime.now(UTC)
            )
            await connection.execute(
                text(
                    "INSERT INTO notifications (org_id, project_id, finding_id, channel_id, kind, "
                    "severity, state, deliver_after) VALUES (:org, :project, :finding, :channel, "
                    "'opened', 'critical', 'pending', now())"
                ),
                {
                    "org": tenant.org_id,
                    "project": tenant.project_id,
                    "finding": finding,
                    "channel": tenant.owners_channel_id,
                },
            )
            asked = time.monotonic()
            for request in (
                JobRequest(task=DELIVER_TASK, queue="deliveries", org_id=str(tenant.org_id)),
                JobRequest(
                    task=JUDGE_PROJECT_TASK,
                    queue=DETECTION_QUEUE,
                    kwargs=judge_request_kwargs(tenant.project_id, settled),
                    org_id=str(tenant.org_id),
                    soft_limit_seconds=PROJECT_SOFT_LIMIT_SECONDS,
                    hard_limit_seconds=PROJECT_HARD_LIMIT_SECONDS,
                ),
            ):
                await connection.execute(text(ENQUEUE), request.parameters())
        delivery_limit = JobRequest(task=DELIVER_TASK, queue="deliveries").soft_limit_seconds

        async def delivered() -> Any:
            return await _one(
                engine,
                "SELECT delivered_at FROM notifications WHERE finding_id = :finding "
                "AND state = 'delivered'",
                finding=finding,
            )

        await _until(delivered, "the alert delivery", workers, seconds=delivery_limit)
        delivery_seconds = time.monotonic() - asked

        async def judged() -> bool:
            row = await _one(
                engine,
                "SELECT count(*) AS n FROM jobs WHERE task = :task AND org_id = :org",
                task=JUDGE_PROJECT_TASK,
                org=tenant.org_id,
            )
            return bool(row.n == 0)

        await _until(judged, "the detection job", workers, seconds=PROJECT_SOFT_LIMIT_SECONDS)
        detection_seconds = time.monotonic() - asked
        still_hanging = await held()
        output = workers.output()

    assert delivery_seconds < delivery_limit
    assert detection_seconds < PROJECT_SOFT_LIMIT_SECONDS
    # The tracker was still hanging when both had finished: they did not wait it out.
    assert still_hanging
    assert detection_seconds < HANG_SECONDS
    assert JUDGE_PROJECT_TASK.rsplit(".", 1)[-1] in output or "detection" in output
