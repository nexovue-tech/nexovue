"""Check my access: the check run for `read`, against the simulated GitHub (F5 plan T4.12;
spec D20; AC10a's read half).

Under the owner's requirement, quoted code shows only after this person's own check: GitHub
says what access their linked account has. At least `read` passes, for an hour; no access,
no link, or GitHub unable to say refuses, audited, and **the suggestion's row never moves**.
A passing failure is retried while attempts last, then `github_unavailable`. Every test
builds and drops its own organization; the clock is its own.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alert_fixtures import postgres_reachable
from live import require_stack
from nexovue_platform.fixes.records import FIX_CHECK_REFUSED, READ_CHECK_LIFETIME
from source_fix_fixtures import (
    KEYS,
    REPOSITORY,
    SourceWorld,
    build_world,
    consent,
    drop_world,
    fix_row,
    source_fix,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tracker_fake import FakeGitHub, serve_fake_github

from nexovue_scheduler.connections import fix_check
from nexovue_scheduler.jobs import registry
from nexovue_scheduler.jobs.registry import Retry

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_check_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

FOLDER = "services/shop"


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
async def world(engine: AsyncEngine, fake: FakeGitHub, now: datetime) -> AsyncIterator[SourceWorld]:
    made = await build_world(engine, fake, now=now)
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM fix_source_consents WHERE org_id = :org"), {"org": made.org_id}
        )
    await consent(
        engine, made.org_id, made.repository_id, folder=FOLDER, now=now, require_github_access=True
    )
    try:
        yield made
    finally:
        await drop_world(engine, made)


async def _proposed(engine: AsyncEngine, world: SourceWorld, now: datetime) -> uuid.UUID:
    return await source_fix(engine, world, candidates=[], now=now, state="proposed")


async def _check(engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID, now: datetime) -> str:
    async with engine.begin() as connection:
        made = await connection.scalar(
            text(
                """
                INSERT INTO finding_fix_checks (org_id, fix_id, requested_by, purpose, status,
                    requested_at, expires_at)
                VALUES (:org, :fix, :user, 'read', 'running', :now, :expires)
                RETURNING id
                """
            ),
            {
                "org": world.org_id,
                "fix": fix_id,
                "user": world.user_id,
                "now": now,
                "expires": now + timedelta(minutes=15),
            },
        )
    return str(made)


async def _link(engine: AsyncEngine, world: SourceWorld, github_id: int, login: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO github_user_links (user_id, org_id, github_user_id, github_login) "
                "VALUES (:user, :org, :id, :login)"
            ),
            {"user": world.user_id, "org": world.org_id, "id": github_id, "login": login},
        )


async def _run(engine: AsyncEngine, world: SourceWorld, check_id: str, now: datetime) -> Any:
    return await fix_check.check_fix(
        engine,
        org_id=str(world.org_id),
        check_id=check_id,
        keys=KEYS,
        endpoints=world.endpoints(),
        now=now,
    )


async def _check_row(engine: AsyncEngine, check_id: str) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text("SELECT status, reason, expires_at FROM finding_fix_checks WHERE id = :id"),
                {"id": check_id},
            )
        ).one()


async def _refusals(engine: AsyncEngine, world: SourceWorld) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT actor_user_id, detail FROM audit_log "
                    "WHERE org_id = :org AND action = :action"
                ),
                {"org": world.org_id, "action": FIX_CHECK_REFUSED},
            )
        )


async def test_ac10a_a_person_github_says_can_read_passes_for_an_hour(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fake.add_user(9101, "reader")
    fake.grant(REPOSITORY, "reader", "read")
    await _link(engine, world, 9101, "reader")
    fix_id = await _proposed(engine, world, now)
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "unchanged"
    assert await _run(engine, world, check, now) is None

    row = await _check_row(engine, check)
    assert (row.status, row.reason, row.expires_at) == (
        "unchanged",
        None,
        now + READ_CHECK_LIFETIME,
    )
    assert (await fix_row(engine, world, fix_id)).state == "proposed"
    assert await _refusals(engine, world) == []


async def test_ac10a_a_person_without_access_is_refused_audited_and_the_row_does_not_move(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fake.add_user(9102, "outsider")
    await _link(engine, world, 9102, "outsider")
    fix_id = await _proposed(engine, world, now)
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "refused"

    assert (await _check_row(engine, check)).reason == "no_github_access"
    assert (await fix_row(engine, world, fix_id)).state == "proposed"
    [audited] = await _refusals(engine, world)
    assert audited.actor_user_id == world.user_id
    assert audited.detail["reason"] == "no_github_access"
    assert "outsider" not in str(audited.detail) and "9102" not in str(audited.detail)


async def test_ac10a_no_link_is_refused_before_any_github_call(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _proposed(engine, world, now)
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "refused"
    assert (await _check_row(engine, check)).reason == "not_linked"


async def test_ac10a_github_failing_is_retried_then_github_unavailable(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fake.add_user(9103, "reader")
    fake.grant(REPOSITORY, "reader", "read")
    await _link(engine, world, 9103, "reader")
    fake.fail("permission", 503)
    fix_id = await _proposed(engine, world, now)
    check = await _check(engine, world, fix_id, now)

    with pytest.raises(Retry):
        await _run(engine, world, check, now)
    token = registry._ATTEMPT.set(3)
    try:
        assert await _run(engine, world, check, now) == "failed"
    finally:
        registry._ATTEMPT.reset(token)
    assert (await _check_row(engine, check)).reason == "github_unavailable"


async def test_a_withdrawn_consent_stops_the_check(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _proposed(engine, world, now)
    check = await _check(engine, world, fix_id, now)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE fix_source_consents SET withdrawn_at = :now, withdrawn_reason = 'owner' "
                "WHERE org_id = :org"
            ),
            {"now": now, "org": world.org_id},
        )

    assert await _run(engine, world, check, now) == "failed"
    assert (await _check_row(engine, check)).reason == "consent_withdrawn"
