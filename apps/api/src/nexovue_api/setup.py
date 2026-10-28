"""Prepare Postgres and the object store before any service starts (ready-to-deploy, T2).

`python -m nexovue_api.setup` — every deployment runs it first, as `nexovue setup`; running it again
changes nothing. ClickHouse and the Kafka topics are the pipeline image's half (`/setup`).

The bucket is created here, by a step that runs once with the operator's credentials, rather
than by the API, which should not hold the permission to create one. Where the infrastructure
already made the bucket — a cloud install — finding it is all this does.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config

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


def main() -> int:
    migrate_postgres()
    print("postgres: at the newest migration")

    settings = object_store_settings()
    if settings is None:
        print("object store: not configured, skipped")
        return 0
    asyncio.run(ObjectStore(settings).ensure_bucket())
    print(f"object store: bucket {settings.bucket} ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
