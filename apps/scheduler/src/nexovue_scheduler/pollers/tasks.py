"""The poller tasks: read one customer's system, publish what it reported.

Same arrangement as `probes/tasks.py`. Beat hands the whole connector definition
over rather than an id, so this worker never touches the control plane on the
path of a measurement. The cost is staleness bounded by the beat refresh
interval, which is the right trade for something that repeats every minute.

Both tasks run on the `light` queue: a poll is one API call or two `SELECT`s and
has nothing in common with the browser workers' memory profile.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

from nexovue_scheduler.celery_app import app
from nexovue_scheduler.pollers import cloudwatch, integrations, postgres_connect, postgres_stats
from nexovue_scheduler.pollers.aws import assume_customer_role, session_name_for
from nexovue_scheduler.pollers.envelope import build_cloudwatch_envelope, build_postgres_envelope
from nexovue_scheduler.pollers.health import report_failure, report_success
from nexovue_scheduler.pollers.window import PollWindow
from nexovue_scheduler.publish import ResultPublisher
from nexovue_scheduler.settings import get_settings
from nexovue_scheduler.telemetry.logging import get_logger

logger = get_logger(__name__)

CLOUDWATCH_TASK: Final = "nexovue_scheduler.pollers.poll_cloudwatch"
POSTGRES_TASK: Final = "nexovue_scheduler.pollers.poll_postgres"

# How far back a poll looks. Two CloudWatch periods, because AWS publishes a
# period some seconds after it closes and a one-period window regularly finds
# nothing — which looks identical to an instance that stopped reporting.
LOOKBACK_SECONDS: Final = 120

CONFIG_REGION: Final = "region"

_publisher: ResultPublisher | None = None


def _publisher_for_process() -> ResultPublisher:
    """One producer per worker process, built on first use.

    Celery forks workers and a Kafka producer's background thread does not
    survive a fork, so it is created after the fork rather than at import.
    """
    global _publisher
    if _publisher is None:
        _publisher = ResultPublisher(get_settings())
    return _publisher


@app.task(name=CLOUDWATCH_TASK, bind=True, max_retries=2)
def poll_cloudwatch(
    self: Any,
    *,
    integration_id: str,
    org_id: str,
    project_id: str,
    config: dict[str, Any],
    external_id: str | None = None,
) -> None:
    """Read one AWS account's RDS metrics and publish them.

    The credentials used here are minted from the customer's role at the top of
    this function and die with it. Nothing in `config` is a secret.
    """
    settings = get_settings()
    try:
        _poll_cloudwatch(settings, integration_id, org_id, project_id, config, external_id, self)
    except Exception as failure:
        # Recorded before re-raising, so the customer sees why. Assuming a role
        # is where this fails in practice, and both causes — a trust policy that
        # does not name us, an ExternalId that does not match — are theirs to fix.
        report_failure(settings, integration_id, str(failure))
        raise
    report_success(settings, integration_id)


def _poll_cloudwatch(
    settings: Any,
    integration_id: str,
    org_id: str,
    project_id: str,
    config: dict[str, Any],
    external_id: str | None,
    task: Any,
) -> None:
    """The poll itself, so the task above is only about reporting the outcome."""
    grant = integrations.role_grant_from(config, external_id)
    region = str(config.get(CONFIG_REGION, settings.aws_region))
    window = PollWindow.ending_now(seconds=LOOKBACK_SECONDS)

    session = assume_customer_role(
        grant, session_name=session_name_for(integration_id), region=region
    )
    readings = cloudwatch.collect_rds_readings(
        session.client("cloudwatch", region_name=region),
        instance_identifiers=integrations.rds_instances_from(config),
        window=window,
    )
    logger.info(
        "cloudwatch poll completed",
        integration_id=integration_id,
        account_id=grant.account_id,
        region=region,
        readings=len(readings),
    )
    if not readings:
        # Nothing was published, so nothing claims a value we did not read.
        return

    envelope = build_cloudwatch_envelope(
        readings,
        org_id=org_id,
        project_id=project_id,
        environment=settings.env,
        region=region,
        account_id=grant.account_id,
        window=window,
    )
    _publish_or_retry(task, envelope, org_id=org_id, integration_id=integration_id)


@app.task(name=POSTGRES_TASK, bind=True, max_retries=2)
def poll_postgres(
    self: Any,
    *,
    integration_id: str,
    org_id: str,
    project_id: str,
    config: dict[str, Any],
    external_id: str | None = None,
) -> None:
    """Read one database's statement and connection statistics and publish them."""
    settings = get_settings()
    try:
        _poll_postgres(settings, integration_id, org_id, project_id, config, external_id, self)
    except Exception as failure:
        report_failure(settings, integration_id, str(failure))
        raise
    report_success(settings, integration_id)


def _poll_postgres(
    settings: Any,
    integration_id: str,
    org_id: str,
    project_id: str,
    config: dict[str, Any],
    external_id: str | None,
    task: Any,
) -> None:
    """The poll itself, so the task above is only about reporting the outcome."""
    target = integrations.database_target_from(config, external_id)
    region = str(config.get(CONFIG_REGION, settings.aws_region))
    window = PollWindow.ending_now(seconds=LOOKBACK_SECONDS)

    snapshot = asyncio.run(_read_database(target, integration_id=integration_id, region=region))
    logger.info(
        "postgres poll completed",
        integration_id=integration_id,
        server_address=target.host,
        statements=len(snapshot.statements),
        connection_states=len(snapshot.connections),
    )

    envelope = build_postgres_envelope(
        snapshot,
        org_id=org_id,
        project_id=project_id,
        environment=settings.env,
        server_address=target.host,
        server_port=target.port,
        window=window,
    )
    _publish_or_retry(task, envelope, org_id=org_id, integration_id=integration_id)


async def _read_database(
    target: postgres_connect.DatabaseTarget, *, integration_id: str, region: str
) -> postgres_stats.DatabaseSnapshot:
    """One connection, two reads, closed either way.

    A monitoring connection left open on a customer's database is a connection
    slot they cannot use, and this poller runs against exactly the database
    whose connection count it is reporting.
    """
    connection = await postgres_connect.connect_read_only(
        target, integration_id=integration_id, region=region
    )
    try:
        return await postgres_stats.collect_snapshot(connection)
    finally:
        await connection.close()


def _publish_or_retry(task: Any, envelope: bytes, *, org_id: str, integration_id: str) -> None:
    """Delivery is the only thing worth retrying.

    A system that answered honestly and reported a bad number is a measurement;
    a reading that never reached the broker is indistinguishable from a poll
    that never ran.
    """
    try:
        _publisher_for_process().publish(envelope, org_id=org_id)
    except Exception as failure:
        logger.warning("publishing a poll result failed; retrying", integration_id=integration_id)
        raise task.retry(exc=failure, countdown=5) from failure
