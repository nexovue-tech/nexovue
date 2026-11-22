"""A narrow role's own Postgres account, made and granted by `setup` (F3's pattern, ADR-040).

Two roles sign in as accounts of their own: `scheduler-insight` as `nexovue_insight`
(`insight_account`) and `scheduler-connections` as `nexovue_connections`
(`connections_account`). Each is a `RoleAccount`: its name, the variable its password comes
from, its limits, and its grants. `setup` runs `provision` after every migration, so a restore,
which drops every grant, ends with the grants back.

**What an account may do is its list, and nothing else.** Everything it held is taken away
first, so a grant removed from the list is removed from a running install too. No account
holds a right on `jobs` or `job_results`: Postgres lets anyone holding `INSERT`, `UPDATE`,
`DELETE`, `TRUNCATE` or `MAINTAIN` on a table `LOCK` it, and those two tables carry every
alert. Each reaches its own queue through `SECURITY DEFINER` functions a migration made.

**Beyond tables.** Every account may take advisory locks (beat's leader lock is one), call
`pg_notify` and make temporary tables. Those are taken from `PUBLIC` and given back to the
install account. A managed Postgres may refuse that; then `setup` says what is left and
finishes.

**Not bounded here.** The timeouts are the account's defaults, and any session may change its
own; and the `NOTIFY` command needs no privilege at all (F3's open decision 7).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final

from sqlalchemy import TextClause, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

Say = Callable[[str], None]

# Two `setup`s, or `setup` and a test, granting at once would collide on the catalog
# ("tuple concurrently updated"); each step holds this for its transaction, whichever account.
_PROVISION_LOCK_KEY: Final = 7_340_211_068


@dataclass(frozen=True, slots=True)
class RoleAccount:
    """One narrow role's account, and how `setup` speaks of it."""

    name: str
    password_variable: str
    connection_limit: int
    statement_timeout: str
    idle_in_transaction_timeout: str
    # The `SECURITY DEFINER` functions its migrations made, by signature; `EXECUTE` is taken
    # from `PUBLIC` and given to this account alone.
    functions: tuple[str, ...]
    grants: tuple[str, ...]
    # How `setup` names it, the service that signs in as it, what stops when it is missing,
    # what a broken-into process of it is called, and where an operator reads more.
    label: str
    service: str
    when_missing: str
    process: str
    guide: str


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

ROLE_EXISTS: Final = text("SELECT 1 FROM pg_roles WHERE rolname = :account")
# Utility statements take no bound values, so the server quotes them.
_CREATE_ROLE: Final = text("SELECT format('CREATE ROLE %I', CAST(:account AS text))")
# Only what an account with `CREATEROLE` may state: Postgres refuses even `NOSUPERUSER` from
# anyone but a superuser (measured on 18). The rest is read back by `HELD_TOO_MUCH`.
_ALTER_ROLE: Final = text(
    """
    SELECT format(
        'ALTER ROLE %I WITH LOGIN NOINHERIT CONNECTION LIMIT %s PASSWORD %L',
        CAST(:account AS text), CAST(:limit AS integer), CAST(:password AS text)
    )
    """
)
HELD_TOO_MUCH: Final = text(
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
_DATABASE: Final = text("SELECT current_database(), current_user")
# Beat's leader lock is an advisory lock: an account still holding this function could take
# it. Asked only where the account exists, since the function errors on an unknown role.
_BEAT_LOCK_EXPOSED: Final = text(
    "SELECT has_function_privilege(:account, 'pg_try_advisory_lock(bigint)', 'EXECUTE')"
)


def function_grants(account: str, functions: tuple[str, ...]) -> tuple[str, ...]:
    """`EXECUTE` on each of an account's functions, for the end of its grant list."""
    return tuple(f"GRANT EXECUTE ON FUNCTION {signature} TO {account}" for signature in functions)


def take_everything(account: RoleAccount) -> tuple[str, ...]:
    """Every right the account could hold in `public`, and `PUBLIC`'s on its functions."""
    return (
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {account.name}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {account.name}",
        f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {account.name}",
        *(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC" for signature in account.functions),
    )


async def prepare_account(
    engine: AsyncEngine,
    account: RoleAccount,
    password: str,
    say: Say,
    apply_grants: Callable[[AsyncConnection], Awaitable[None]] | None = None,
) -> bool:
    """The account and its grants; False, said, when Postgres refuses or it holds too much.

    `apply_grants` replaces the plain list where an account's grants depend on the database
    (the AI account's meaning-search columns)."""
    try:
        async with engine.begin() as connection:
            await serialised(connection)
            await _ensure_account(connection, account, password)
            if await connection.scalar(HELD_TOO_MUCH, {"account": account.name}):
                say(
                    f"{account.label}: {account.name} holds a right no grant can fence "
                    "(superuser, role or database creation, replication or bypassing row "
                    f"security); remove it by hand. {account.service} must not run until then"
                )
                return False
        async with engine.begin() as connection:
            await serialised(connection)
            if apply_grants is None:
                await apply_listed_grants(connection, account)
            else:
                await apply_grants(connection)
    except DBAPIError as error:
        say(
            f"{account.label}: Postgres refused to prepare {account.name} "
            f"({reason_of(error)}); {account.service} cannot start, and {account.when_missing}. "
            "Give the install account CREATEROLE, or create the role by hand "
            f"({account.guide})"
        )
        return False
    return True


async def apply_listed_grants(connection: AsyncConnection, account: RoleAccount) -> None:
    """Everything the account held is taken first, so what is left is exactly the list."""
    for revoke in (await connection.execute(_MEMBERSHIPS, {"account": account.name})).scalars():
        await connection.exec_driver_sql(revoke)
    for statement in (*take_everything(account), *account.grants):
        await connection.exec_driver_sql(statement)


async def take_shared_rights(engine: AsyncEngine, account: RoleAccount, say: Say) -> None:
    """Advisory locks, `pg_notify` and `TEMP` from `PUBLIC`; a refusal leaves the account
    ready, with what is left said."""
    try:
        async with engine.begin() as connection:
            await serialised(connection)
            await _revoke_shared_rights(connection)
    except DBAPIError as error:
        say(
            f"{account.label}: ready, but Postgres refused to take advisory locks, pg_notify "
            f"and temporary tables from PUBLIC ({reason_of(error)}). A broken-into "
            f"{account.process} could take beat's leader lock and stop every schedule until "
            f"it is stopped ({account.guide})"
        )
        return
    say(f"{account.label}: {account.name} ready, with its grants and nothing else")


async def serialised(connection: AsyncConnection) -> None:
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PROVISION_LOCK_KEY}
    )


async def _ensure_account(connection: AsyncConnection, account: RoleAccount, password: str) -> None:
    named = {"account": account.name}
    if (await connection.execute(ROLE_EXISTS, named)).first() is None:
        await _run_formatted(connection, _CREATE_ROLE, named)
    await _run_formatted(
        connection, _ALTER_ROLE, named | {"limit": account.connection_limit, "password": password}
    )
    # Only these two settings stay, whatever was set by hand before.
    await _run_formatted(connection, _RESET_ROLE_DEFAULTS, named)
    for setting, value in (
        ("statement_timeout", account.statement_timeout),
        ("idle_in_transaction_session_timeout", account.idle_in_transaction_timeout),
    ):
        await _run_formatted(
            connection, _SET_ROLE_DEFAULT, named | {"setting": setting, "value": value}
        )


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


def reason_of(error: DBAPIError) -> str:
    """Postgres's own one line, never the statement, which may hold the password."""
    original = getattr(error, "orig", None)
    return str(original).splitlines()[0] if original is not None else type(error).__name__


async def account_exists(session: AsyncSession, account: RoleAccount) -> bool:
    """Whether setup has made the account on this server."""
    return await session.scalar(ROLE_EXISTS, {"account": account.name}) is not None


async def beat_lock_exposed(session: AsyncSession, account: RoleAccount) -> bool:
    """Whether the account may still take beat's leader lock. Ask only once it exists."""
    return bool(await session.scalar(_BEAT_LOCK_EXPOSED, {"account": account.name}))
