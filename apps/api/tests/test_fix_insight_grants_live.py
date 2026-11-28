"""The AI account reaches rung 2b's queue and rows as `setup` grants them (F5 plan T2b.5;
design, *The Postgres account*, D40, D56; AC6's general half).

Grants live in `insight_account.INSIGHT_GRANTS`, applied by `setup`, never in a migration.
Column by column, the whole list is `test_insight_grants_live.py`'s; this holds what rung 2b
needs of it: F3's three job functions claim, retry and finish an `insight-fix` job, and
still match no queue outside the AI role's; and the job's reads of `finding_fixes` and of
the version's `model_input` work, while the evidence and the names stay out of reach.

The job tests write their tenant and job, act as the AI role (`SET LOCAL ROLE`, so the role's
own grants decide each call), and claim, all in one transaction that is rolled back. The dev
stack's own `scheduler-insight` claims every due job on these queues, whichever organization
it is for, so a due job this test committed could be claimed by it first (ai-solution
status, *CI fix 2026-11-22*). The reads sign in as the AI account itself.

This suite fails rather than skips when Postgres is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final
from urllib.parse import urlsplit

import pytest
from insight_login import DEV_INSIGHT_PASSWORD, INSIGHT_ACCOUNT, insight_dsn
from live import require_stack
from nexovue_platform.fixes.records import FIX_QUEUE
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from nexovue_api import insight_account

DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
WORKER: Final = "fix-grants-test"
_ADD_ORGANIZATION: Final = text(
    "INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Grants')"
)
_ADD_JOB: Final = text(
    """
    INSERT INTO jobs (queue, task, kwargs, org_id, max_attempts, run_after,
                      soft_limit_seconds, hard_limit_seconds)
    VALUES (:queue, :task, '{}', :org, 2, now() - interval '1 second', 20, 30)
    RETURNING id
    """
)
_CLAIM: Final = text("SELECT id FROM insight_claim_jobs(:worker, :queues, 10, 30)")
_ACT_AS_AI: Final = text(f"SET LOCAL ROLE {INSIGHT_ACCOUNT}")
_STOP_ACTING: Final = text("RESET ROLE")


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


@asynccontextmanager
async def _unseen(install: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A transaction on the install's account, rolled back: no other session, the stack's
    workers included, ever sees what is written in it."""
    async with install.connect() as db:
        transaction = await db.begin()
        try:
            yield db
        finally:
            await transaction.rollback()


async def _add_job(db: AsyncConnection, queue: str) -> uuid.UUID:
    """A due job on `queue` for an organization of its own, as the install's account."""
    org = uuid.uuid4()
    await db.execute(_ADD_ORGANIZATION, {"id": org, "slug": f"grants-{org.hex[:12]}"})
    made = await db.scalar(
        _ADD_JOB, {"queue": queue, "task": f"fix-grants-test.{queue}", "org": org}
    )
    assert isinstance(made, uuid.UUID)
    return made


async def _claimed(db: AsyncConnection, queues: list[str]) -> set[uuid.UUID]:
    return set((await db.execute(_CLAIM, {"worker": WORKER, "queues": queues})).scalars())


async def test_the_ai_account_claims_retries_and_finishes_an_insight_fix_job(
    install: AsyncEngine,
) -> None:
    async with _unseen(install) as db:
        job = await _add_job(db, FIX_QUEUE)
        await db.execute(_ACT_AS_AI)

        assert job in await _claimed(db, [FIX_QUEUE])
        await db.execute(
            text("SELECT insight_retry_job(:id, :worker, 0, 'one more attempt')"),
            {"id": job, "worker": WORKER},
        )
        assert job in await _claimed(db, [FIX_QUEUE])
        await db.execute(
            text("SELECT insight_finish_job(:id, :worker)"), {"id": job, "worker": WORKER}
        )

        await db.execute(_STOP_ACTING)
        left = await db.scalar(text("SELECT count(*) FROM jobs WHERE id = :id"), {"id": job})
    assert left == 0


async def test_the_claim_still_matches_no_queue_outside_the_ai_role(
    install: AsyncEngine,
) -> None:
    async with _unseen(install) as db:
        light = await _add_job(db, "light")
        await db.execute(_ACT_AS_AI)

        claimed = await _claimed(db, ["light", FIX_QUEUE])

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


# Step 4 (T4.2, AC6): what the proposal job reads and writes of a source row, and no more.
SOURCE_READS: Final = (
    ("finding_fixes", "connection_repository_id, folder, round, retry_code"),
    ("finding_fix_source_packs", "fix_id, org_id, files, frames, hint"),
    (
        "fix_source_consents",
        "provider_base_url, provider_format, key_id, model, folder_scope, "
        "extra_excluded_paths, withdrawn_at",
    ),
)
SOURCE_NEVER_READ: Final = (
    ("finding_fixes", "files_read"),
    ("finding_fixes", "edits"),
    ("finding_fixes", "requested_by"),
    ("finding_fixes", "path_candidates"),
    ("finding_fixes", "files_digest"),
    ("fix_source_consents", "consented_by"),
    ("fix_source_consents", "allowed_config_paths"),
    ("finding_fix_checks", "patch"),
    ("service_code_locations", "folder"),
    ("github_user_links", "github_login"),
)


@pytest.mark.parametrize(("table", "columns"), SOURCE_READS)
async def test_ac6_the_ai_account_reads_what_the_proposal_needs(
    as_insight: AsyncEngine, table: str, columns: str
) -> None:
    async with as_insight.connect() as db:
        await db.execute(text(f"SELECT {columns} FROM {table} LIMIT 1"))


@pytest.mark.parametrize(("table", "column"), SOURCE_NEVER_READ)
async def test_ac6_the_ai_account_never_reads_the_listing_the_edits_or_a_person(
    as_insight: AsyncEngine, table: str, column: str
) -> None:
    async with as_insight.connect() as db:
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text(f"SELECT {column} FROM {table} LIMIT 1"))


async def test_ac88_the_ai_account_may_execute_the_more_files_function_only(
    install: AsyncEngine, as_insight: AsyncEngine
) -> None:
    async with install.connect() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT has_function_privilege(:account,
                               'fix_request_more_files(uuid, uuid)', 'EXECUTE') AS insight,
                           has_function_privilege('public',
                               'fix_request_more_files(uuid, uuid)', 'EXECUTE') AS anyone
                    """
                ),
                {"account": insight_account.INSIGHT_ACCOUNT},
            )
        ).one()
    assert rows.insight and not rows.anyone
    async with as_insight.connect() as db:
        # A row that does not exist moves nothing, and the call itself is allowed.
        moved = await db.scalar(
            text("SELECT fix_request_more_files(CAST(:id AS uuid), CAST(:id AS uuid))"),
            {"id": str(uuid.uuid4())},
        )
    assert moved is False


async def test_ac6_the_ai_account_writes_no_source_column_but_its_own(
    as_insight: AsyncEngine,
) -> None:
    async with as_insight.connect() as db:
        for column in ("files_read", "files_digest", "folder", "round", "candidates_from"):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with db.begin_nested():
                    await db.execute(text(f"UPDATE finding_fixes SET {column} = NULL WHERE false"))
