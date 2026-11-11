"""The fence's spike (F3 plan T3.1): the AI account reaches the alert queue only through its
functions, cannot lock what alerts depend on, and cannot take beat's lock (AC43, AC44).

**What this proved on 2026-11-11** (status, *Validation*):

- A column-level `UPDATE` grant does **not** permit `LOCK TABLE`, in any mode: Postgres
  refuses `ACCESS EXCLUSIVE`, `ROW EXCLUSIVE` and even `ACCESS SHARE` on
  `finding_explanations`. (A table-level `INSERT` does permit `ROW EXCLUSIVE`, which is
  why the account holds none on `jobs` or `job_results`.)
- Taking advisory locks, `pg_notify` and `TEMP` from `PUBLIC` holds against this account,
  and the install account keeps them.
- The functions and the shared statements agree on one table of cases.

**And two things the design assumed that are false**, asserted here as they are so a
change in Postgres shows (status, *Open decisions* 7): the `NOTIFY` command needs no
privilege, so revoking `pg_notify` does not stop it; and the account's timeouts are only
its defaults, which any session may `SET` to zero, so they bound a working process and
not a broken-into one.

**Safe beside the dev stack's own `scheduler-insight`,** which claims from the same queue:
every parity case writes its rows inside a transaction that is rolled back, so no other
session ever sees them, and the role is taken with `SET LOCAL ROLE` inside it. The one
case that needs a second session (a locked row) commits a row that is not yet due, and
locks it before it is.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

import asyncpg
import pytest
from insight_login import DEV_INSIGHT_PASSWORD, INSIGHT_ACCOUNT, insight_dsn
from live import require_stack
from nexovue_platform.evidence import INSIGHT_QUEUE
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from nexovue_api import insight_account
from nexovue_scheduler.jobs import queue, results
from nexovue_scheduler.jobs.queue import LEASE_GRACE_SECONDS, QueueStatements
from nexovue_scheduler.leader import BEAT_LEADER_LOCK

DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
INSIGHT_DSN: Final = insight_dsn(DSN)
INSIGHT_ASYNC_DSN: Final = INSIGHT_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

HARD_LIMIT_SECONDS: Final = 90
LEASE_SECONDS: Final = HARD_LIMIT_SECONDS + LEASE_GRACE_SECONDS
# Far enough back that the dev stack's own due jobs, if any, sort after these.
_LONG_AGO: Final = "now() - interval '10 years'"
PERMISSION_DENIED: Final = "permission denied"


def _reachable() -> bool:
    parts = urlsplit(DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="the AI account suite",
    remedy="start the dev stack so Postgres answers (`just dev`)",
)


@pytest.fixture
async def install(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """The install's own account, prepared by the real `setup` step's AI part."""
    monkeypatch.setenv(insight_account.PASSWORD_VARIABLE, DEV_INSIGHT_PASSWORD)
    said: list[str] = []
    assert await insight_account.provision(ASYNC_DSN, say=said.append), said
    built = create_async_engine(ASYNC_DSN)
    yield built
    await built.dispose()


@pytest.fixture
async def as_insight(install: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """A real login as the AI account, after `install` has prepared it."""
    built = create_async_engine(INSIGHT_ASYNC_DSN, pool_size=1, max_overflow=0)
    yield built
    await built.dispose()


# ------------------------------------------------------------------ the parity table


@dataclass(frozen=True)
class Row:
    """One job a case writes. Times are seconds from the transaction's `now()`."""

    label: str
    run_after: str = _LONG_AGO
    lease_seconds: int | None = None
    leased_by: str | None = None
    expires_seconds: int | None = None
    attempts: int = 0
    max_attempts: int = 3
    queue_name: str = INSIGHT_QUEUE


@dataclass(frozen=True)
class Case:
    name: str
    rows: tuple[Row, ...]
    act: Callable[[AsyncConnection, QueueStatements, dict[str, uuid.UUID]], Awaitable[Any]]


async def _claim(
    connection: AsyncConnection, statements: QueueStatements, limit: int, worker: str = "w"
) -> list[uuid.UUID]:
    rows = await connection.execute(
        statements.claim,
        statements.claim_parameters(queues=[INSIGHT_QUEUE], worker=worker, limit=limit),
    )
    return [row.id for row in rows]


async def _retry(
    connection: AsyncConnection, statements: QueueStatements, job: uuid.UUID, worker: str
) -> None:
    await connection.execute(
        statements.retry, {"id": job, "worker": worker, "countdown": 60.0, "error": "boom"}
    )


async def _finish(
    connection: AsyncConnection, statements: QueueStatements, job: uuid.UUID, worker: str
) -> None:
    await connection.execute(statements.finish, {"id": job, "worker": worker})


def _claim_case(limit: int) -> Callable[..., Awaitable[Any]]:
    async def act(
        connection: AsyncConnection, statements: QueueStatements, ids: dict[str, uuid.UUID]
    ) -> Any:
        claimed = set(await _claim(connection, statements, limit))
        return sorted(label for label, job in ids.items() if job in claimed)

    return act


def _on(label: str, step: Callable[..., Awaitable[None]], worker: str) -> Callable[..., Any]:
    async def act(
        connection: AsyncConnection, statements: QueueStatements, ids: dict[str, uuid.UUID]
    ) -> None:
        await step(connection, statements, ids[label], worker)

    return act


def _held(label: str, queue_name: str = INSIGHT_QUEUE) -> Row:
    """A job worker `w` holds a live lease on, on its first attempt."""
    return Row(label, lease_seconds=300, leased_by="w", attempts=1, queue_name=queue_name)


CASES: Final = (
    Case(
        "a claim takes due jobs and lapsed leases, and nothing else",
        (
            Row("due"),
            Row("not_yet", run_after="now() + interval '1 hour'"),
            Row("leased", lease_seconds=300, leased_by="other"),
            Row("lapsed", lease_seconds=-10, leased_by="dead", attempts=1),
            Row("expired", expires_seconds=-1),
            Row("spent", attempts=1, max_attempts=1),
        ),
        _claim_case(limit=10),
    ),
    Case(
        "a claim takes the oldest first, and no more than its limit",
        (
            Row("third", run_after=f"{_LONG_AGO} + interval '3 seconds'"),
            Row("first", run_after=f"{_LONG_AGO} + interval '1 second'"),
            Row("second", run_after=f"{_LONG_AGO} + interval '2 seconds'"),
        ),
        _claim_case(limit=2),
    ),
    Case("its holder hands a job back", (_held("held"),), _on("held", _retry, "w")),
    Case("a stranger cannot hand it back", (_held("held"),), _on("held", _retry, "x")),
    Case("its holder finishes a job", (_held("held"),), _on("held", _finish, "w")),
    Case("a stranger cannot finish it", (_held("held"),), _on("held", _finish, "x")),
)

_INSERT_JOB: Final = """
    INSERT INTO jobs (queue, task, kwargs, run_after, lease_until, leased_by, expires_at,
                      attempts, max_attempts, soft_limit_seconds, hard_limit_seconds)
    VALUES (:queue, 'insight-account-test.noop', '{{}}'::jsonb, {run_after},
            CASE WHEN CAST(:lease AS integer) IS NULL THEN NULL
                 ELSE now() + make_interval(secs => CAST(:lease AS integer)) END,
            :leased_by,
            CASE WHEN CAST(:expires AS integer) IS NULL THEN NULL
                 ELSE now() + make_interval(secs => CAST(:expires AS integer)) END,
            :attempts, :max_attempts, 60, {hard})
    RETURNING id
"""
# What a job looks like after a case, relative to the transaction's own `now()`, so the
# result is the same whenever and wherever it runs.
_OBSERVE: Final = text(
    """
    SELECT id, attempts, leased_by,
           round(extract(epoch FROM lease_until - now()))::int AS lease_left,
           round(extract(epoch FROM run_after - now()))::int AS due_in,
           last_error
    FROM jobs WHERE id = ANY(:ids)
    """
)


async def _write(connection: AsyncConnection, rows: tuple[Row, ...]) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    for row in rows:
        statement = text(_INSERT_JOB.format(run_after=row.run_after, hard=HARD_LIMIT_SECONDS))
        ids[row.label] = (
            await connection.execute(
                statement,
                {
                    "queue": row.queue_name,
                    "lease": row.lease_seconds,
                    "leased_by": row.leased_by,
                    "expires": row.expires_seconds,
                    "attempts": row.attempts,
                    "max_attempts": row.max_attempts,
                },
            )
        ).scalar_one()
    return ids


async def _observe(connection: AsyncConnection, ids: dict[str, uuid.UUID]) -> dict[str, Any]:
    labels = {job: label for label, job in ids.items()}
    rows = await connection.execute(_OBSERVE, {"ids": list(ids.values())})
    seen = {
        labels[row.id]: (row.attempts, row.leased_by, row.lease_left, row.due_in, row.last_error)
        for row in rows
    }
    return {label: seen.get(label, "gone") for label in ids}


async def _run_case(engine: AsyncEngine, case: Case, *, as_function: bool) -> tuple[Any, Any]:
    """The case in one transaction, rolled back: its rows are never seen by anyone else."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            ids = await _write(connection, case.rows)
            if as_function:
                await connection.execute(text(f"SET LOCAL ROLE {INSIGHT_ACCOUNT}"))
                statements = queue.INSIGHT_STATEMENTS
            else:
                statements = queue.SHARED_STATEMENTS
            answer = await case.act(connection, statements, ids)
            await connection.execute(text("RESET ROLE"))
            return answer, await _observe(connection, ids)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_the_functions_do_what_the_shared_statements_do(
    install: AsyncEngine, case: Case
) -> None:
    """AC44's parity table: leases, attempts, order, limit, expiry and the lease fence."""
    through_statements = await _run_case(install, case, as_function=False)
    through_functions = await _run_case(install, case, as_function=True)

    assert through_functions == through_statements
    observed = through_functions[1]
    if case.name.startswith("a claim takes due"):
        assert through_functions[0] == ["due", "lapsed"]
        assert observed["due"] == (1, "w", LEASE_SECONDS, observed["due"][3], None)
    if case.name == "its holder hands a job back":
        assert observed["held"] == (1, None, None, 60, "boom")
    if case.name == "its holder finishes a job":
        assert observed["held"] == "gone"


async def test_a_locked_job_is_skipped_by_both(install: AsyncEngine) -> None:
    """`SKIP LOCKED`, which needs a second session: the row is committed before it is due,
    and locked before it becomes due, so no worker on the dev stack can take it first."""
    async with install.begin() as connection:
        [locked] = (
            await _write(connection, (Row("locked", run_after="now() + interval '2 seconds'"),))
        ).values()
    try:
        async with install.connect() as holder:
            hold = await holder.begin()
            await holder.execute(
                text("SELECT id FROM jobs WHERE id = :id FOR UPDATE"), {"id": locked}
            )
            await _wait_until_due(install, locked)
            for as_function in (False, True):
                async with install.connect() as connection:
                    transaction = await connection.begin()
                    try:
                        [control] = (await _write(connection, (Row("control"),))).values()
                        if as_function:
                            await connection.execute(text(f"SET LOCAL ROLE {INSIGHT_ACCOUNT}"))
                        statements = (
                            queue.INSIGHT_STATEMENTS if as_function else queue.SHARED_STATEMENTS
                        )
                        claimed = await _claim(connection, statements, limit=10)
                    finally:
                        await transaction.rollback()
                assert control in claimed, as_function
                assert locked not in claimed, as_function
            await hold.rollback()
    finally:
        async with install.begin() as connection:
            await connection.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": locked})


async def _wait_until_due(engine: AsyncEngine, job: uuid.UUID) -> None:
    for _ in range(50):
        async with engine.connect() as connection:
            due = await connection.scalar(
                text("SELECT run_after <= now() FROM jobs WHERE id = :id"), {"id": job}
            )
        if due:
            return
        await asyncio.sleep(0.2)
    raise AssertionError("the locked job never became due")


async def test_the_functions_touch_no_other_queue(install: AsyncEngine) -> None:
    """AC44: whatever it is asked, the account claims, retries and finishes `insight` jobs
    only, where the shared statements would reach any queue they were given."""
    rows = (
        Row("light_due", queue_name="light"),
        _held("light_held", "light"),
        _held("light_finished", "light"),
    )
    async with install.connect() as connection:
        transaction = await connection.begin()
        try:
            ids = await _write(connection, rows)
            before = await _observe(connection, ids)
            await connection.execute(text(f"SET LOCAL ROLE {INSIGHT_ACCOUNT}"))
            claimed = await _claim(connection, queue.INSIGHT_STATEMENTS, limit=100)
            await _retry(connection, queue.INSIGHT_STATEMENTS, ids["light_held"], "w")
            await _finish(connection, queue.INSIGHT_STATEMENTS, ids["light_finished"], "w")
            await connection.execute(text("RESET ROLE"))
            after = await _observe(connection, ids)
        finally:
            await transaction.rollback()

    assert not set(ids.values()) & set(claimed)
    assert after == before


async def _store_twice(engine: AsyncEngine, *, as_function: bool) -> list[Any]:
    """One request stored twice and another once; read back by request, kind and expiry."""
    first, second = uuid.uuid4(), uuid.uuid4()
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            org_id = (
                await connection.execute(
                    text("INSERT INTO organizations (slug, name) VALUES (:slug, 'x') RETURNING id"),
                    {"slug": f"insight-account-test-{uuid.uuid4().hex}"},
                )
            ).scalar_one()
            if as_function:
                await connection.execute(text(f"SET LOCAL ROLE {INSIGHT_ACCOUNT}"))
            for request, payload in ((first, "kept"), (first, "dropped"), (second, "other")):
                values = {
                    "request_id": str(request),
                    "org_id": str(org_id),
                    "payload": f'{{"said": "{payload}"}}',
                    "ttl_seconds": 300,
                }
                if as_function:
                    await connection.execute(results._STORE_AI_CHECK_RESULT, values)
                else:
                    await connection.execute(
                        results._STORE_RESULT, values | {"kind": results.AI_CHECK_KIND}
                    )
            await connection.execute(text("RESET ROLE"))
            stored = await connection.execute(
                text(
                    "SELECT request_id = :first AS is_first, kind, payload->>'said' AS said, "
                    "round(extract(epoch FROM expires_at - now()))::int AS ttl "
                    "FROM job_results WHERE request_id IN (:first, :second) ORDER BY 1 DESC"
                ),
                {"first": first, "second": second},
            )
            return [tuple(row) for row in stored]
        finally:
            await transaction.rollback()


async def test_a_check_result_is_stored_once_and_always_as_ai_check(install: AsyncEngine) -> None:
    """AC44's store case: the first result stands, and the function writes `kind` itself,
    so the account cannot write a Run now's or a send test's result."""
    through_function = await _store_twice(install, as_function=True)

    assert through_function == await _store_twice(install, as_function=False)
    assert through_function == [(True, "ai_check", "kept", 300), (False, "ai_check", "other", 300)]


# ---------------------------------------------------------------- what it cannot do


async def _refused(engine: AsyncEngine, statement: str) -> str:
    """The statement as the AI account, in its own transaction; Postgres's reason."""
    with pytest.raises(DBAPIError) as refusal:
        async with engine.begin() as connection:
            await connection.execute(text(statement))
    return str(refusal.value.orig)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM findings LIMIT 1",
        "SELECT * FROM jobs LIMIT 1",
        "SELECT * FROM job_results LIMIT 1",
        "SELECT * FROM notifications LIMIT 1",
        "SELECT * FROM audit_log LIMIT 1",
        "SELECT evidence FROM finding_explanations LIMIT 1",
        "SELECT aliases FROM finding_explanations LIMIT 1",
        "INSERT INTO jobs (queue, task, kwargs, max_attempts, soft_limit_seconds, "
        "hard_limit_seconds) VALUES ('light', 'x', '{}', 1, 1, 2)",
        "UPDATE jobs SET attempts = 0",
        "DELETE FROM job_results",
        "UPDATE ai_runs SET provider_host = 'https://elsewhere.example'",
        "DELETE FROM ai_runs",
    ],
)
async def test_the_account_is_refused_what_alerts_rest_on(
    as_insight: AsyncEngine, statement: str
) -> None:
    assert PERMISSION_DENIED in await _refused(as_insight, statement)


@pytest.mark.parametrize("table", ["jobs", "finding_explanations", "ai_runs", "ai_usage_monthly"])
@pytest.mark.parametrize("mode", ["ACCESS EXCLUSIVE", "EXCLUSIVE", "SHARE"])
async def test_the_account_cannot_lock_a_table(
    as_insight: AsyncEngine, table: str, mode: str
) -> None:
    """The spike's question: a column-level `UPDATE` grant does not count as the right to
    lock, and neither does `SELECT` with `INSERT` (AC43)."""
    assert PERMISSION_DENIED in await _refused(as_insight, f"LOCK TABLE {table} IN {mode} MODE")


async def test_the_account_cannot_take_beat_s_lock(as_insight: AsyncEngine) -> None:
    """AC43: taking it would leave every beat a follower, so nothing is scheduled."""
    for call in (
        f"SELECT pg_try_advisory_lock({BEAT_LEADER_LOCK})",
        f"SELECT pg_advisory_lock({BEAT_LEADER_LOCK})",
        f"SELECT pg_try_advisory_xact_lock({BEAT_LEADER_LOCK})",
        "SELECT pg_notify('nexovue_jobs', 'light')",
    ):
        assert PERMISSION_DENIED in await _refused(as_insight, call), call


async def test_no_advisory_or_notify_function_is_left_to_it_or_to_public(
    install: AsyncEngine,
) -> None:
    """Every overload, from the catalog: the revoke from `PUBLIC` held, and the install
    account, which beat and detection run as, kept each one."""
    async with install.connect() as connection:
        signatures = (await connection.execute(insight_account.SHARED_FUNCTIONS)).scalars().all()
        rows = await connection.execute(
            text(
                """
                SELECT s AS signature,
                       has_function_privilege(:account, s, 'EXECUTE') AS insight_may,
                       has_function_privilege(current_user, s, 'EXECUTE') AS install_may,
                       EXISTS (SELECT 1 FROM aclexplode(p.proacl) AS a
                               WHERE a.grantee = 0) AS public_may
                FROM unnest(CAST(:signatures AS text[])) AS s
                JOIN pg_proc AS p ON p.oid = CAST(s AS regprocedure)
                """
            ),
            {"account": INSIGHT_ACCOUNT, "signatures": list(signatures)},
        )
        found = rows.all()

    assert len(found) >= 10, "the catalog read found too few advisory functions to trust"
    assert [row.signature for row in found if row.insight_may or row.public_may] == []
    assert all(row.install_may for row in found)


async def test_the_account_has_no_temporary_tables(as_insight: AsyncEngine) -> None:
    assert PERMISSION_DENIED in await _refused(as_insight, "CREATE TEMP TABLE scratch (x int)")


async def test_the_account_stops_at_its_connection_limit(install: AsyncEngine) -> None:
    """AC43: past the limit Postgres refuses it. The dev stack's own `scheduler-insight` may
    hold some, so the refusal comes at the limit or before."""
    opened: list[asyncpg.Connection] = []
    try:
        with pytest.raises(asyncpg.TooManyConnectionsError, match="for role"):
            for _ in range(insight_account.CONNECTION_LIMIT + 1):
                opened.append(await asyncpg.connect(INSIGHT_DSN))
        assert len(opened) <= insight_account.CONNECTION_LIMIT
    finally:
        await asyncio.gather(*(connection.close() for connection in opened))


async def test_what_postgres_cannot_take_from_it_is_as_recorded(as_insight: AsyncEngine) -> None:
    """Not a fence: the two gaps the spike found, held here as they are (status, *Open
    decisions* 7). `NOTIFY` needs no privilege; and the timeouts are only defaults."""
    async with as_insight.connect() as connection:
        assert await connection.scalar(text("SHOW statement_timeout")) == (
            insight_account.STATEMENT_TIMEOUT
        )
        await connection.execute(text("NOTIFY insight_account_test, 'not refused'"))
        await connection.execute(text("SET statement_timeout = 0"))
        assert await connection.scalar(text("SHOW statement_timeout")) == "0"
        await connection.rollback()


async def test_no_row_policy_sits_on_the_alert_queue(install: AsyncEngine) -> None:
    """AC44: the fence is grants, not row-level security, so alerts never pass an AI rule."""
    async with install.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT relname, relrowsecurity, "
                "(SELECT count(*) FROM pg_policy WHERE polrelid = c.oid) AS policies "
                "FROM pg_class AS c WHERE relname IN ('jobs', 'job_results')"
            )
        )
        found = {row.relname: (row.relrowsecurity, row.policies) for row in rows}

    assert found == {"jobs": (False, 0), "job_results": (False, 0)}
