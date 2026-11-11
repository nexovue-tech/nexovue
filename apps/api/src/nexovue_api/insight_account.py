"""The AI role's own Postgres account, `nexovue_insight` (F3, design *The Postgres account*).

`setup` runs `provision` after every migration, and `nexovue setup grants` runs it alone. A
restore drops every grant (`restore-postgres.sh --no-privileges`), and `nexovue restore`
ends in `start`, which runs `setup`, so the grants come back without a step of their own.

What the account may do is `INSIGHT_GRANTS`, and nothing else: everything it held is taken
away first, so a grant removed here is removed from a running install too. It holds no
right on `jobs` or `job_results`. Postgres lets anyone holding `UPDATE`, `DELETE`,
`TRUNCATE` or `MAINTAIN` on a table `LOCK` it, so the alert queue is reached only through
the four functions migration 0070 created, each limited to the `insight` queue.

**Beyond tables.** Every account may take advisory locks (beat's leader lock is one), call
`pg_notify` and make temporary tables. Those are taken from `PUBLIC` and given back to the
install account. A managed Postgres may refuse that; then `setup` says what is left and
finishes, because AI is optional and nothing else depends on this step (AC71).

**Not bounded here (status, *Open decisions*).** The timeouts are the account's defaults,
and any session may change its own; and the `NOTIFY` command needs no privilege at all.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Final

from sqlalchemy import TextClause, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine

INSIGHT_ACCOUNT: Final = "nexovue_insight"
PASSWORD_VARIABLE: Final = "POSTGRES_INSIGHT_PASSWORD"
# One process with four children and its pools needs well under this (A58).
CONNECTION_LIMIT: Final = 20
# The account's own statements are short reads and single-row writes; the provider call
# happens outside any transaction (design, *The wording decision and run*). Guesses.
STATEMENT_TIMEOUT: Final = "10s"
IDLE_IN_TRANSACTION_TIMEOUT: Final = "30s"

# Two `setup`s, or `setup` and a test, granting at once would collide on the catalog
# ("tuple concurrently updated"); each step holds this for its transaction.
_PROVISION_LOCK_KEY: Final = 7_340_211_068

# The four functions of migration 0070, by signature.
INSIGHT_FUNCTIONS: Final = (
    "insight_claim_jobs(text, integer, integer)",
    "insight_retry_job(uuid, text, double precision, text)",
    "insight_finish_job(uuid, text)",
    "insight_store_check_result(uuid, uuid, jsonb, double precision)",
)

# What the account may do (design, *The Postgres account*). Column lists, never a table
# grant of UPDATE or DELETE. `test_insight_grants_live.py` reads the catalog back.
INSIGHT_GRANTS: Final = (
    # Bound by `org_id` in every statement. All the facts it may see is `model_input`;
    # the real names (`aliases`) and the evidence stay out of reach (AC45).
    "GRANT SELECT (id, org_id, project_id, finding_id, version, model_input, wording_state, "
    "wording_generation, wording_changed_at) ON finding_explanations TO nexovue_insight",
    "GRANT UPDATE (wording, wording_state, wording_reason, wording_changed_at, "
    "wording_from_version) ON finding_explanations TO nexovue_insight",
    # Everything but who consented, when, and the prices; it may record a test's result. The
    # sealed key, which it opens with `AI_SECRET_KEY` for the address it is bound to.
    "GRANT SELECT (id, org_id, wire_format, base_url, model, region_label, key_id, sealed_key, "
    "call_seconds, monthly_token_limit, enabled, off_reason, checked_at, checked_ok, "
    "checked_fingerprint, consented_base_url, consented_fingerprint, consented_schema) "
    "ON ai_connections TO nexovue_insight",
    "GRANT UPDATE (checked_at, checked_ok, checked_fingerprint) "
    "ON ai_connections TO nexovue_insight",
    "GRANT SELECT ON ai_project_opt_outs TO nexovue_insight",
    # So a run can re-check that its project still exists before it spends.
    "GRANT SELECT (id, deleted_at) ON projects TO nexovue_insight",
    # A run's address and purpose stay as written; only its ending may change (AC72).
    "GRANT SELECT, INSERT ON ai_runs TO nexovue_insight",
    "GRANT UPDATE (input_tokens, output_tokens, tokens_estimated, outcome, finished_at) "
    "ON ai_runs TO nexovue_insight",
    "GRANT SELECT, INSERT ON ai_usage_monthly TO nexovue_insight",
    "GRANT UPDATE (used_tokens, reserved_tokens) ON ai_usage_monthly TO nexovue_insight",
    *(
        f"GRANT EXECUTE ON FUNCTION {signature} TO nexovue_insight"
        for signature in INSIGHT_FUNCTIONS
    ),
)

# Built-in functions every account may call that reach past its own tables: advisory locks,
# among them beat's leader lock, and `pg_notify`, whose commit lock other commits wait on.
# Every overload, read from the catalog, so a version adding one is covered.
SHARED_FUNCTIONS: Final = text(
    """
    SELECT p.oid::regprocedure::text AS signature
    FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'pg_catalog'
      AND (p.proname LIKE 'pg\\_advisory\\_%' OR p.proname LIKE 'pg\\_try\\_advisory\\_%'
           OR p.proname = 'pg_notify')
    ORDER BY 1
    """
)

_ROLE_EXISTS: Final = text("SELECT 1 FROM pg_roles WHERE rolname = :account")
# Utility statements take no bound values, so the server quotes them.
_CREATE_ROLE: Final = text("SELECT format('CREATE ROLE %I', CAST(:account AS text))")
# Only what an account with `CREATEROLE` may state: Postgres refuses even `NOSUPERUSER` from
# anyone but a superuser (measured on 18). The rest is read back by `_HELD_TOO_MUCH`.
_ALTER_ROLE: Final = text(
    """
    SELECT format(
        'ALTER ROLE %I WITH LOGIN NOINHERIT CONNECTION LIMIT %s PASSWORD %L',
        CAST(:account AS text), CAST(:limit AS integer), CAST(:password AS text)
    )
    """
)
_HELD_TOO_MUCH: Final = text(
    "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls "
    "FROM pg_roles WHERE rolname = :account"
)
_RESET_ROLE_DEFAULTS: Final = text(
    "SELECT format('ALTER ROLE %I RESET ALL', CAST(:account AS text))"
)
_SET_ROLE_DEFAULT: Final = text(
    "SELECT format('ALTER ROLE %I SET %I = %L', "
    "CAST(:account AS text), CAST(:setting AS text), CAST(:value AS text))"
)
_MEMBERSHIPS: Final = text(
    """
    SELECT format('REVOKE %I FROM %I', granted.rolname, member.rolname)
    FROM pg_auth_members AS m
    JOIN pg_roles AS granted ON granted.oid = m.roleid
    JOIN pg_roles AS member ON member.oid = m.member
    WHERE member.rolname = :account
    """
)
_TAKE_EVERYTHING: Final = (
    "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM nexovue_insight",
    "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM nexovue_insight",
    "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM nexovue_insight",
    *(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC" for signature in INSIGHT_FUNCTIONS),
)
_DATABASE: Final = text("SELECT current_database(), current_user")
# Beat's leader lock is an advisory lock: an account still holding this function could take
# it. Asked only where the account exists, since the function errors on an unknown role.
_BEAT_LOCK_EXPOSED: Final = text(
    "SELECT has_function_privilege(:account, 'pg_try_advisory_lock(bigint)', 'EXECUTE')"
)

Say = Callable[[str], None]


async def provision(dsn: str | None = None, *, say: Say = print) -> bool:
    """Create or update the account and apply its grants. True when the account is ready.

    Never raises for a refusal: AI is optional, so a missing password or a refused right
    is said plainly and `setup` goes on (AC71). A refused revoke from `PUBLIC` still leaves
    the account ready, with its remaining risk said (AC43)."""
    password = os.environ.get(PASSWORD_VARIABLE, "")
    if not password:
        say(
            f"ai account: {PASSWORD_VARIABLE} is not set, so {INSIGHT_ACCOUNT} was not "
            "created; scheduler-insight cannot start, and nothing else is affected"
        )
        return False
    engine = create_async_engine(dsn or os.environ["POSTGRES_DSN"], pool_size=1, max_overflow=0)
    try:
        if not await _prepare_account(engine, password, say):
            return False
        await _take_shared_rights(engine, say)
        return True
    finally:
        await engine.dispose()


async def _prepare_account(engine: AsyncEngine, password: str, say: Say) -> bool:
    """The account and its grants; False, said, when Postgres refuses or it holds too much."""
    try:
        async with engine.begin() as connection:
            await _serialised(connection)
            await _ensure_account(connection, password)
            if await connection.scalar(_HELD_TOO_MUCH, {"account": INSIGHT_ACCOUNT}):
                say(
                    f"ai account: {INSIGHT_ACCOUNT} holds a right no grant can fence "
                    "(superuser, role or database creation, replication or bypassing row "
                    "security); remove it by hand. scheduler-insight must not run until then"
                )
                return False
        async with engine.begin() as connection:
            await _serialised(connection)
            await _apply_grants(connection)
    except DBAPIError as error:
        say(
            f"ai account: Postgres refused to prepare {INSIGHT_ACCOUNT} "
            f"({_reason(error)}); scheduler-insight cannot start, and nothing else is "
            "affected. Give the install account CREATEROLE, or create the role by hand "
            "(docs/operations/ai.md)"
        )
        return False
    return True


async def _take_shared_rights(engine: AsyncEngine, say: Say) -> None:
    """Advisory locks, `pg_notify` and `TEMP` from `PUBLIC`; a refusal leaves the account
    ready, with what is left said (AC43)."""
    try:
        async with engine.begin() as connection:
            await _serialised(connection)
            await _revoke_shared_rights(connection)
    except DBAPIError as error:
        say(
            f"ai account: ready, but Postgres refused to take advisory locks, pg_notify "
            f"and temporary tables from PUBLIC ({_reason(error)}). A broken-into AI "
            "process could take beat's leader lock and stop every schedule until it is "
            "stopped (docs/operations/ai.md)"
        )
        return
    say(f"ai account: {INSIGHT_ACCOUNT} ready, with its grants and nothing else")


async def _serialised(connection: AsyncConnection) -> None:
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PROVISION_LOCK_KEY}
    )


async def _ensure_account(connection: AsyncConnection, password: str) -> None:
    account = {"account": INSIGHT_ACCOUNT}
    if (await connection.execute(_ROLE_EXISTS, account)).first() is None:
        await _run_formatted(connection, _CREATE_ROLE, account)
    await _run_formatted(
        connection, _ALTER_ROLE, account | {"limit": CONNECTION_LIMIT, "password": password}
    )
    # Only these two settings stay, whatever was set by hand before.
    await _run_formatted(connection, _RESET_ROLE_DEFAULTS, account)
    for setting, value in (
        ("statement_timeout", STATEMENT_TIMEOUT),
        ("idle_in_transaction_session_timeout", IDLE_IN_TRANSACTION_TIMEOUT),
    ):
        await _run_formatted(
            connection, _SET_ROLE_DEFAULT, account | {"setting": setting, "value": value}
        )


async def _apply_grants(connection: AsyncConnection) -> None:
    """Everything the account held is taken first, so what is left is exactly the list."""
    for revoke in (await connection.execute(_MEMBERSHIPS, {"account": INSIGHT_ACCOUNT})).scalars():
        await connection.exec_driver_sql(revoke)
    for statement in (*_TAKE_EVERYTHING, *INSIGHT_GRANTS):
        await connection.exec_driver_sql(statement)


async def _revoke_shared_rights(connection: AsyncConnection) -> None:
    """Given to the install account first, so taking them from `PUBLIC` cannot take them
    from the services that use them (beat's leader lock, detection's locks, a wake)."""
    database, install_account = (await connection.execute(_DATABASE)).one()
    signatures = (await connection.execute(SHARED_FUNCTIONS)).scalars().all()
    quoted_account = await _identifier(connection, install_account)
    quoted_database = await _identifier(connection, database)
    for signature in signatures:
        await connection.exec_driver_sql(
            f"GRANT EXECUTE ON FUNCTION {signature} TO {quoted_account}"
        )
        await connection.exec_driver_sql(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
    await connection.exec_driver_sql(
        f"GRANT TEMP ON DATABASE {quoted_database} TO {quoted_account}"
    )
    await connection.exec_driver_sql(f"REVOKE TEMP ON DATABASE {quoted_database} FROM PUBLIC")
    # Postgres 15's default; said again for a database created before it.
    await connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


async def _run_formatted(
    connection: AsyncConnection, statement: TextClause, values: Mapping[str, object]
) -> None:
    """Run the statement the server built from `values`, quoted by `format`."""
    formatted = (await connection.execute(statement, values)).scalar_one()
    await connection.exec_driver_sql(str(formatted))


async def _identifier(connection: AsyncConnection, name: str) -> str:
    return str(
        (await connection.execute(text("SELECT quote_ident(:name)"), {"name": name})).scalar_one()
    )


def _reason(error: DBAPIError) -> str:
    """Postgres's own one line, never the statement, which may hold the password."""
    original = getattr(error, "orig", None)
    return str(original).splitlines()[0] if original is not None else type(error).__name__


async def account_exists(session: AsyncSession) -> bool:
    """Whether setup has made the AI account on this server (F3, AC71)."""
    return await session.scalar(_ROLE_EXISTS, {"account": INSIGHT_ACCOUNT}) is not None


async def beat_lock_exposed(session: AsyncSession) -> bool:
    """Whether the AI account may still take beat's leader lock. Ask only once
    `account_exists` holds."""
    return bool(await session.scalar(_BEAT_LOCK_EXPOSED, {"account": INSIGHT_ACCOUNT}))
