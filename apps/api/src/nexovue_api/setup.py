"""Prepare Postgres and the object store before any service starts (ready-to-deploy, T2).

`python -m nexovue_api.setup` — every deployment runs it first, as `nexovue setup`; running it again
changes nothing. ClickHouse and the Kafka topics are the pipeline image's half (`/setup`).

`nexovue setup schema` prints the newest Postgres migration this image carries and touches nothing:
`nexovue rollback` compares two versions' answers to tell whether an upgrade changed the schema.

After the migrations, meaning search's extension and columns (`provision_vector`, F4 Step 6),
then the AI role's own account and its grants (`insight_account`, F3); a restore drops grants,
and `start` runs this step, so they come back. `nexovue setup grants` runs that part alone.

**`vector` is created here, not by a migration** (F4 design, *Extension*; D62; ADR-067): a
missing extension in the Alembic chain would stop `setup`, and with it every service. Here a
refusal is said and `setup` goes on, and chat searches by keyword only (AC43). The columns sit
outside Alembic, so no migration names them; statements cast their values as SQL text, so no
`pgvector` package is needed.

The bucket is created here, by a step that runs once with the operator's credentials, rather
than by the API, which should not hold the permission to create one. Where the infrastructure
already made the bucket — a cloud install — finding it is all this does.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from nexovue_platform.knowledge import VECTOR_COLUMNS
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from nexovue_api import insight_account
from nexovue_api.data.objectstore.store import ObjectStore
from nexovue_api.settings import ObjectStoreSettings

ALEMBIC_INI: Final = Path(__file__).resolve().parents[2] / "alembic.ini"

# The S3 variables every service reads (settings.py); all four or none.
OBJECT_STORE_VARIABLES: Final = (
    "S3_ENDPOINT",
    "S3_BUCKET",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
)


def migrate_postgres() -> None:
    """Every Alembic migration above the database's revision."""
    command.upgrade(Config(str(ALEMBIC_INI)), "head")


def postgres_schema() -> str:
    """The newest migration this image carries — one head, which `test_migrations` holds."""
    return " ".join(sorted(ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads()))


# Superuser only: pgvector's control file does not mark it trusted (read 2026-11-21, v0.8.1's
# `vector.control`). The bundled image's account is a superuser; a managed one may not be.
CREATE_VECTOR: Final = "CREATE EXTENSION IF NOT EXISTS vector"
VECTOR_REFUSED: Final = (
    "vector: Postgres refused to create the vector extension or its columns ({reason}); "
    "chat searches runbooks and fact lines by keyword only, and nothing else is affected. "
    "An account allowed to create extensions can run `CREATE EXTENSION vector`, then "
    "`nexovue setup` again (docs/self-hosting/)"
)
VECTOR_READY: Final = "vector: meaning search's extension and columns ready"


# As the migrations' (`migrations/env.py`): adding a column takes the table's strongest lock,
# so a long transaction makes this fail, said, rather than stop every write behind it.
VECTOR_LOCK_TIMEOUT: Final = "SET LOCAL lock_timeout = '5s'"
_COLUMN_EXISTS: Final = text(
    """
    SELECT 1 FROM pg_catalog.pg_attribute
    WHERE attrelid = CAST(:table AS regclass) AND attname = :column AND NOT attisdropped
    """
)


async def provision_vector(dsn: str | None = None, *, say: Callable[[str], None] = print) -> bool:
    """Create `vector` and meaning search's columns, each with no size so every model keeps
    its own; True when they are there. A column already there is not touched, so a later
    `setup` takes no lock. Never raises for a refusal: an install without the extension
    loses meaning search alone (AC43)."""
    engine = create_async_engine(dsn or os.environ["POSTGRES_DSN"], pool_size=1, max_overflow=0)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(VECTOR_LOCK_TIMEOUT)
            await connection.exec_driver_sql(CREATE_VECTOR)
            for table, column, kind in VECTOR_COLUMNS:
                found = await connection.scalar(
                    _COLUMN_EXISTS, {"table": f"public.{table}", "column": column}
                )
                if found is None:
                    await connection.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {column} {kind}"
                    )
    except DBAPIError as error:
        original = getattr(error, "orig", None)
        reason = str(original).splitlines()[0] if original is not None else type(error).__name__
        say(VECTOR_REFUSED.format(reason=reason))
        return False
    finally:
        await engine.dispose()
    say(VECTOR_READY)
    return True


def object_store_settings() -> ObjectStoreSettings | None:
    """The store from the environment, or None when the install has none configured."""
    present = [name for name in OBJECT_STORE_VARIABLES if os.environ.get(name)]
    if not present:
        return None
    if len(present) != len(OBJECT_STORE_VARIABLES):
        missing = sorted(set(OBJECT_STORE_VARIABLES) - set(present))
        raise SystemExit(f"setup: the object store is half configured; set {', '.join(missing)}")
    return ObjectStoreSettings(
        endpoint=os.environ["S3_ENDPOINT"],
        bucket=os.environ["S3_BUCKET"],
        access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        region=os.environ.get("S3_REGION", "us-east-1"),
    )


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["schema"]:
        print(f"postgres {postgres_schema()}")
        return 0
    if arguments == ["grants"]:
        asyncio.run(insight_account.provision())
        return 0
    if arguments:
        raise SystemExit("usage: nexovue setup [schema | grants]")
    migrate_postgres()
    print("postgres: at the newest migration")
    # Never stops setup: meaning search is optional, and a refusal is said (AC43). Before the
    # AI account's grants, which name these columns only once they exist.
    asyncio.run(provision_vector())
    # Never stops setup: AI is optional, and every refusal is said (AC71).
    asyncio.run(insight_account.provision())

    settings = object_store_settings()
    if settings is None:
        print("object store: not configured, skipped")
        return 0
    asyncio.run(ObjectStore(settings).ensure_bucket())
    print(f"object store: bucket {settings.bucket} ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
