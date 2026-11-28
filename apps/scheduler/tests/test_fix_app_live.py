"""The Fix App's and the account link's jobs, against real Postgres and the simulated GitHub
(F5 plan T3.3, T3.17; spec AC15, AC31, A18).

- The manifest code converts once; the App's key and its OAuth client secret are sealed in
  the transaction that drops the code; no secret reaches a log.
- The hourly refresh binds the App's own installation, its repositories and its grant.
- A person's code is exchanged once; the GitHub id and login are kept, the code is gone in
  the same transaction, no token is stored, and the audit entry names no login.
Each test builds and drops its own organization.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from alert_fixtures import postgres_reachable
from live import require_stack
from nexovue_platform.connections import CONNECTION_MANIFEST_CODE_PURPOSE, connection_secret_owner
from nexovue_platform.fixes.records import (
    FIX_APP_CLIENT_SECRET_PURPOSE,
    GITHUB_LINK_CODE_PURPOSE,
    github_link_code_owner,
)
from source_fix_fixtures import KEYS, REPOSITORY, SourceWorld, box, build_world, drop_world
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs
from tracker_fake import CLIENT_SECRET, FakeGitHub, serve_fake_github

from nexovue_scheduler.connections import fix_app, fix_link

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_app_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
def now() -> datetime:
    """This test's clock, read when it starts: the stack's own sweep reads the real clock,
    so a row dated in the past is one it may expire or end under the test."""
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def fake() -> Iterator[FakeGitHub]:
    with serve_fake_github() as served:
        yield served


@pytest.fixture
async def world(
    engine: AsyncEngine, connections_engine: AsyncEngine, fake: FakeGitHub, now: datetime
) -> AsyncIterator[SourceWorld]:
    made = await build_world(engine, fake, jobs=connections_engine, now=now)
    try:
        yield made
    finally:
        await drop_world(engine, made)


async def test_t3_3_conversion_seals_the_key_and_client_secret_and_drops_the_code(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    connection_id = uuid.uuid4()
    owner = connection_secret_owner(str(world.org_id), str(connection_id))
    code = fake.add_code()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE connections SET state = 'disconnected', sealed_credential = NULL, "
                "sealed_client_secret = NULL WHERE id = :id"
            ),
            {"id": world.connection_id},
        )
        await connection.execute(
            text(
                "INSERT INTO connections (id, org_id, kind, state, sealed_manifest_code, "
                "setup_expires_at) VALUES (:id, :org, 'github_fix', 'setting_up', :code, :at)"
            ),
            {
                "id": connection_id,
                "org": world.org_id,
                "code": box(CONNECTION_MANIFEST_CODE_PURPOSE).seal(code, owner=owner),
                "at": now + timedelta(hours=1),
            },
        )

    with capture_logs() as logs:
        await fix_app.convert_fix_app(
            world.jobs,
            org_id=str(world.org_id),
            connection_id=str(connection_id),
            keys=KEYS,
            endpoints=world.endpoints(),
            now=now,
        )

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT * FROM connections WHERE id = :id"), {"id": connection_id}
            )
        ).one()
    assert row.sealed_manifest_code is None and row.github_app_id == fake.app_id
    assert box(FIX_APP_CLIENT_SECRET_PURPOSE).open(
        bytes(row.sealed_client_secret), owner=owner
    ) == (CLIENT_SECRET)
    assert CLIENT_SECRET not in json.dumps(logs, default=str)
    assert CLIENT_SECRET.encode() not in bytes(row.sealed_client_secret)


async def test_t3_3_the_refresh_keeps_the_grant_and_the_repositories(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fake.granted = {"contents": "read", "metadata": "read", "pull_requests": "read"}

    await fix_app.refresh_fix_app(
        world.jobs,
        org_id=str(world.org_id),
        connection_id=str(world.connection_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        now=now,
    )

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT granted_permissions, granted_at, state FROM connections WHERE id = :id"
                ),
                {"id": world.connection_id},
            )
        ).one()
        keys = list(
            await connection.scalars(
                text("SELECT key FROM connection_repositories WHERE org_id = :org"),
                {"org": world.org_id},
            )
        )
    assert row.granted_permissions == fake.granted and row.granted_at == now
    assert row.state == "working" and keys == [REPOSITORY]


async def _attempt(engine: AsyncEngine, world: SourceWorld, code: str, now: datetime) -> uuid.UUID:
    attempt_id = uuid.uuid4()
    sealed = box(GITHUB_LINK_CODE_PURPOSE).seal(
        code, owner=github_link_code_owner(str(world.org_id), str(world.user_id), str(attempt_id))
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO github_link_attempts (id, org_id, user_id, session_hash, state_hash, "
                "sealed_code, status, expires_at) VALUES (:id, :org, :user, :session, :state, "
                ":code, 'running', :at)"
            ),
            {
                "id": attempt_id,
                "org": world.org_id,
                "user": world.user_id,
                "session": "s" * 64,
                "state": uuid.uuid4().hex * 2,
                "code": sealed,
                "at": now + timedelta(minutes=10),
            },
        )
    return attempt_id


async def test_ac31_a_link_keeps_the_id_and_login_and_no_token_and_the_code_goes(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    code = fake.add_oauth_code(7001, "octo-reader")
    attempt_id = await _attempt(engine, world, code, now)

    outcome = await fix_link.complete_github_user_link(
        world.jobs,
        org_id=str(world.org_id),
        attempt_id=str(attempt_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        now=now,
    )

    assert outcome == "linked"
    async with engine.connect() as connection:
        link = (
            await connection.execute(
                text("SELECT * FROM github_user_links WHERE user_id = :user"),
                {"user": world.user_id},
            )
        ).one()
        attempt = (
            await connection.execute(
                text("SELECT status, sealed_code FROM github_link_attempts WHERE id = :id"),
                {"id": attempt_id},
            )
        ).one()
        audit = list(
            await connection.scalars(
                text(
                    "SELECT detail::text FROM audit_log WHERE org_id = :org "
                    "AND action = 'github_account.linked'"
                ),
                {"org": world.org_id},
            )
        )
        jobs = list(
            await connection.scalars(
                text("SELECT kwargs::text FROM jobs WHERE org_id = :org"), {"org": world.org_id}
            )
        )
    assert (link.github_user_id, link.github_login) == (7001, "octo-reader")
    assert attempt == ("linked", None)
    assert all("octo-reader" not in (each or "") and "7001" not in (each or "") for each in audit)
    assert all(code not in each for each in jobs)


async def test_a18_a_code_github_refuses_links_nothing(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    attempt_id = await _attempt(engine, world, "never-issued", now)

    outcome = await fix_link.complete_github_user_link(
        world.jobs,
        org_id=str(world.org_id),
        attempt_id=str(attempt_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        now=now,
    )

    assert outcome == "refused"
    async with engine.connect() as connection:
        linked = await connection.scalar(
            text("SELECT count(*) FROM github_user_links WHERE org_id = :org"),
            {"org": world.org_id},
        )
    assert linked == 0


async def test_a_job_naming_another_organization_s_attempt_finds_nothing(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    attempt_id = await _attempt(engine, world, fake.add_oauth_code(7002, "other"), now)

    outcome = await fix_link.complete_github_user_link(
        world.jobs,
        org_id=str(uuid.uuid4()),
        attempt_id=str(attempt_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        now=now,
    )

    assert outcome is None
