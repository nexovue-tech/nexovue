"""The hourly baseline job's memory against real ClickHouse and Postgres (AC29, plan T8.2).

A worker child keeps the arenas a job grew into, so a job's peak becomes the child's size
for as long as it lives, and eight children share `scheduler-light`'s 768 MiB. The job's
peak must therefore stay small and bounded however many services a project has: four
weeks of full history, every minute counted, is the most a project can hold.
This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import socket
import tracemalloc
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

from alert_fixtures import AlertTenant
from clickhouse_connect.driver.client import Client
from detection_history import CLICKHOUSE_DSN, POSTGRES_DSN, ROLLUP
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.detection import baselines as baseline_job
from nexovue_scheduler.detection.method import SENSITIVITIES
from nexovue_scheduler.detection.store import READINGS_TABLE, DetectionStore

# Two batches' worth, so a peak that grows with the project's size shows.
SERVICES: Final = 20
HISTORY_DAYS: Final = 29
MINUTES: Final = HISTORY_DAYS * 24 * 60
WIDTH: Final = SENSITIVITIES["normal"].window_minutes
# Measured: 180 MiB for these 20 services when 28 days of counts were held as tuples and
# every batch at once; 24 MiB once read compactly, one batch of services at a time.
PEAK_BOUND_MIB: Final = 32


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port), 2
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN, 8123) and _reachable(POSTGRES_DSN, 5432),
    suite="apps/scheduler/tests/test_detection_memory_live.py",
    remedy="ClickHouse or Postgres is not reachable — run `just up`",
)


def _seed_full_history(client: Client, tenant: AlertTenant, until: datetime) -> None:
    """Every minute of four weeks for each service, made by the server: 835,200 rows."""
    first = int((until - timedelta(minutes=MINUTES)).timestamp())
    client.command(
        f"""
        INSERT INTO {ROLLUP}
            (tenant_id, project_id, service_name, minute, request_count, error_count,
             latency_buckets)
        SELECT '{tenant.org_id}', '{tenant.project_id}', concat('service-', toString(s.number)),
               toDateTime({first} + m.number * 60), 100 + m.number % 7, m.number % 3,
               arrayReduce('sumForEachState',
                   [arrayMap(i -> toUInt32(if(i BETWEEN 20 AND 30, 9, 0)), range(64))])
        FROM numbers({SERVICES}) AS s CROSS JOIN numbers({MINUTES}) AS m
        """
    )


def _seed_judged_readings(client: Client, tenant: AlertTenant, at: datetime) -> None:
    """One judged reading per service, so the job reads each one's 28 days of counts for
    its burst profile (D17)."""
    client.command(
        f"""
        INSERT INTO {READINGS_TABLE}
            (tenant_id, project_id, service_name, minute, version, window_minutes,
             failing_median, failing_spread)
        SELECT '{tenant.org_id}', '{tenant.project_id}', concat('service-', toString(number)),
               toDateTime({int(at.timestamp())}), 1, {WIDTH}, 0.01, 0.001
        FROM numbers({SERVICES})
        """
    )


async def test_a_baseline_job_s_peak_stays_bounded_over_four_full_weeks(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _seed_full_history(administrator, project, now)
    _seed_judged_readings(administrator, project, now - timedelta(days=1))

    tracemalloc.start()
    try:
        written = await baseline_job.compute_project(engine, store, project.project_id, now=now)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert written == SERVICES
    async with engine.connect() as connection:
        rows = await connection.scalar(
            text(
                "SELECT count(DISTINCT service_name) FROM detection_baselines "
                "WHERE project_id = :project_id"
            ),
            {"project_id": project.project_id},
        )
    assert rows == SERVICES
    assert peak / 2**20 < PEAK_BOUND_MIB, f"the job's peak was {peak / 2**20:.0f} MiB"
