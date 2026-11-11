"""The AI account holds exactly its list, read back from the catalog (F3 plan T3.3; AC43,
AC45, AC71).

The list below is written out, not taken from `insight_account.INSIGHT_GRANTS`: a check
that read the code under test would agree with it when both were wrong. Every table and
column in `public` is asked about, with Postgres's own `has_*_privilege`, which counts what
`PUBLIC` holds too, so a right reaching the account any way at all shows here.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import pytest
from insight_login import DEV_INSIGHT_PASSWORD, INSIGHT_ACCOUNT, as_account
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_api import insight_account

DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

TABLE_PRIVILEGES: Final = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
)
COLUMN_PRIVILEGES: Final = ("SELECT", "INSERT", "UPDATE", "REFERENCES")

# Table-level rights: the only ones the account holds on whole tables.
EXPECTED_TABLE_RIGHTS: Final = {
    ("ai_project_opt_outs", "SELECT"),
    ("ai_runs", "SELECT"),
    ("ai_runs", "INSERT"),
    ("ai_usage_monthly", "SELECT"),
    ("ai_usage_monthly", "INSERT"),
}
# Column rights where there is no table right (design, *The Postgres account*).
EXPECTED_COLUMN_RIGHTS: Final = {
    ("finding_explanations", "SELECT"): {
        "id",
        "org_id",
        "project_id",
        "finding_id",
        "version",
        "model_input",
        "wording_state",
        "wording_generation",
        "wording_changed_at",
    },
    ("finding_explanations", "UPDATE"): {
        "wording",
        "wording_state",
        "wording_reason",
        "wording_changed_at",
        "wording_from_version",
    },
    ("ai_connections", "SELECT"): {
        "id",
        "org_id",
        "wire_format",
        "base_url",
        "model",
        "region_label",
        "key_id",
        # F3 Step 4: the sealed key, which the connection test and the wording job open.
        "sealed_key",
        "call_seconds",
        "monthly_token_limit",
        "enabled",
        "off_reason",
        "checked_at",
        "checked_ok",
        "checked_fingerprint",
        "consented_base_url",
        "consented_fingerprint",
        "consented_schema",
    },
    ("ai_connections", "UPDATE"): {"checked_at", "checked_ok", "checked_fingerprint"},
    ("projects", "SELECT"): {"id", "deleted_at"},
    ("ai_runs", "UPDATE"): {
        "input_tokens",
        "output_tokens",
        "tokens_estimated",
        "outcome",
        "finished_at",
    },
    ("ai_usage_monthly", "UPDATE"): {"used_tokens", "reserved_tokens"},
}
EXPECTED_FUNCTIONS: Final = {
    "insight_claim_jobs(text,integer,integer)",
    "insight_retry_job(uuid,text,double precision,text)",
    "insight_finish_job(uuid,text)",
    "insight_store_check_result(uuid,uuid,jsonb,double precision)",
}

_TABLES: Final = text(
    "SELECT c.relname FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm') ORDER BY 1"
)
_TABLE_RIGHT: Final = text("SELECT has_table_privilege(:account, :table, :privilege)")
_COLUMNS_WITH_RIGHT: Final = text(
    """
    SELECT attname FROM pg_attribute
    WHERE attrelid = CAST(:table AS regclass) AND attnum > 0 AND NOT attisdropped
      AND has_column_privilege(:account, attrelid, attnum, :privilege)
    """
)
# Functions in `public` that are not an extension's own (pgvector's are plain arithmetic).
_EXECUTABLE: Final = text(
    """
    SELECT replace(p.oid::regprocedure::text, ', ', ',') FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND NOT EXISTS (SELECT 1 FROM pg_depend AS d WHERE d.objid = p.oid AND d.deptype = 'e')
      AND has_function_privilege(:account, p.oid, 'EXECUTE')
    """
)


def _reachable() -> bool:
    parts = urlsplit(DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="the AI account grants suite",
    remedy="start the dev stack so Postgres answers (`just dev`)",
)


@pytest.fixture
def password(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(insight_account.PASSWORD_VARIABLE, DEV_INSIGHT_PASSWORD)
    return DEV_INSIGHT_PASSWORD


@pytest.fixture
async def engine(password: str) -> AsyncIterator[AsyncEngine]:
    said: list[str] = []
    assert await insight_account.provision(ASYNC_DSN, say=said.append), said
    built = create_async_engine(ASYNC_DSN)
    yield built
    await built.dispose()


async def _rights(
    engine: AsyncEngine,
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], set[str]]]:
    """Every table right and every column right the account holds, from the catalog."""
    tables: set[tuple[str, str]] = set()
    columns: dict[tuple[str, str], set[str]] = {}
    async with engine.connect() as connection:
        names = (await connection.execute(_TABLES)).scalars().all()
        for table in names:
            for privilege in TABLE_PRIVILEGES:
                held = await connection.scalar(
                    _TABLE_RIGHT,
                    {
                        "account": INSIGHT_ACCOUNT,
                        "table": f"public.{table}",
                        "privilege": privilege,
                    },
                )
                if held:
                    tables.add((table, privilege))
            for privilege in COLUMN_PRIVILEGES:
                if (table, privilege) in tables:
                    continue
                found = set(
                    (
                        await connection.execute(
                            _COLUMNS_WITH_RIGHT,
                            {
                                "account": INSIGHT_ACCOUNT,
                                "table": f"public.{table}",
                                "privilege": privilege,
                            },
                        )
                    ).scalars()
                )
                if found:
                    columns[(table, privilege)] = found
    return tables, columns


async def test_the_account_holds_exactly_its_list(engine: AsyncEngine) -> None:
    """AC43: column by column, nothing more, and no table-level `UPDATE` or `DELETE`."""
    tables, columns = await _rights(engine)

    assert tables == EXPECTED_TABLE_RIGHTS
    assert columns == EXPECTED_COLUMN_RIGHTS
    assert not {right for right in tables if right[1] in ("UPDATE", "DELETE", "TRUNCATE")}
    async with engine.connect() as connection:
        functions = set(
            (await connection.execute(_EXECUTABLE, {"account": INSIGHT_ACCOUNT})).scalars()
        )
    assert functions == EXPECTED_FUNCTIONS


async def test_the_account_cannot_read_the_real_names_or_the_evidence(engine: AsyncEngine) -> None:
    """AC45."""
    async with engine.connect() as connection:
        for column in ("evidence", "aliases", "summary", "likely_cause"):
            held = await connection.scalar(
                text(
                    "SELECT has_column_privilege(:account, 'finding_explanations', :column, "
                    "'SELECT')"
                ),
                {"account": INSIGHT_ACCOUNT, "column": column},
            )
            assert held is False, column


async def test_the_account_is_a_plain_login_with_a_limit_and_timeouts(engine: AsyncEngine) -> None:
    """AC43: a connection limit, both timeouts, no `TEMP`, no `CREATE`, no memberships."""
    async with engine.connect() as connection:
        role = (
            await connection.execute(
                text(
                    "SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolinherit, "
                    "rolreplication, rolbypassrls, rolconnlimit, rolconfig, "
                    "(SELECT count(*) FROM pg_auth_members WHERE member = r.oid) AS memberships, "
                    "has_database_privilege(rolname, current_database(), 'TEMP') AS temp, "
                    "has_database_privilege(rolname, current_database(), 'CREATE') AS create_db, "
                    "has_schema_privilege(rolname, 'public', 'CREATE') AS create_schema "
                    "FROM pg_roles AS r WHERE rolname = :account"
                ),
                {"account": INSIGHT_ACCOUNT},
            )
        ).one()

    assert role.rolcanlogin
    assert not (role.rolsuper or role.rolcreaterole or role.rolcreatedb or role.rolinherit)
    assert not (role.rolreplication or role.rolbypassrls)
    assert role.rolconnlimit == insight_account.CONNECTION_LIMIT
    assert set(role.rolconfig) == {
        f"statement_timeout={insight_account.STATEMENT_TIMEOUT}",
        f"idle_in_transaction_session_timeout={insight_account.IDLE_IN_TRANSACTION_TIMEOUT}",
    }
    assert role.memberships == 0
    assert not (role.temp or role.create_db or role.create_schema)


async def test_a_right_given_by_hand_is_taken_back_by_the_next_setup(
    engine: AsyncEngine, password: str
) -> None:
    """`setup` takes everything away first, so its list is all the account has after it.
    On `dashboards`, which the account suite beside this one never asks about, so a run of
    both at once cannot see the stray right."""
    async with engine.begin() as connection:
        await connection.execute(text(f"GRANT SELECT, UPDATE ON dashboards TO {INSIGHT_ACCOUNT}"))

    assert await insight_account.provision(ASYNC_DSN, say=lambda _line: None)

    tables, _ = await _rights(engine)
    assert tables == EXPECTED_TABLE_RIGHTS


async def test_setup_without_the_password_says_so_and_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC71: no account, no failure; only the AI role cannot start."""
    monkeypatch.delenv(insight_account.PASSWORD_VARIABLE, raising=False)
    said: list[str] = []

    assert await insight_account.provision(ASYNC_DSN, say=said.append) is False
    assert said and "POSTGRES_INSIGHT_PASSWORD is not set" in said[0]


async def test_setup_under_an_account_that_cannot_manage_roles_warns_and_goes_on(
    engine: AsyncEngine, password: str
) -> None:
    """AC71: a managed Postgres's account with no `CREATEROLE` (nor rights over the AI
    account) is refused, and `setup` says what to do instead of failing."""
    probe = f"insight_setup_probe_{uuid.uuid4().hex[:12]}"
    async with engine.begin() as connection:
        await connection.execute(text(f"CREATE ROLE {probe} LOGIN PASSWORD 'probe'"))
        # Only so the refusal comes from managing the role, not from the setup's own lock.
        await connection.execute(
            text(f"GRANT EXECUTE ON FUNCTION pg_advisory_xact_lock(bigint) TO {probe}")
        )
    said: list[str] = []
    try:
        probe_dsn = as_account(DSN, probe, "probe").replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
        ready = await insight_account.provision(probe_dsn, say=said.append)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP OWNED BY {probe}"))
            await connection.execute(text(f"DROP ROLE {probe}"))

    assert ready is False
    assert len(said) == 1 and "refused to prepare nexovue_insight" in said[0]
    assert "permission denied" in said[0]
    assert password not in said[0]


async def test_a_right_no_grant_fences_is_seen(engine: AsyncEngine) -> None:
    """The read `setup` relies on, on a throwaway role, so the shared account is never
    changed while another suite signs in as it."""
    probe = f"insight_held_probe_{uuid.uuid4().hex[:12]}"
    async with engine.begin() as connection:
        await connection.execute(text(f"CREATE ROLE {probe} CREATEDB"))
        try:
            held = await connection.scalar(insight_account._HELD_TOO_MUCH, {"account": probe})
            plain = await connection.scalar(
                insight_account._HELD_TOO_MUCH, {"account": INSIGHT_ACCOUNT}
            )
        finally:
            await connection.execute(text(f"DROP ROLE {probe}"))

    assert held is True
    assert plain is False


async def test_setup_will_not_call_an_over_privileged_account_ready(
    password: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`setup` cannot take such a right back without being a superuser, so it says so and
    does not call the account ready. The read is replaced by one that finds it."""
    monkeypatch.setattr(insight_account, "_HELD_TOO_MUCH", text("SELECT true OR :account = ''"))
    said: list[str] = []

    assert await insight_account.provision(ASYNC_DSN, say=said.append) is False
    assert "holds a right no grant can fence" in said[-1]
