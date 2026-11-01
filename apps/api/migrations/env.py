"""Alembic environment for the control-plane database.

Async, because the application's engine is asyncpg and running migrations on a
second driver means two connection stacks to keep in agreement.
"""

import asyncio
import time
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from nexovue_api.data.postgres import (
    Base,
    models,  # noqa: F401  imported for the side effect: registers the tables on Base.metadata
    postgres_dsn,
)

config = context.config

# ConfigParser reads `%` as interpolation, and a generated password may contain
# one. Doubling it here is what keeps a valid DSN from becoming a parse error.
config.set_main_option("sqlalchemy.url", postgres_dsn().replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of connecting — `alembic upgrade head --sql`."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# A migration waiting on a lock behind a long transaction would hold its own
# request at the head of the queue, and every write to that table would wait
# behind it. This fails it instead; run it again when the table is quiet
# (group 9, C6).
LOCK_TIMEOUT = "5s"

# Held for the whole run so two installs starting together migrate one after the other: without
# it the second creates the tables the first is creating, and fails (docs/changes/ready-to-deploy,
# T2). Session-level, not per transaction, because a few migrations commit mid-run in autocommit
# blocks; it ends with the connection, which NullPool closes when the run does.
MIGRATION_LOCK = 0x6E65786F76756502  # "nexovue" and 2; ClickHouse's runner holds 1

# How long a setup waits for another's migrations before giving up. Generous, because the other
# may be building an index concurrently on a large table; a stuck holder is the operator's to end.
MIGRATION_LOCK_WAIT_MINUTES = 15
MIGRATION_LOCK_POLL_SECONDS = 0.5


def wait_for_migration_lock(connection: Connection) -> None:
    """Take MIGRATION_LOCK by polling in autocommit, so the wait holds no transaction and no
    running statement.

    CREATE INDEX CONCURRENTLY waits for every transaction holding a snapshot, and a session
    blocked in pg_advisory_lock holds one even in autocommit: the holder's index build would
    wait on the waiter, which waits on the holder, and Postgres ends one as a deadlock.
    """
    deadline = time.monotonic() + MIGRATION_LOCK_WAIT_MINUTES * 60
    connection.execution_options(isolation_level="AUTOCOMMIT")
    while not connection.exec_driver_sql(
        f"SELECT pg_try_advisory_lock({MIGRATION_LOCK})"
    ).scalar_one():
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"another setup has held the migration lock for {MIGRATION_LOCK_WAIT_MINUTES} "
                "minutes; if it is still migrating, wait for it and run setup again, "
                "otherwise end its session (pg_stat_activity names it)"
            )
        time.sleep(MIGRATION_LOCK_POLL_SECONDS)
    # SQLAlchemy began a transaction it never sent to the database; end it, so Alembic's
    # begin_transaction starts a real one rather than joining this.
    connection.commit()
    connection.execution_options(isolation_level=connection.default_isolation_level)


def do_run_migrations(connection: Connection) -> None:
    wait_for_migration_lock(connection)
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        # Inside Alembic's own transaction: a statement before it starts one
        # Alembic does not commit, and every migration is silently rolled back.
        # Session-level rather than LOCAL, so it outlives the autocommit blocks a
        # few migrations open.
        connection.exec_driver_sql(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
