"""Alembic environment for the control-plane database.

Async, because the application's engine is asyncpg and running migrations on a
second driver means two connection stacks to keep in agreement.
"""

import asyncio
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


def do_run_migrations(connection: Connection) -> None:
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
