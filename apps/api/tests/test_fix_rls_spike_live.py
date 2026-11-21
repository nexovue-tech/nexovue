"""Rung 2b's row security, run as `nexovue_insight` (F5 plan T2b.5, the spike's first
version; design, *The Postgres account*; AC6's general half).

The AI account sees a row in rung 2b's states and may change one only while `proposing`,
into one of them: the job's write lands, and a write to a row the sweep or the API ended
matches nothing. It cannot insert a row, read the stored answer or the requester, or move a
row back to `proposing`. T4.1 extends this to the source columns and the pack.

The tenant is this test's own, committed so the AI account's own session sees it, and
dropped at the end.

This suite fails rather than skips when Postgres is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
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
