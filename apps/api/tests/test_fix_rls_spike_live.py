"""Rung 2b's row security, run as `nexovue_insight` (F5 plan T2b.5, the spike's first
version; design, *The Postgres account*; AC6's general half).

The AI account sees a row in rung 2b's states and may change one only while `proposing`,
into one of them: the job's write lands, and a write to a row the sweep or the API ended
matches nothing. It cannot insert a row, read the stored answer or the requester, or move a
row back to `proposing`. T4.1 extends this to the source columns and the pack, running the
proposal job's own statements (`source_fix_store`): find the consent, read the frames,
drop the pack first and count it, move the state; and a late write, and a late pack
drop, match nothing once the sweep has ended the row.

The tenant is this test's own, committed so the AI account's own session sees it, and
dropped at the end.

This suite fails rather than skips when Postgres is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

import pytest
from insight_login import DEV_INSIGHT_PASSWORD, insight_dsn
from live import require_stack
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_api import insight_account

DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = DSN.replace("postgresql://", "postgresql+asyncpg://", 1)


def _reachable() -> bool:
    parts = urlsplit(DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_fix_rls_spike_live.py",
    remedy="start the dev stack so Postgres answers (`just dev`)",
)


@pytest.fixture
async def install(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    monkeypatch.setenv(insight_account.PASSWORD_VARIABLE, DEV_INSIGHT_PASSWORD)
    said: list[str] = []
    assert await insight_account.provision(ASYNC_DSN, say=said.append), said
    built = create_async_engine(ASYNC_DSN)
    yield built
    await built.dispose()


@pytest.fixture
async def as_insight(install: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    built = create_async_engine(insight_dsn(ASYNC_DSN))
    yield built
    await built.dispose()


@pytest.fixture
async def tenant(install: AsyncEngine) -> AsyncIterator[dict[str, uuid.UUID]]:
    org, project, user, finding = (uuid.uuid4() for _ in range(4))
    async with install.begin() as db:
        await db.execute(
            text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'RLS')"),
            {"id": org, "slug": f"rls-{org.hex[:12]}"},
        )
        await db.execute(
            text(
                "INSERT INTO users (id, org_id, email, role, password_hash) "
                "VALUES (:id, :org, :email, 'member', 'x')"
            ),
            {"id": user, "org": org, "email": f"r-{user.hex[:8]}@example.test"},
        )
        await db.execute(
            text("INSERT INTO projects (id, org_id, slug, name) VALUES (:id, :org, 'p', 'P')"),
            {"id": project, "org": org},
        )
        await db.execute(
            text(
                """
                INSERT INTO findings (id, org_id, project_id, scope, service_name, severity,
                                      state, title, signals, started_at)
                VALUES (:id, :org, :project, 'service', 'checkout', 'warning', 'open', 't',
                        '[]', now())
                """
            ),
            {"id": finding, "org": org, "project": project},
        )
    try:
        yield {"org": org, "project": project, "user": user, "finding": finding}
    finally:
        async with install.begin() as db:
            await db.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org})


async def _fix(install: AsyncEngine, tenant: dict[str, uuid.UUID], state: str) -> uuid.UUID:
    reason = None if state in ("proposing", "proposed") else "timed_out"
    suggestion = '{"summary": "s"}' if state == "proposed" else None
    async with install.begin() as db:
        made = await db.scalar(
            text(
                """
                INSERT INTO finding_fixes (org_id, project_id, finding_id, kind,
                    explanation_version, state, general_language, general_suggestion, reason,
                    requested_by)
                VALUES (:org, :project, :finding, 'general', 1, :state, 'python',
                        CAST(:suggestion AS jsonb), :reason, :user)
                RETURNING id
                """
            ),
            {**tenant, "state": state, "reason": reason, "suggestion": suggestion},
        )
    assert isinstance(made, uuid.UUID)
    return made


async def _end(as_insight: AsyncEngine, org: uuid.UUID, fix: uuid.UUID, state: str) -> int:
    """The job's own end write, as `fix_store._END` makes it."""
    reason = None if state == "proposed" else "general_withheld_G4"
    suggestion = '{"outcome": "suggestion"}' if state == "proposed" else None
    async with as_insight.begin() as db:
        changed = await db.execute(
            text(
                """
                UPDATE finding_fixes
                SET state = :state, reason = :reason,
                    general_suggestion = CAST(:suggestion AS jsonb),
                    tokens_used = tokens_used + 1, state_changed_at = now()
                WHERE org_id = :org AND id = :id AND state = 'proposing'
                """
            ),
            {"state": state, "reason": reason, "suggestion": suggestion, "org": org, "id": fix},
        )
    return changed.rowcount


async def test_the_ai_account_ends_a_proposing_row(
    install: AsyncEngine, as_insight: AsyncEngine, tenant: dict[str, uuid.UUID]
) -> None:
    fix = await _fix(install, tenant, "proposing")

    assert await _end(as_insight, tenant["org"], fix, "proposed") == 1


async def test_a_row_the_sweep_ended_takes_no_late_write(
    install: AsyncEngine, as_insight: AsyncEngine, tenant: dict[str, uuid.UUID]
) -> None:
    fix = await _fix(install, tenant, "failed")

    assert await _end(as_insight, tenant["org"], fix, "proposed") == 0
    async with install.connect() as db:
        state = await db.scalar(text("SELECT state FROM finding_fixes WHERE id = :id"), {"id": fix})
    assert state == "failed"


async def test_an_ended_row_cannot_be_moved_back_to_proposing(
    install: AsyncEngine, as_insight: AsyncEngine, tenant: dict[str, uuid.UUID]
) -> None:
    fix = await _fix(install, tenant, "refused")

    async with as_insight.begin() as db:
        changed = await db.execute(
            text(
                "UPDATE finding_fixes SET state = 'proposing', reason = NULL "
                "WHERE org_id = :org AND id = :id"
            ),
            {"org": tenant["org"], "id": fix},
        )
    assert changed.rowcount == 0


REFUSED: Final = {
    "an insert": (
        "INSERT INTO finding_fixes (org_id, project_id, finding_id, kind, "
        "explanation_version, general_language) VALUES (:org, :project, :finding, "
        "'general', 1, 'python')"
    ),
    "the stored answer": "SELECT general_suggestion FROM finding_fixes WHERE org_id = :org",
    "the requester": "SELECT requested_by FROM finding_fixes WHERE org_id = :org",
    "a delete": "DELETE FROM finding_fixes WHERE org_id = :org",
    "a change of language": "UPDATE finding_fixes SET general_language = 'go' WHERE org_id = :org",
}


@pytest.mark.parametrize("statement", REFUSED.values(), ids=REFUSED.keys())
async def test_the_ai_account_holds_no_more_than_its_columns(
    install: AsyncEngine, as_insight: AsyncEngine, tenant: dict[str, uuid.UUID], statement: str
) -> None:
    await _fix(install, tenant, "proposing")

    with pytest.raises(DBAPIError, match="permission denied"):
        async with as_insight.begin() as db:
            await db.execute(text(statement), tenant)


# ------------------------------------------------------------------ T4.1, rung 3


async def _source(install: AsyncEngine, tenant: dict[str, uuid.UUID], state: str) -> uuid.UUID:
    """A source row with a repository, a consent and a pack, as Send leaves it."""
    connection, repository = uuid.uuid4(), uuid.uuid4()
    reason = None if state in ("proposing", "proposed") else "timed_out"
    async with install.begin() as db:
        await db.execute(
            text(
                "INSERT INTO connections (id, org_id, kind, state) "
                "VALUES (:id, :org, 'github_fix', 'working')"
            ),
            {"id": connection, "org": tenant["org"]},
        )
        await db.execute(
            text(
                "INSERT INTO connection_repositories (id, org_id, connection_id, external_id, "
                "key, is_private) VALUES (:id, :org, :connection, '1', 'acme/shop', true)"
            ),
            {"id": repository, "org": tenant["org"], "connection": connection},
        )
        await db.execute(
            text(
                "INSERT INTO fix_source_consents (org_id, connection_repository_id, "
                "provider_base_url, provider_format, model, folder_scope, "
                "require_github_access, consented_at) VALUES (:org, :repository, "
                "'https://p.example/v1', 'openai_chat', 'm', 'shop', false, now())"
            ),
            {"org": tenant["org"], "repository": repository},
        )
        made = await db.scalar(
            text(
                """
                INSERT INTO finding_fixes (org_id, project_id, finding_id, kind, state,
                    reason, connection_repository_id, folder, requested_by)
                VALUES (:org, :project, :finding, 'source', :state, :reason, :repository,
                        'shop', :user)
                RETURNING id
                """
            ),
            {**tenant, "state": state, "reason": reason, "repository": repository},
        )
        await db.execute(
            text(
                "INSERT INTO finding_fix_source_packs (fix_id, org_id, files, frames, bytes, "
                "expires_at) VALUES (:fix, :org, CAST(:files AS jsonb), "
                "CAST(:frames AS jsonb), 3, now() + interval '20 minutes')"
            ),
            {
                "fix": made,
                "org": tenant["org"],
                "files": '[{"ref": "file_1", "path": "shop/a.py", "content": "x\\n"}]',
                "frames": '[{"ref": "file_1", "line": 1}]',
            },
        )
    assert isinstance(made, uuid.UUID)
    return made


async def test_t4_1_the_proposal_job_s_own_statements_run_as_the_ai_account(
    install: AsyncEngine, as_insight: AsyncEngine, tenant: dict[str, uuid.UUID]
) -> None:
    from nexovue_scheduler.insight import source_fix_store as store

    fix = await _source(install, tenant, "proposing")
    ids = {"org_id": tenant["org"], "fix_id": fix}

    async with as_insight.begin() as db:
        row = (await db.execute(store._FIX, ids)).one()
        consents = (
            await db.execute(
                store._CONSENTS,
                {"org_id": tenant["org"], "repository_id": row.connection_repository_id},
            )
        ).all()
        pack = (await db.execute(store._PACK, ids)).one()
        dropped = await db.scalar(store._DROP_PACK, ids)
        ended = await db.execute(
            store._END,
            {
                **ids,
                "state": "proposed",
                "reason": None,
                "edits": "[]",
                "evidence_ids": "[]",
                "withheld_refs": None,
                "tokens": 1,
                "at": datetime.now(UTC),
            },
        )

    assert row.folder == "shop" and [c.folder_scope for c in consents] == ["shop"]
    assert pack.frames == [{"ref": "file_1", "line": 1}]
    assert dropped == 1 and ended.rowcount == 1
    async with install.connect() as db:
        left = await db.scalar(
            text("SELECT count(*) FROM finding_fix_source_packs WHERE fix_id = :id"), {"id": fix}
        )
    assert left == 0


async def test_t4_1_a_row_the_sweep_ended_takes_no_late_write_and_keeps_no_readable_pack(
    install: AsyncEngine, as_insight: AsyncEngine, tenant: dict[str, uuid.UUID]
) -> None:
    from nexovue_scheduler.insight import source_fix_store as store

    fix = await _source(install, tenant, "failed")
    ids = {"org_id": tenant["org"], "fix_id": fix}

    async with as_insight.begin() as db:
        pack = (await db.execute(store._PACK, ids)).one_or_none()
        dropped = await db.scalar(store._DROP_PACK, ids)
        late = await db.execute(store._KEEP_FOR_RETRY, {**ids, "retry_code": None, "tokens": 1})

    assert pack is None and dropped == 0 and late.rowcount == 0


async def test_t4_1_only_the_function_takes_a_row_back_to_gathering(
    install: AsyncEngine, as_insight: AsyncEngine, tenant: dict[str, uuid.UUID]
) -> None:
    from nexovue_scheduler.insight import source_fix_store as store

    fix = await _source(install, tenant, "proposing")
    ids = {"org_id": tenant["org"], "fix_id": fix}

    with pytest.raises(DBAPIError, match="row-level security"):
        async with as_insight.begin() as db:
            await db.execute(
                text(
                    "UPDATE finding_fixes SET state = 'gathering' "
                    "WHERE org_id = :org_id AND id = :fix_id"
                ),
                ids,
            )
    async with as_insight.begin() as db:
        await db.execute(
            store._ASK_FOR_FILES, {**ids, "requested": '[{"path": "a.py"}]', "tokens": 1}
        )
        moved = await db.scalar(store._REQUEST_MORE_FILES, ids)
    assert moved is True
    async with install.begin() as db:
        state = await db.scalar(text("SELECT state FROM finding_fixes WHERE id = :id"), {"id": fix})
        await db.execute(text("DELETE FROM jobs WHERE org_id = :org"), {"org": tenant["org"]})
    assert state == "gathering"
