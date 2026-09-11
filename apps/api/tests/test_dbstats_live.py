"""Database-statistics reads against the running ClickHouse (M6 Phase A).

**This suite asserted emptiness until 2026-09-07, and said so out loud so that
it could stop.** Door 4b's poller had never been pointed at a real Postgres, and
the premise was written as an assertion with an instruction attached: *"if this
fails, a database has been polled for the first time — and the tests below
should be replaced with ones that assert on real readings."*

It failed. Polling a database we cannot reach through the customer's own
collector made the demo's own Postgres reachable that way, so real readings
exist and these are the assertions the old ones asked for.

The suite is deliberately tolerant of *how much* data there is — it runs against
whatever the demo happens to have published — and strict about its shape. A
count that varies between runs is not evidence; a statement with no hash, or a
duration that is not a positive number of seconds, is.

It fails when the stack is not up rather than skipping, because a skip here is
indistinguishable from a pass. `NEXOVUE_SKIP_LIVE_TESTS=1` opts out on purpose.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.dbstats import queries, service

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_HTTP_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

LOOKBACK: Final = timedelta(days=2)


def _parts() -> tuple[str, int, str, str, str]:
    url = urlsplit(CLICKHOUSE_DSN)
    return (
        url.hostname or "localhost",
        url.port or 8123,
        url.username or "",
        url.password or "",
        (url.path or "/").lstrip("/"),
    )


def _reachable() -> bool:
    host, port, *_ = _parts()
    try:
        with socket.create_connection((host, port), REACHABILITY_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_dbstats_live.py",
    remedy=f"ClickHouse is not answering on {CLICKHOUSE_DSN} — start it with `just dev`",
)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    host, port, username, password, database = _parts()
    connection = await clickhouse_connect.get_async_client(
        host=host, port=port, username=username, password=password, database=database
    )
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def tenant_with_metrics(client: AsyncClient) -> tuple[str, str]:
    """A tenant that has *some* gauge readings, so emptiness means one thing.

    Raw client on purpose: asking "who has data" is a question no tenant-scoped
    query can ask, and it is setup rather than a claim about the API.
    """
    result = await client.query(
        """
        SELECT tenant_id, project_id, count() AS readings
        FROM metrics_gauge
        WHERE timestamp >= %(since)s
        GROUP BY tenant_id, project_id
        ORDER BY readings DESC
        LIMIT 1
        """,
        parameters={"since": _since()},
    )
    if not result.result_rows:
        # No gauge data at all: the endpoints are still asserted against a real
        # ClickHouse below, which is the part that can be proved either way.
        return str(uuid.uuid4()), str(uuid.uuid4())
    tenant_id, project_id, _ = result.result_rows[0]
    return str(tenant_id), str(project_id)


def _since() -> datetime:
    return datetime.now(UTC) - LOOKBACK


def _window() -> tuple[datetime, datetime]:
    return _since(), datetime.now(UTC)


async def test_both_metric_names_have_readings_in_the_store(client: AsyncClient) -> None:
    """The premise, still stated as an assertion — now the other way round.

    This replaced a test asserting the opposite, which failed the day the
    collector route made a database reachable. Keeping the premise asserted is
    the point: if these rows disappear, the suite below would go green against
    nothing, which is the failure `require_stack` exists to prevent one level up.
    """
    result = await client.query(
        """
        SELECT metric_name, count() AS readings
        FROM metrics_gauge
        WHERE metric_name IN (%(duration)s, %(connections)s)
        GROUP BY metric_name
        """,
        parameters={
            "duration": queries.METRIC_OPERATION_DURATION,
            "connections": queries.METRIC_CONNECTION_COUNT,
        },
    )

    published = {str(name) for name, _ in result.result_rows}
    assert published == {queries.METRIC_OPERATION_DURATION, queries.METRIC_CONNECTION_COUNT}, (
        "door 4b's readings are missing from the store, so everything below would "
        f"assert against nothing. Run `just otel-demo up`. Found: {published}"
    )


async def test_slow_statements_reads_real_rows_and_they_are_shaped_correctly(
    client: AsyncClient, tenant_with_metrics: tuple[str, str]
) -> None:
    """Every field, because the ones that are wrong are wrong silently.

    A hash that came back empty, or a mean in milliseconds under a column
    labelled seconds, produces a screen that renders happily and misleads. The
    count is deliberately not asserted — it depends on what the demo has done
    recently, and a number that varies between runs is not evidence.
    """
    tenant_id, project_id = tenant_with_metrics
    start, end = _window()

    result = await service.slow_statements(
        TenantScopedRepository(client, tenant_id=tenant_id),
        project_id=project_id,
        from_time=start,
        to_time=end,
        limit=service.DEFAULT_STATEMENT_LIMIT,
    )

    assert result.statements, (
        "no statements read back; the premise test above should have caught this"
    )
    for statement in result.statements:
        assert statement.query_hash, "a statement with no hash cannot be looked up by anyone"
        assert statement.db_system, "the screen needs to say which engine this was"
        assert statement.server.address, "grouped by server, so it cannot be blank"
        assert statement.latest_mean_ms >= 0
        # Seconds on the wire, milliseconds on the screen. A poller that sent
        # milliseconds would put a thousand-fold error here and nothing else
        # would notice — an hour-long statement is what that looks like.
        assert statement.latest_mean_ms < 3_600_000, (
            f"{statement.latest_mean_ms}ms is longer than an hour, which is what a "
            f"seconds/milliseconds unit error looks like"
        )
        assert statement.worst_mean_ms >= statement.latest_mean_ms or statement.readings == 1


async def test_connection_health_reads_real_backend_states(
    client: AsyncClient, tenant_with_metrics: tuple[str, str]
) -> None:
    """States come from `pg_stat_activity`, so they are Postgres's own words.

    Asserted against that vocabulary rather than a fixed list, because a state
    this product invented would mean the translation layer had started guessing.
    """
    tenant_id, project_id = tenant_with_metrics
    start, end = _window()

    result = await service.connection_health(
        TenantScopedRepository(client, tenant_id=tenant_id),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )

    assert result.states, "no backend states read back"
    # `pg_stat_activity.state`'s full vocabulary. `starting` is the one this
    # test first omitted and the demo's Postgres produced within a minute — a
    # backend that has connected and not yet begun. The web app renders an
    # unrecognised state with a generic caption rather than dropping it, which
    # is why an omission here is a test bug and not a product one.
    known = {
        "active",
        "idle",
        "idle in transaction",
        "idle in transaction (aborted)",
        "fastpath function call",
        "starting",
        "disabled",
        "unknown",
    }
    for state in result.states:
        assert state.state in known, f"{state.state!r} is not a state pg_stat_activity reports"
        assert state.latest_backends >= 0
        assert state.peak_backends >= state.latest_backends
    assert result.latest_backends_total == sum(s.latest_backends for s in result.states)
    assert result.latest_backends_total > 0, "a database with no backends is not one we polled"


async def test_the_tenant_this_suite_reads_holds_other_metrics_too(
    client: AsyncClient, tenant_with_metrics: tuple[str, str]
) -> None:
    """Kept from when this suite asserted emptiness, and still worth having.

    It proved `[]` meant "door 4b has not run" rather than "this read is
    broken". Now that rows exist it proves the fixture picked a tenant that is
    genuinely busy, so a future emptiness above would be about door 4b and not
    about the suite having chosen a dormant project.
    """
    tenant_id, _ = tenant_with_metrics
    result = await client.query(
        """
        SELECT count() AS readings
        FROM metrics_gauge
        WHERE tenant_id = %(tenant_id)s AND timestamp >= %(since)s
        """,
        parameters={"tenant_id": tenant_id, "since": _since()},
    )

    assert result.result_rows[0][0] > 0, (
        "no gauge readings at all for this tenant, so the empty database "
        "responses above prove nothing — start the stack and seed it"
    )
