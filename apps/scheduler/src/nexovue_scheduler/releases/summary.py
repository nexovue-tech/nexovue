"""A period's stored reading, and the release summary a project opted in to (F28 FR55;
design section 6, D52, D53; plan T5.6).

**When.** The sync adds this job when it opens a period, and the deploy webhook when a
report opens one (`nexovue_platform.releases.summarize_request`), for 30 minutes after
the period's start (a starting guess). A start that moved later, as a reported start
does once its version is counted (FR42), puts the job back until it is due; so does a
comparison still under 5 settled minutes, for at most `SETTLE_GRACE`, after which what
it reads is stored.

**What it stores.** The shared comparison, read through `facts.release_comparison` with
the API's own rule, as `release_periods.last_reading` (`stored_reading`: the leading
block's method, words and counts, or the reason; never a name) and
`mixed_environments`. `/releases` reads both from Postgres alone (AC81).

**What it sends.** With the project's `release_summary_channel_id` set, one
`release_summary` notification on that channel, for every period that is a release,
whatever its reading: a report, not a detector. A period flagged `already_running` is
not a release (FR43) and gets neither. The channel is joined by the project's
organization and must be one people read, so a summary never pages; `sequence` is
always 0 and the insert does nothing on conflict, so a retried job writes it once. The
words are written at send (`alerts/messages.py`). Nothing here opens a finding.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from nexovue_platform import releases
from nexovue_platform.alerts.channels import READ_BY_PEOPLE
from nexovue_platform.alerts.model import RELEASE_SUMMARY
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.jobs.queue import enqueue
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.releases import SUMMARIZE_PERIOD_TASK, facts
from nexovue_scheduler.releases.store import ReleaseStore, connect_store
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# A comparison still under 5 settled minutes is tried again this much later, for at most
# `SETTLE_GRACE` past when it was due; then what it reads is stored (a guess).
RETRY_TOO_RECENT_AFTER: Final = timedelta(minutes=5)
SETTLE_GRACE: Final = timedelta(minutes=30)
# A summary never pages (`READ_BY_PEOPLE` only), and the outbox's `SEVERITIES` has two
# values: a warning, which quiet hours may hold as a digest (design section 6).
SUMMARY_SEVERITY: Final = "warning"
SUMMARY_SEQUENCE: Final = 0

_PERIOD: Final = text(
    """
    SELECT periods.started_at, periods.already_running
    FROM release_periods AS periods
    JOIN projects
      ON projects.org_id = periods.org_id AND projects.id = periods.project_id
    WHERE periods.org_id = :org_id AND periods.project_id = :project_id
      AND periods.id = :period_id AND projects.deleted_at IS NULL
    """
)
_STORE_READING: Final = text(
    """
    UPDATE release_periods
    SET last_reading = CAST(:reading AS jsonb), mixed_environments = :mixed
    WHERE org_id = :org_id AND project_id = :project_id AND id = :period_id
    """
)
# The project's chosen channel, by its organization, and only one people read: a
# summary never pages, whatever the column holds.
_WRITE_SUMMARY: Final = text(
    f"""
    INSERT INTO notifications
        (org_id, project_id, release_period_id, channel_id, kind, sequence, severity,
         deliver_after, created_at)
    SELECT projects.org_id, projects.id, CAST(:period_id AS uuid), channels.id,
           '{RELEASE_SUMMARY}', {SUMMARY_SEQUENCE}, '{SUMMARY_SEVERITY}', :now, :now
    FROM projects
    JOIN notification_channels AS channels
      ON channels.org_id = projects.org_id AND channels.id = projects.release_summary_channel_id
    WHERE projects.org_id = :org_id AND projects.id = :project_id
      AND projects.deleted_at IS NULL AND channels.kind IN :people
    ON CONFLICT DO NOTHING
    RETURNING id
    """
).bindparams(bindparam("people", expanding=True))


@dataclass(frozen=True, slots=True)
class Summarized:
    """What one run did, for its log line and the tests."""

    stored: bool
    notified: bool
    # Seconds until it runs again, when it put itself back.
    again_in: float | None = None


NOTHING_DONE: Final = Summarized(stored=False, notified=False)


async def summarize_period(
    engine: AsyncEngine,
    store: ReleaseStore,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    period_id: uuid.UUID,
    now: datetime,
) -> Summarized:
    """Store one period's reading and, where the project opted in, its summary."""
    keys = {"org_id": org_id, "project_id": project_id, "period_id": period_id}
    async with engine.connect() as connection:
        period = (await connection.execute(_PERIOD, keys)).first()
        if period is None or period.already_running:
            return NOTHING_DONE
        due = period.started_at + releases.SUMMARY_AFTER
        if now < due:
            return await _again(connection, keys, (due - now).total_seconds())
        fact = await facts.release_comparison(
            store,
            connection,
            org_id=org_id,
            project_id=project_id,
            period_id=period_id,
            window=releases.DEFAULT_WINDOW,
            now=now,
        )
        if fact is None:
            return NOTHING_DONE
        if fact.reason == releases.TOO_RECENT and now < due + SETTLE_GRACE:
            return await _again(connection, keys, RETRY_TOO_RECENT_AFTER.total_seconds())
        reading = releases.stored_reading(
            reason=fact.reason,
            blocks=fact.blocks,
            lead=fact.lead,
            traffic=fact.traffic,
            versions_combined=fact.versions_combined,
            minutes_differing=fact.minutes_differing,
        )
        await connection.execute(
            _STORE_READING,
            keys | {"reading": json.dumps(reading), "mixed": fact.mixed_environments},
        )
        written = await connection.execute(
            _WRITE_SUMMARY, keys | {"now": now, "people": sorted(READ_BY_PEOPLE)}
        )
        notified = written.first() is not None
        await connection.commit()
    return Summarized(stored=True, notified=notified)


async def _again(
    connection: AsyncConnection, keys: dict[str, uuid.UUID], seconds: float
) -> Summarized:
    await enqueue(
        connection,
        releases.summarize_request(
            keys["org_id"], keys["project_id"], keys["period_id"], countdown=seconds
        ),
    )
    await connection.commit()
    return Summarized(stored=False, notified=False, again_in=seconds)


# --------------------------------------------------------------------- the task

_store: ReleaseStore | None = None


def _the_store() -> ReleaseStore | None:
    global _store
    if _store is None:
        dsn = get_settings().clickhouse_dsn
        if not dsn:
            logger.error("CLICKHOUSE_DSN is not set for the scheduler; no release is summarized")
            return None
        _store = connect_store(dsn)
    return _store


@task(SUMMARIZE_PERIOD_TASK)
def summarize_period_task(org_id: str, project_id: str, period_id: str) -> None:
    store = _the_store()
    if store is None:
        return
    done = with_engine(
        lambda engine: summarize_period(
            engine,
            store,
            org_id=uuid.UUID(org_id),
            project_id=uuid.UUID(project_id),
            period_id=uuid.UUID(period_id),
            now=datetime.now(UTC),
        )
    )
    logger.info(
        "release_summary",
        org_id=org_id,
        project_id=project_id,
        period_id=period_id,
        stored=done.stored,
        notified=done.notified,
        again_in=done.again_in,
    )
