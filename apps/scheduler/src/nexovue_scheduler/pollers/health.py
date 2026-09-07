"""Telling the customer whether their connector is working.

A poll that fails silently is the worst outcome this door has. The customer sees
an empty graph and cannot tell whether we are broken, their account is
misconfigured, or there is genuinely nothing to report — and the two things that
actually go wrong (a trust policy that does not name our account, an ExternalId
that does not match) are both fixed in *their* account, so they have to be told.

Writing here is the scheduler's only write to Postgres. It is deliberately a
separate connection with its own short-lived engine rather than the beat
scheduler's: a poll runs in a worker process, not in beat, and a pool held open
per worker for one small update every few minutes would cost more than it saves.
"""

from __future__ import annotations

import asyncio
import uuid

from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import create_async_engine

from nexovue_scheduler.pollers import integrations
from nexovue_scheduler.settings import Settings

logger = get_logger(__name__)


async def _with_engine(settings: Settings, work: object) -> None:
    engine = create_async_engine(str(settings.postgres_dsn), pool_size=1, max_overflow=0)
    try:
        await work(engine)  # type: ignore[operator]
    finally:
        await engine.dispose()


def report_success(settings: Settings, integration_id: str) -> None:
    """Record that this connector just worked."""
    _run(settings, integration_id, None)


def report_failure(settings: Settings, integration_id: str, reason: str) -> None:
    """Record that this connector is broken, and why."""
    _run(settings, integration_id, reason)


def _run(settings: Settings, integration_id: str, reason: str | None) -> None:
    """Write the outcome, and never let writing it break the poll.

    A failure to record health is worth a log line and nothing more. Raising
    here would turn "we could not update a status column" into "this poll
    failed", which is a worse lie than the one it is trying to correct.
    """

    async def work(engine: object) -> None:
        identifier = uuid.UUID(integration_id)
        if reason is None:
            await integrations.record_poll_succeeded(engine, identifier)  # type: ignore[arg-type]
        else:
            await integrations.record_poll_failed(engine, identifier, reason)  # type: ignore[arg-type]

    try:
        asyncio.run(_with_engine(settings, work))
    except Exception:
        logger.exception("could not record connector health", integration_id=integration_id)
