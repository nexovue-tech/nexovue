"""The alert tasks: deliver, the delivery sweep, send test, and pruning the log."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from nexovue_platform.alerts.tasks import (
    DELIVER_TASK,
    PRUNE_ATTEMPTS_TASK,
    SEND_TEST_RESULT_KIND,
    SEND_TEST_RESULT_TTL_SECONDS,
    SEND_TEST_TASK,
    SWEEP_DELIVERIES_TASK,
)
from nexovue_platform.checks.sealing import NOTIFICATION_CHANNEL_PURPOSE, SealedBox, decode_key
from nexovue_platform.email import build_email_sender
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import health, incidents, outbox
from nexovue_scheduler.alerts import send_test as channel_test
from nexovue_scheduler.alerts.delivery import deliver_due
from nexovue_scheduler.alerts.dispatch import Outbound
from nexovue_scheduler.jobs import results
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# The delivery log is kept this long (FR20).
ATTEMPTS_KEPT: Final = timedelta(days=30)

_outbound: Outbound | None = None


@task(DELIVER_TASK)
def deliver() -> None:
    """The fast path, dispatched after a commit that wrote notifications."""
    _deliver_due()


@task(SWEEP_DELIVERIES_TASK)
def sweep_deliveries() -> None:
    """The safety net: anything due that the fast path did not take (ADR-013), the
    repeats open incidents are owed (FR10), and which channels are failing (FR21)."""

    async def repeat(engine: AsyncEngine) -> int:
        async with engine.begin() as connection:
            return await incidents.renotify_due(connection, now=datetime.now(UTC))

    repeated = with_engine(repeat)
    if repeated:
        logger.info("alert repeats written", notifications=repeated)
    _deliver_due()
    outbound = _outbound_for_process()
    reported = with_engine(
        lambda engine: health.report_failing_channels(
            engine,
            email_sender=outbound.email_sender,
            app_base_url=outbound.app_base_url,
            now=datetime.now(UTC),
        )
    )
    if reported:
        logger.info("reported failing alert channels", channels=reported)


@task(SEND_TEST_TASK)
def send_test(*, org_id: str, channel_id: str, request_id: str) -> None:
    """Send one labelled test and leave the outcome for the API to collect (FR3)."""
    outcome = with_engine(
        lambda engine: channel_test.send_test(
            engine,
            _outbound_for_process(),
            org_id=uuid.UUID(org_id),
            channel_id=uuid.UUID(channel_id),
            request_id=request_id,
            now=datetime.now(UTC),
        )
    )
    with_engine(
        lambda engine: results.store(
            engine,
            kind=SEND_TEST_RESULT_KIND,
            org_id=org_id,
            request_id=request_id,
            payload={
                "delivered": outcome.delivered,
                "http_status": outcome.http_status,
                "reason": outcome.reason,
            },
            ttl_seconds=SEND_TEST_RESULT_TTL_SECONDS,
        )
    )


@task(PRUNE_ATTEMPTS_TASK)
def prune_attempts() -> None:
    before = datetime.now(UTC) - ATTEMPTS_KEPT

    async def prune(engine: AsyncEngine) -> int:
        async with engine.begin() as connection:
            return await outbox.prune_attempts(connection, before=before)

    pruned = with_engine(prune)
    if pruned:
        logger.info("pruned the alert delivery log", attempts=pruned)


def _deliver_due() -> None:
    report = with_engine(lambda engine: deliver_due(engine, _outbound_for_process()))
    if report.delivered or report.retrying or report.failed or report.dropped:
        logger.info(
            "alert deliveries",
            delivered=report.delivered,
            retrying=report.retrying,
            failed=report.failed,
            dropped=report.dropped,
        )


def _outbound_for_process() -> Outbound:
    """Built once per process. A missing mail provider or key is not an error here:
    each delivery that needs one records which, as its reason."""
    global _outbound
    if _outbound is None:
        settings = get_settings()
        configured = settings.email
        _outbound = Outbound(
            email_sender=build_email_sender(configured) if configured else None,
            secret_box=_secret_box(settings.check_secret_key),
            app_base_url=settings.app_base_url,
            private_allowed=settings.private_probe_targets_allowed,
        )
    return _outbound


def _secret_box(configured_key: str) -> SealedBox | None:
    if not configured_key:
        return None
    try:
        return SealedBox(decode_key(configured_key), purpose=NOTIFICATION_CHANNEL_PURPOSE)
    except ValueError:
        logger.error("CHECK_SECRET_KEY is not a usable key; channel secrets cannot be opened")
        return None
