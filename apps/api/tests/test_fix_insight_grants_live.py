"""The AI account reaches rung 2b's queue and rows as `setup` grants them (F5 plan T2b.5;
design, *The Postgres account*, D40, D56; AC6's general half).

Grants live in `insight_account.INSIGHT_GRANTS`, applied by `setup`, never in a migration.
Column by column, the whole list is `test_insight_grants_live.py`'s; this holds what rung 2b
needs of it: F3's three job functions claim, retry and finish an `insight-fix` job, and
still match no queue outside the AI role's; and the job's reads of `finding_fixes` and of
the version's `model_input` work, while the evidence and the names stay out of reach.

The job rows and tenant are this test's own, committed for the AI account's session, and
removed at the end.

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
from nexovue_platform.fixes.records import FIX_QUEUE
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_api import insight_account

DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
WORKER: Final = "fix-grants-test"
_ADD_JOB: Final = text(
    """
    INSERT INTO jobs (queue, task, kwargs, org_id, max_attempts, run_after,
                      soft_limit_seconds, hard_limit_seconds)
    VALUES (:queue, :task, '{}', :org, 2, now() - interval '1 second', 20, 30)
    RETURNING id
    """
)
_CLAIM: Final = text("SELECT id FROM insight_claim_jobs(:worker, :queues, 10, 30)")


def _reachable() -> bool:
    parts = urlsplit(DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_fix_insight_grants_live.py",
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
async def org(install: AsyncEngine) -> AsyncIterator[uuid.UUID]:
    made = uuid.uuid4()
    async with install.begin() as db:
        await db.execute(
            text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Grants')"),
            {"id": made, "slug": f"grants-{made.hex[:12]}"},
        )
    try:
        yield made
    finally:
        async with install.begin() as db:
            await db.execute(text("DELETE FROM jobs WHERE org_id = :id"), {"id": made})
            await db.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": made})


async def _job(install: AsyncEngine, org: uuid.UUID, queue: str) -> uuid.UUID:
    async with install.begin() as db:
        made = await db.scalar(
            _ADD_JOB, {"queue": queue, "task": f"fix-grants-test.{queue}", "org": org}
        )
    assert isinstance(made, uuid.UUID)
    return made


async def test_the_ai_account_claims_retries_and_finishes_an_insight_fix_job(
    install: AsyncEngine, as_insight: AsyncEngine, org: uuid.UUID
) -> None:
    job = await _job(install, org, FIX_QUEUE)

    async with as_insight.begin() as db:
        claimed = set(
            (await db.execute(_CLAIM, {"worker": WORKER, "queues": [FIX_QUEUE]})).scalars()
        )
        assert job in claimed
        await db.execute(
            text("SELECT insight_retry_job(:id, :worker, 0, 'one more attempt')"),
            {"id": job, "worker": WORKER},
        )
    async with as_insight.begin() as db:
        again = set((await db.execute(_CLAIM, {"worker": WORKER, "queues": [FIX_QUEUE]})).scalars())
        assert job in again
        await db.execute(
            text("SELECT insight_finish_job(:id, :worker)"), {"id": job, "worker": WORKER}
        )
    async with install.connect() as db:
        left = await db.scalar(text("SELECT count(*) FROM jobs WHERE id = :id"), {"id": job})
    assert left == 0


async def test_the_claim_still_matches_no_queue_outside_the_ai_role(
    install: AsyncEngine, as_insight: AsyncEngine, org: uuid.UUID
) -> None:
    light = await _job(install, org, "light")

    async with as_insight.begin() as db:
        claimed = set(
            (await db.execute(_CLAIM, {"worker": WORKER, "queues": ["light", FIX_QUEUE]})).scalars()
        )

    assert light not in claimed


async def test_the_ai_account_reads_model_input_and_never_the_evidence_or_the_names(
    as_insight: AsyncEngine,
) -> None:
    async with as_insight.connect() as db:
        await db.execute(text("SELECT model_input FROM finding_explanations LIMIT 1"))
        for column in ("evidence", "aliases", "likely_cause"):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with db.begin_nested():
                    await db.execute(text(f"SELECT {column} FROM finding_explanations LIMIT 1"))
