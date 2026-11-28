"""Rung 3's sweep, against real Postgres (F5 plan T3.13; spec AC14's sweep half, AC46).

Each test builds and drops its own organization and asserts on its own rows only; the
clock is the test's own, passed to the sweep. A sweep moves every organization's due rows,
so the assertions name this test's ids, never a count across tenants.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from alert_fixtures import postgres_reachable
from held_jobs import jobs_held
from live import require_stack
from nexovue_platform.fixes.records import REFRESH_FIX_APP_TASK
from source_fix_fixtures import (
    SourceWorld,
    build_world,
    drop_world,
    fix_row,
    pack_of,
    source_fix,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tracker_fake import FakeGitHub, serve_fake_github

from nexovue_scheduler.connections import fix_sweep as source_sweep

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_sweep_live.py",
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
    """A world whose every job is held from the stack's own connections worker: the sweep's
    jobs are read here, never run, and the stack would run and finish them first."""
    made = await build_world(engine, fake, jobs=connections_engine, now=now)
    try:
        with jobs_held(engine, made.org_id):
            yield made
    finally:
        await drop_world(engine, made)


async def _gathered(engine: AsyncEngine, world: SourceWorld, at: datetime) -> uuid.UUID:
    fix_id = await source_fix(
        engine,
        world,
        candidates=[],
        now=at,
        state="gathered",
        files_read=json.dumps({"files": [{"path": "services/shop/a.py"}]}),
        files_digest="d" * 64,
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO finding_fix_source_packs (fix_id, org_id, files, frames, bytes, "
                "expires_at) VALUES (:id, :org, '[]', '[]', 0, :expires)"
            ),
            {"id": fix_id, "org": world.org_id, "expires": at + timedelta(hours=1)},
        )
    return fix_id


async def test_ac46_a_list_nobody_sent_in_15_minutes_lapses_with_its_pack_and_code_gone(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    fix_id = await _gathered(engine, world, now - timedelta(minutes=16))

    await source_sweep.sweep(world.jobs, clock=lambda: now, only_org=str(world.org_id))

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.reason) == ("failed", "lapsed")
    assert await pack_of(engine, world, fix_id) is None
    assert row.files_read is None and row.files_digest is None
    assert row.code_deleted_reason == "ended_short" and row.result_recorded_at == now


async def test_a_list_younger_than_15_minutes_waits(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    fix_id = await _gathered(engine, world, now - timedelta(minutes=14))

    await source_sweep.sweep(world.jobs, clock=lambda: now, only_org=str(world.org_id))

    assert (await fix_row(engine, world, fix_id)).state == "gathered"


async def test_ac14_a_row_stuck_gathering_ends_timed_out_with_its_candidates_cleared(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    fix_id = await source_fix(
        engine,
        world,
        candidates=[{"path": "/app/x.py", "line": 1, "from": "stack"}],
        now=now - timedelta(minutes=21),
    )

    await source_sweep.sweep(world.jobs, clock=lambda: now, only_org=str(world.org_id))

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.reason) == ("failed", "timed_out")
    assert row.path_candidates is None


async def test_ac14_a_pack_past_its_expiry_is_removed(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    fix_id = await _gathered(engine, world, now)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE finding_fix_source_packs SET expires_at = :at WHERE fix_id = :id"),
            {"at": now - timedelta(seconds=1), "id": fix_id},
        )

    await source_sweep.sweep(world.jobs, clock=lambda: now, only_org=str(world.org_id))

    assert await pack_of(engine, world, fix_id) is None


async def test_code_is_cleared_when_a_person_switches_ai_off(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    fix_id = await source_fix(
        engine,
        world,
        candidates=[],
        now=now,
        state="proposed",
        files_read=json.dumps({"files": []}),
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE ai_connections SET enabled = false, off_reason = 'person' "
                "WHERE org_id = :org"
            ),
            {"org": world.org_id},
        )

    await source_sweep.sweep(world.jobs, clock=lambda: now, only_org=str(world.org_id))

    row = await fix_row(engine, world, fix_id)
    assert (row.files_read, row.code_deleted_reason) == (None, "ai_off")


async def test_the_fix_app_is_refreshed_hourly_as_a_job_with_ids_only(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE connections SET repositories_read_at = :at WHERE id = :id"),
            {"at": now - timedelta(hours=2), "id": world.connection_id},
        )

    await source_sweep.sweep(world.jobs, clock=lambda: now, only_org=str(world.org_id))

    async with engine.connect() as connection:
        kwargs = (
            await connection.execute(
                text("SELECT kwargs FROM jobs WHERE org_id = :org AND task = :task"),
                {"org": world.org_id, "task": REFRESH_FIX_APP_TASK},
            )
        ).scalar_one()
    assert kwargs == {"org_id": str(world.org_id), "connection_id": str(world.connection_id)}
