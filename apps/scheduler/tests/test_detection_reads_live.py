"""What the project job's window read costs ClickHouse, from `system.query_log` (NFR5, AC29).

Every minute each project's window read takes its services' last few minutes. A read takes
whole granules, so with many services in one part the granule size, not the rows wanted,
decides what is read: at the default 8,192 rows a month-full partition read 90,112 rows for
69 (T8.2). This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

from alert_fixtures import AlertTenant
from clickhouse_connect.driver.client import Client
from detection_history import CLICKHOUSE_DSN, POSTGRES_DSN, ROLLUP
from live import require_stack

from nexovue_scheduler.detection.store import DetectionStore, ProjectBinding

SERVICES: Final = 10
DAYS: Final = 28
MINUTES: Final = DAYS * 24 * 60
WINDOW: Final = timedelta(minutes=7)
# A service's few minutes fall in one granule, or two at a boundary (migration 000035).
# At the default of 8,192 rows this read took 75,520.
GRANULE_ROWS: Final = 256
MOST_ROWS: Final = SERVICES * 2 * GRANULE_ROWS


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
    suite="apps/scheduler/tests/test_detection_reads_live.py",
    remedy="ClickHouse or Postgres is not reachable — run `just up`",
)


def _month_end() -> datetime:
    """The end of this month: 28 days before it are all in this month's partition, and
    none has passed its retention."""
    today = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (today + timedelta(days=32)).replace(day=1)


def _seed(client: Client, tenant: AlertTenant, until: datetime) -> None:
    """Every minute of four weeks for each service up to `until`, in one month's part:
    403,200 rows, as a project's month-full partition holds."""
    first = int((until - timedelta(minutes=MINUTES)).timestamp())
    client.command(
        f"""
        INSERT INTO {ROLLUP}
            (tenant_id, project_id, service_name, minute, request_count, error_count,
             latency_buckets)
        SELECT '{tenant.org_id}', '{tenant.project_id}', concat('service-', toString(s.number)),
               toDateTime({first} + m.number * 60), 100, 0,
               arrayReduce('sumForEachState', [arrayMap(i -> toUInt32(i = 25), range(64))])
        FROM numbers({SERVICES}) AS s CROSS JOIN numbers({MINUTES}) AS m
        """
    )


def test_a_window_read_takes_about_the_rows_it_needs(
    store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    until = _month_end()
    _seed(administrator, project, until)
    binding = ProjectBinding(str(project.org_id), str(project.project_id), 30)

    found = store.for_project(binding).window_minutes(start=until - WINDOW, end=until)

    administrator.command("SYSTEM FLUSH LOGS")
    read_rows = administrator.query(
        """
        SELECT read_rows FROM system.query_log
        WHERE type = 'QueryFinish' AND query_kind = 'Select'
          AND event_time >= now() - INTERVAL 10 MINUTE
          AND has(tables, 'nexovue.service_stats_1m')
          AND position(query, {tenant_id:String}) > 0
        ORDER BY event_time_microseconds DESC LIMIT 1
        """,
        parameters={"tenant_id": str(project.org_id)},
    ).result_rows[0][0]
    assert len(found) == SERVICES
    assert sum(len(minutes) for minutes in found.values()) == SERVICES * 7
    assert read_rows <= MOST_ROWS, f"read {read_rows} rows for {SERVICES * 7}"
