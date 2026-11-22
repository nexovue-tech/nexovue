"""A full case with AI off, through the routes and the scheduler's real worker (F22 plan T8.3;
AC56).

**What runs.** The API's routes, in this process, as a member and a colleague; and a real
`scheduler-light` worker process (`worker --queues light`), as its container starts it, with no
AI setting in its environment. A case is taken, noted, survives its signal's recovery, is handed
over, resolved and reopened, and is then ended by the real worker's cases sweep after 90 idle
days (BR13). Every row it leaves is real and committed.

**AI off, asserted, not assumed.** The API's settings and the worker's environment hold no AI
key; no organization row says AI is on; no session is signed in as the AI account while the case
runs, which is what a running `scheduler-insight` would be; and no job reaches an AI queue. CI's
`ai-off` job runs this suite on a Postgres alone, with no AI account made, and refuses the run if
an AI setting or a `scheduler-insight` container is present.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from live import require_stack
from nexovue_platform.assistant_records import ASSISTANT_QUEUE
from nexovue_platform.evidence import INSIGHT_QUEUE
from nexovue_platform.fixes.records import FIX_QUEUE
from nexovue_platform.jobs import ENQUEUE, JobRequest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexovue_api.auth.dependencies import (
    Identity,
    Reader,
    get_current_identity,
    get_current_reader,
    get_tenant_repository,
)
from nexovue_api.data.postgres.models import User
from nexovue_api.deps import get_app_settings, get_db_session, get_optional_email_sender
from nexovue_api.settings import API_PREFIX, Settings
from nexovue_api.signup.service import provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
REPOSITORY: Final = Path(__file__).resolve().parents[3]
CONTRACTS: Final = REPOSITORY / "packages" / "contracts" / "gen" / "python"
SERVICE: Final = "checkout"
AI_QUEUES: Final = (INSIGHT_QUEUE, ASSISTANT_QUEUE, FIX_QUEUE)
# Every variable an AI role or the API's AI half reads (`deploy/compose/nexovue.yaml`).
AI_VARIABLES: Final = (
    "AI_SECRET_KEY",
    "AI_SECRET_PREVIOUS_KEYS",
    "AI_PRIVATE_ENDPOINTS",
    "POSTGRES_INSIGHT_PASSWORD",
    "DEV_AI_SECRET_KEY",
)
INSIGHT_ACCOUNT: Final = "nexovue_insight"
# The sweep's own job, as beat adds it (`beat.py`); a guess at a shared VM's patience.
CASES_SWEEP_TASK: Final = "nexovue_scheduler.cases.sweep"
SETTLES_SECONDS: Final = 60.0
POLL_SECONDS: Final = 0.2


def _reachable() -> bool:
    parts = urlsplit(POSTGRES_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="the AI-off case suite",
    remedy="start Postgres (`just dev`, or CI's `ai-off` job)",
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _worker_environment() -> dict[str, str]:
    """`scheduler-light`'s, with no AI setting: this process's own, less every store and AI
    variable, plus what the role needs."""
    kept = {
        key: value
        for key, value in os.environ.items()
        if key not in AI_VARIABLES and not key.startswith(("POSTGRES", "AI_", "NEXOVUE_"))
    }
    return {
        **kept,
        "PYTHONPATH": str(CONTRACTS),
        "ENV": "development",
        "LOG_FORMAT": "json",
        "SCHEDULER_ROLE": "full",
        "POSTGRES_DSN": ASYNC_DSN,
        # Required of the role, and never reached by the cases sweep.
        "KAFKA_BROKERS": "127.0.0.1:9",
        "EMAIL_PROVIDER": "console",
        "APP_BASE_URL": "https://nexovue.acme.test",
        "HEALTH_ADDR": f"127.0.0.1:{_free_port()}",
    }


@contextmanager
def _light_worker(log: Path) -> Iterator[subprocess.Popen[bytes]]:
    environment = _worker_environment()
    assert not set(AI_VARIABLES) & set(environment)
    with log.open("wb") as written:
        worker = subprocess.Popen(
            [sys.executable, "-m", "nexovue_scheduler", "worker", "--queues", "light"],
            env=environment,
            stdout=written,
            stderr=subprocess.STDOUT,
        )
    try:
        yield worker
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=30)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()


class NoTelemetry:
    """The case's routes read no telemetry; Postgres is all this suite starts."""

    async def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[Any]:
        return []


class Mail:
    """What the API's case emails carried."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, *, to: str, message: Any, data: dict[str, Any], **_: Any) -> None:
        self.sent.append({"to": to, "data": data})


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    built = create_async_engine(ASYNC_DSN)
    yield built
    await built.dispose()


@pytest.fixture
async def team(engine: AsyncEngine) -> AsyncIterator[dict[str, Any]]:
    """An organization, its owner and two members, committed, and removed afterwards."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        tenant = await provision_tenant(
            session,
            organization_name="AI off",
            email=f"owner-{uuid.uuid4()}@example.test",
            password="correct horse battery staple",
        )
        people = {}
        for label in ("member", "colleague"):
            people[label] = User(
                org_id=tenant.organization.id,
                email=f"{label}-{uuid.uuid4()}@example.test",
                name=label.capitalize(),
                role="member",
                password_hash="x",
            )
            session.add(people[label])
        await session.commit()
    made = {"tenant": tenant, **people}
    yield made
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM organizations WHERE id = :org"), {"org": tenant.organization.id}
        )


def _client(
    engine: AsyncEngine, team: dict[str, Any], person: User, mail: Mail
) -> httpx.AsyncClient:
    from nexovue_api.main import create_app

    settings = Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]
    assert settings.ai_secret_key == ""
    app = create_app(settings)
    organization = team["tenant"].organization
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def session_per_request() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    identity = Identity(user=person, organization=organization, impersonation=None)
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = session_per_request
    app.dependency_overrides[get_current_reader] = lambda: Reader(
        org_id=organization.id, identity=identity
    )
    app.dependency_overrides[get_current_identity] = lambda: identity
    app.dependency_overrides[get_optional_email_sender] = lambda: mail
    app.dependency_overrides[get_tenant_repository] = NoTelemetry
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _call(
    engine: AsyncEngine,
    team: dict[str, Any],
    person: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with _client(engine, team, team[person], Mail()) as client:
        answer = await client.request(method, f"{API_PREFIX}{path}", json=body)
    assert answer.status_code < 300, (path, answer.status_code, answer.text)
    return dict(answer.json()) if answer.content else {}


async def _one(engine: AsyncEngine, sql: str, **values: Any) -> Any:
    async with engine.connect() as connection:
        return (await connection.execute(text(sql), values)).first()


async def _watch(engine: AsyncEngine, org_id: uuid.UUID, seen: dict[str, int]) -> None:
    """While the case runs: jobs on an AI queue, and sessions signed in as the AI account."""
    while True:
        row = await _one(
            engine,
            "SELECT (SELECT count(*) FROM jobs WHERE org_id = :org AND queue = ANY(:queues)) "
            "AS ai_jobs, (SELECT count(*) FROM pg_stat_activity WHERE usename = :account) "
            "AS ai_sessions",
            org=org_id,
            queues=list(AI_QUEUES),
            account=INSIGHT_ACCOUNT,
        )
        seen["ai_jobs"] = max(seen["ai_jobs"], row.ai_jobs)
        seen["ai_sessions"] = max(seen["ai_sessions"], row.ai_sessions)
        await asyncio.sleep(POLL_SECONDS)


async def test_ac56_a_full_case_runs_with_no_ai_configured_and_no_ai_role(
    engine: AsyncEngine, team: dict[str, Any], tmp_path: Path
) -> None:
    org_id = team["tenant"].organization.id
    async with engine.begin() as connection:
        finding = (
            await connection.execute(
                text(
                    "INSERT INTO findings (org_id, project_id, scope, service_name, severity, "
                    "state, title, signals, started_at) VALUES (:org, :project, 'service', "
                    ":service, 'critical', 'open', 'checkout is slower than normal', "
                    "CAST(:signals AS jsonb), now()) RETURNING id"
                ),
                {
                    "org": org_id,
                    "project": team["tenant"].project.id,
                    "service": SERVICE,
                    "signals": "[]",
                },
            )
        ).scalar_one()
    seen = {"ai_jobs": 0, "ai_sessions": 0}
    watcher = asyncio.create_task(_watch(engine, org_id, seen))
    try:
        with _light_worker(tmp_path / "light.log") as worker:
            path = f"/findings/{finding}"
            taken = await _call(
                engine, team, "member", "PATCH", f"{path}/case", {"case_state": "investigating"}
            )
            assert taken["finding"]["case_state"] == "investigating"
            await _call(
                engine,
                team,
                "member",
                "POST",
                f"{path}/notes",
                {"body": "Restarted the pool; watching."},
            )
            # The signal recovers, as the project job closes a finding: the case stays.
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE findings SET state = 'closed', closed_reason = 'recovered', "
                        "closed_at = now() WHERE id = :id"
                    ),
                    {"id": finding},
                )
            recovered = await _call(engine, team, "member", "GET", path)
            assert (recovered["state"], recovered["case_state"]) == ("closed", "investigating")
            await _call(
                engine,
                team,
                "member",
                "PATCH",
                f"{path}/case",
                {"assignee_id": str(team["colleague"].id)},
            )
            await _call(
                engine, team, "colleague", "PATCH", f"{path}/case", {"case_state": "resolved"}
            )
            reopened = await _call(
                engine, team, "colleague", "PATCH", f"{path}/case", {"case_state": "investigating"}
            )
            assert reopened["finding"]["case_state"] == "investigating"

            # 90 days of silence, then the real worker's sweep ends it (BR13).
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE findings SET case_activity_at = now() - interval '91 days', "
                        "case_warned_at = now() - interval '15 days', "
                        "started_at = now() - interval '92 days', "
                        "closed_at = now() - interval '91 days' "
                        "WHERE id = :id"
                    ),
                    {"id": finding},
                )
                await connection.execute(
                    text(ENQUEUE), JobRequest(task=CASES_SWEEP_TASK, queue="light").parameters()
                )
            deadline = time.monotonic() + SETTLES_SECONDS
            ended = None
            while time.monotonic() < deadline:
                ended = await _one(
                    engine,
                    "SELECT case_state, case_resolution FROM findings WHERE id = :id "
                    "AND case_state = 'resolved'",
                    id=finding,
                )
                if ended is not None:
                    break
                assert worker.poll() is None, (tmp_path / "light.log").read_text()
                await asyncio.sleep(POLL_SECONDS)
    finally:
        watcher.cancel()

    assert ended is not None, (tmp_path / "light.log").read_text()[-3000:]
    assert ended.case_resolution == "no_activity"
    kinds = [
        row.kind
        for row in (
            await _rows(
                engine,
                "SELECT kind FROM finding_events WHERE org_id = :org AND finding_id = :id "
                "ORDER BY at, id",
                org=org_id,
                id=finding,
            )
        )
    ]
    assert kinds[0] == "case_started" and kinds[-1] == "case_resolved", kinds
    assert "note_added" in kinds
    # AI was off throughout, and nothing tried to use it.
    assert seen == {"ai_jobs": 0, "ai_sessions": 0}
    ai_rows = await _one(
        engine,
        "SELECT (SELECT count(*) FROM ai_connections WHERE org_id = :org) AS connections, "
        "(SELECT count(*) FROM ai_runs WHERE org_id = :org) AS runs",
        org=org_id,
    )
    assert (ai_rows.connections, ai_rows.runs) == (0, 0)


async def _rows(engine: AsyncEngine, sql: str, **values: Any) -> list[Any]:
    async with engine.connect() as connection:
        return list(await connection.execute(text(sql), values))
