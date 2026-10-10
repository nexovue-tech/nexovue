"""Erasing every deleted organization's telemetry and files, once a night (ADR-014).

The API deletes the Postgres rows when the owner asks; what is left is the
telemetry in ClickHouse and the uploads in the object store. Erasing a tenant
from ClickHouse is a mutation that rewrites most of each table and holds the
superseded parts in memory for `old_parts_lifetime`, so the night erases every
owed tenant **together**, one table at a time: only one table's old parts are
ever held at once, and ten deletions cost what one does.

**Proof per tenant.** After the mutations, a tenant with no rows left in any
table has its files deleted and its certificate written. One with rows left —
records the pipeline landed after the mutation passed — stays owed and is taken
again the next night. A run that fails part-way certifies nothing it did not
prove, so the next night is always the retry.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from nexovue_platform import erasure
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.erasure import ERASE_OWED_TASK
from nexovue_scheduler.erasure import owed as owed_erasures
from nexovue_scheduler.erasure.store import ErasureStore, connect
from nexovue_scheduler.erasure.tables import PURGED_TABLES
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.probes.failures import ProberError
from nexovue_scheduler.probes.files import ObjectStoreFiles
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import Settings, get_settings

logger = get_logger(__name__)

# ClickHouse's default `old_parts_lifetime`: how long one group's superseded
# parts are held. The next group waits it out so the two never add up.
OLD_PARTS_LIFETIME_SECONDS: Final = 480


@dataclass(slots=True)
class Night:
    """What one run proved erased, and what it leaves owed."""

    erased: list[str] = field(default_factory=list)
    still_owed: dict[str, str] = field(default_factory=dict)


async def erase_owed(
    engine: AsyncEngine,
    store: ErasureStore,
    files: ObjectStoreFiles | None,
    *,
    batch_size: int,
    pause: Callable[[float], None] = time.sleep,
) -> Night:
    """Erase every owed organization, a group of `batch_size` at a time.

    `files` is None when the deployment has no object store, in which case no
    upload was ever accepted and there are no files to erase.
    """
    night = Night()
    attempted: set[str] = set()
    while True:
        async with engine.connect() as connection:
            owed = await owed_erasures.take_owed(
                connection, limit=batch_size, attempted=frozenset(attempted)
            )
        if not owed.ids:
            return night
        if attempted:
            pause(OLD_PARTS_LIFETIME_SECONDS)
        attempted.update(owed.ids)

        for table in PURGED_TABLES:
            store.submit(table, owed)
            store.wait_for(table)

        for left in store.remaining(owed):
            if left.tables:
                night.still_owed[left.org_id] = f"rows left: {left.tables}"
                continue
            try:
                for prefix in erasure.tenant_prefixes(left.org_id):
                    if files is not None:
                        files.delete_prefix(prefix)
            except ProberError as refused:
                night.still_owed[left.org_id] = str(refused)
                continue
            async with engine.begin() as connection:
                await owed_erasures.certify(connection, left.org_id)
            night.erased.append(left.org_id)


@task(ERASE_OWED_TASK)
def erase_owed_tonight() -> None:
    """Beat runs this hourly; it works only in `ERASURE_HOUR_UTC`, so a leader
    changing hands cannot run two erasures in one night."""
    settings = get_settings()
    if not settings.clickhouse_eraser_dsn:
        logger.error("deleted organizations are not being erased: CLICKHOUSE_ERASER_DSN is not set")
        return
    now = datetime.now(UTC)
    if now.hour != settings.erasure_hour_utc:
        return

    try:
        night = with_engine(
            lambda engine: erase_owed(
                engine,
                connect(settings.clickhouse_eraser_dsn),
                _files(settings),
                batch_size=settings.erasure_batch_size,
            )
        )
        logger.info(
            "nightly erasure finished",
            erased=len(night.erased),
            still_owed=len(night.still_owed),
            reasons=night.still_owed,
        )
    finally:
        _report_overdue(now)


def _report_overdue(now: datetime) -> None:
    """An erasure owed past `OVERDUE_AFTER` is stuck; the operators must see it."""

    async def read(engine: AsyncEngine) -> owed_erasures.Overdue:
        async with engine.connect() as connection:
            return await owed_erasures.overdue(connection, now=now)

    late = with_engine(read)
    if late.count:
        logger.error(
            "deleted organizations have been owed an erasure for more than 3 days",
            overdue=late.count,
            oldest_deleted_at=late.oldest.isoformat() if late.oldest else None,
        )


def _files(settings: Settings) -> ObjectStoreFiles | None:
    configured = (
        settings.s3_endpoint
        and settings.s3_bucket
        and settings.s3_access_key_id
        and settings.s3_secret_access_key
    )
    return ObjectStoreFiles(settings) if configured else None
