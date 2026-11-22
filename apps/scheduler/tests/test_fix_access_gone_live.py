"""Taking stored code back where the scheduler owns the trigger (F5 plan T5.3; spec BR9,
AC29, AC29b, AC59).

BR9's table, row by row, for the rows the sweep and the notifier own: GitHub access gone
for 72 hours (an unreachable repository, or an App GitHub says is not installed) withdraws
consent and deletes code, as an owner would; a person's AI-off deletes code, F3's automatic
off does not; a consent that ended by itself keeps code and tells each owner once; a stale
suggestion keeps its code until its 30 days. Each test builds and drops its own
organization, passes its own clock, and narrows every pass to its own rows (`only_org`).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alert_fixtures import postgres_reachable
from live import require_stack
from nexovue_platform.email import Message
from nexovue_platform.email.templates import render
from nexovue_platform.fixes.records import (
    ACCESS_GONE_AFTER,
    AI_SOURCE_DISCLOSURE_WITHDRAWN,
    REPOSITORY_AUDIT_TARGET,
)
from source_fix_fixtures import SourceWorld, build_world, drop_world, fix_row, pack_of, source_fix
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tracker_fake import FakeGitHub, serve_fake_github

from nexovue_scheduler.connections import fix_sweep
from nexovue_scheduler.connections.tasks import write_reach
from nexovue_scheduler.connections.trackers import Place, Reach
from nexovue_scheduler.fixes import notify

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_access_gone_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

MARKER = "MARKER-4f1c-in-a-customer-file"


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


async def _written(engine: AsyncEngine, world: SourceWorld, now: datetime, **columns: Any) -> Any:
    values: dict[str, Any] = {
        "files_read": json.dumps({"files": [{"ref": "file_1", "path": f"x/{MARKER}.py"}]}),
        "edits": json.dumps([{"file_ref": "file_1", "old_text": MARKER, "new_text": "y"}]),
        "finished_at": now,
        "result_recorded_at": now,
    }
    values.update(columns)
    state = values.pop("state", "proposed")
    return await source_fix(engine, world, candidates=[], now=now, state=state, **values)


async def _in_flight(engine: AsyncEngine, world: SourceWorld, now: datetime) -> uuid.UUID:
    fix_id = await source_fix(engine, world, candidates=[], now=now, state="proposing")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO finding_fix_source_packs (fix_id, org_id, files, frames, bytes, "
                "expires_at) VALUES (:id, :org, CAST(:files AS jsonb), '[]', 1, :expires)"
            ),
            {
                "id": fix_id,
                "org": world.org_id,
                "files": json.dumps([{"ref": "file_1", "content": MARKER}]),
                "expires": now + timedelta(hours=1),
            },
        )
    return fix_id


async def _sweep(engine: AsyncEngine, world: SourceWorld, now: datetime) -> Any:
    return await fix_sweep.sweep(world.jobs, clock=lambda: now, only_org=str(world.org_id))


async def _execute(engine: AsyncEngine, statement: str, **values: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(statement), values)


async def _consents(engine: AsyncEngine, world: SourceWorld) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT withdrawn_at, withdrawn_reason, ended_notice_sent_at "
                    "FROM fix_source_consents WHERE org_id = :org"
                ),
                {"org": world.org_id},
            )
        )


async def _whole_row(engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID) -> str:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT row_to_json(f)::text FROM finding_fixes AS f WHERE f.id = :id"),
                {"id": fix_id},
            )
        ).scalar_one()
    return str(row)


# ------------------------------------------------------- GitHub access gone, 72 hours


async def test_ac29_a_repository_unreachable_past_72_hours_takes_everything_back(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    written = await _written(engine, world, now)
    flying = await _in_flight(engine, world, now)
    await _execute(
        engine,
        "UPDATE connection_repositories SET is_reachable = false, unreachable_since = :since "
        "WHERE id = :id",
        since=now - ACCESS_GONE_AFTER - timedelta(minutes=1),
        id=world.repository_id,
    )

    swept = await _sweep(engine, world, now)

    assert swept.access_gone == 1
    [consent] = await _consents(engine, world)
    assert (consent.withdrawn_at, consent.withdrawn_reason) == (now, "access_gone")
    kept = await fix_row(engine, world, written)
    assert (kept.state, kept.code_deleted_reason) == ("proposed", "access_gone")
    assert MARKER not in await _whole_row(engine, world, written)
    ended = await fix_row(engine, world, flying)
    assert (ended.state, ended.reason) == ("failed", "consent_ended_in_flight")
    assert await pack_of(engine, world, flying) is None
    async with engine.connect() as connection:
        audited = (
            await connection.execute(
                text("SELECT target, detail FROM audit_log WHERE org_id = :org AND action = :a"),
                {"org": world.org_id, "a": AI_SOURCE_DISCLOSURE_WITHDRAWN},
            )
        ).one()
    assert audited.target == f"{REPOSITORY_AUDIT_TARGET}:{world.repository_id}"
    assert audited.detail["reason"] == "access_gone"


async def test_ac29b_a_repository_unreachable_under_72_hours_keeps_its_code(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    written = await _written(engine, world, now)
    await _execute(
        engine,
        "UPDATE connection_repositories SET is_reachable = false, unreachable_since = :since "
        "WHERE id = :id",
        since=now - ACCESS_GONE_AFTER + timedelta(minutes=1),
        id=world.repository_id,
    )

    assert (await _sweep(engine, world, now)).access_gone == 0

    assert (await fix_row(engine, world, written)).code_deleted_at is None
    assert (await _consents(engine, world))[0].withdrawn_at is None


async def test_ac29_an_app_github_says_is_not_installed_past_72_hours_takes_code_back(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    written = await _written(engine, world, now)
    await _execute(
        engine,
        "UPDATE connections SET state = 'needs_attention', last_failure = 'app_not_installed', "
        "failing_since = :since, consecutive_failures = 1 WHERE id = :id",
        since=now - ACCESS_GONE_AFTER - timedelta(minutes=1),
        id=world.connection_id,
    )

    await _sweep(engine, world, now)

    assert (await fix_row(engine, world, written)).code_deleted_reason == "access_gone"


async def test_the_refresh_starts_the_clock_once_and_a_repository_that_answers_clears_it(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    """F22's refresh (`write_reach`) keeps the first miss, so 72 hours count from it."""
    org, connection = str(world.org_id), str(world.connection_id)
    await write_reach(world.jobs, org, connection, Reach(places=()), now=now)
    await write_reach(world.jobs, org, connection, Reach(places=()), now=now + timedelta(hours=1))
    async with engine.connect() as db:
        missed = (
            await db.execute(
                text(
                    "SELECT is_reachable, unreachable_since FROM connection_repositories "
                    "WHERE id = :id"
                ),
                {"id": world.repository_id},
            )
        ).one()
    assert (missed.is_reachable, missed.unreachable_since) == (False, now)

    place = Place(external_id="4200001", key="acme/shop", is_private=True)
    await write_reach(
        world.jobs, org, connection, Reach(places=(place,)), now=now + timedelta(hours=2)
    )
    async with engine.connect() as db:
        back = (
            await db.execute(
                text(
                    "SELECT is_reachable, unreachable_since FROM connection_repositories "
                    "WHERE id = :id"
                ),
                {"id": world.repository_id},
            )
        ).one()
    assert (back.is_reachable, back.unreachable_since) == (True, None)


# ------------------------------------------------- AI off, and consent ended by itself


async def test_ac29b_f3_s_automatic_off_keeps_code_where_a_person_s_off_deletes_it(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    written = await _written(engine, world, now)
    await _execute(
        engine,
        "UPDATE ai_connections SET enabled = false, off_reason = 'consent_ended' "
        "WHERE org_id = :org",
        org=world.org_id,
    )

    await _sweep(engine, world, now)
    assert (await fix_row(engine, world, written)).code_deleted_at is None

    await _execute(
        engine,
        "UPDATE ai_connections SET off_reason = 'person' WHERE org_id = :org",
        org=world.org_id,
    )
    await _sweep(engine, world, now)
    assert (await fix_row(engine, world, written)).code_deleted_reason == "ai_off"


async def test_ac29b_a_consent_ended_by_a_new_model_keeps_code(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    written = await _written(engine, world, now)
    await _execute(
        engine, "UPDATE ai_connections SET model = 'model-b' WHERE org_id = :org", org=world.org_id
    )

    await _sweep(engine, world, now)

    assert (await fix_row(engine, world, written)).code_deleted_at is None


async def test_ac29b_a_stale_suggestion_keeps_its_code_until_its_30_days(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    young = await _written(engine, world, now, state="stale", reason="rules_changed")
    old = await _written(
        engine,
        world,
        now,
        state="stale",
        reason="rules_changed",
        finished_at=now - timedelta(days=31),
    )

    await _sweep(engine, world, now)

    assert (await fix_row(engine, world, young)).code_deleted_at is None
    assert (await fix_row(engine, world, old)).code_deleted_reason == "aged_out"


# ------------------------------------------------------------------ the owners' email


class Recording:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(
        self, *, to: str, message: Message, data: dict[str, Any], idempotency_key: str | None = None
    ) -> None:
        self.sent.append({"to": to, "message": message, "data": data})


async def test_ac59_a_new_key_tells_each_owner_once_naming_it_and_keeps_code(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    written = await _written(engine, world, now)
    await _execute(
        engine,
        "UPDATE users SET role = 'owner', email_verified_at = :now WHERE org_id = :org",
        now=now,
        org=world.org_id,
    )
    await _execute(
        engine,
        "UPDATE ai_connections SET key_id = :key WHERE org_id = :org",
        key=uuid.uuid4(),
        org=world.org_id,
    )
    sender = Recording()

    first = await notify.notify(
        engine, sender=sender, app_base_url="https://console.example", only_org=str(world.org_id)
    )
    again = await notify.notify(
        engine, sender=sender, app_base_url="https://console.example", only_org=str(world.org_id)
    )

    assert (first.owners_told, again.owners_told) == (1, 0)
    [email] = sender.sent
    assert email["message"] == Message.FIX_CONSENT_ENDED
    assert email["data"] == {
        "changed": "the AI provider's key",
        "link": "https://console.example/settings/ai",
    }
    _subject, body = render(Message.FIX_CONSENT_ENDED, email["data"])
    assert "because the AI provider's key changed" in body and "acme" not in body
    assert (await _consents(engine, world))[0].ended_notice_sent_at is not None
    assert (await fix_row(engine, world, written)).code_deleted_at is None


async def test_ac59_unchanged_terms_tell_nobody(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    await _execute(
        engine,
        "UPDATE users SET role = 'owner', email_verified_at = :now WHERE org_id = :org",
        now=now,
        org=world.org_id,
    )
    sender = Recording()

    told = await notify.notify(
        engine, sender=sender, app_base_url="https://console.example", only_org=str(world.org_id)
    )

    assert told.owners_told == 0 and sender.sent == []


def test_the_changed_words_name_each_change_once_in_order() -> None:
    class Row:
        def __init__(self, **changed: bool) -> None:
            for name in ("address", "format", "key", "model"):
                setattr(self, name, changed.get(name, False))

    assert notify.changed_words([Row(model=True), Row(key=True)]) == (
        "the AI provider's key and the AI model"
    )
    assert notify.changed_words([Row(address=True, format=True, model=True)]) == (
        "the AI provider's address, the AI provider's format and the AI model"
    )
