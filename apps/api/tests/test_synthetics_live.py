"""Synthetic check reads against the running ClickHouse (M6 Phase A).

The offline suite proves the code cannot construct a cross-tenant read. This one
proves the statements are valid against the real schema and that the numbers
they produce describe the readings actually stored: the phases a probe measured
sum to the duration it reported.

It fails when the stack is not up rather than skipping, because a skip here is
indistinguishable from a pass. `NEXOVUE_SKIP_LIVE_TESTS=1` opts out on purpose.

**Discovery is not the assertion.** Finding which tenant has readings needs an
unscoped query, which the repository rightly refuses, so it is done with the raw
client as fixture setup. Every assertion below goes through the chokepoint.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.synthetics import queries, service
from nexovue_api.synthetics.schemas import PHASE_ORDER

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_HTTP_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

# Wide enough to cover a machine that was asleep, narrow enough to stay inside
# a handful of daily partitions.
LOOKBACK: Final = timedelta(days=2)

# The prober publishes seconds with microsecond detail; the sum of five of them
# against the total it reported differs only by float addition order.
# Wide on purpose. libcurl's cumulative offsets stop composing when it retries
# inside a request, and the phases then overlap: of several hundred live probes
# two disagreed, both slow outliers, by 3-4%. An equality assertion failed on
# real data that was not wrong. What this still catches is a breakdown measuring
# a different request from the total beside it, or phases silently zero.
PHASE_SUM_TOLERANCE: Final = 0.10


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
    suite="apps/api/tests/test_synthetics_live.py",
    remedy=f"ClickHouse is not answering on {CLICKHOUSE_DSN} — start it with `just dev`",
)


@dataclass(frozen=True, slots=True)
class SyntheticSubject:
    """A tenant that actually has readings, and one that does not."""

    tenant_id: str
    project_id: str
    check_id: str
    other_tenant_id: str


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
async def subject(client: AsyncClient) -> SyntheticSubject:
    """The tenant with the most synthetic readings, plus a stranger's id.

    Raw client on purpose: asking "who has data" is a question no tenant-scoped
    query can ask, and it is setup rather than a claim about the API.
    """
    result = await client.query(
        f"""
        SELECT tenant_id, project_id, {queries.CHECK_ID} AS check_id, count() AS readings
        FROM metrics_gauge
        WHERE metric_name = %(metric)s AND timestamp >= %(since)s
        GROUP BY tenant_id, project_id, check_id
        ORDER BY readings DESC
        LIMIT 2
        """,
        parameters={"metric": queries.METRIC_STATUS, "since": _since()},
    )
    rows = [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
    assert rows, (
        "no synthetic readings in ClickHouse for the last two days. This suite "
        "asserts on real prober output, so an empty store is a failure here, not "
        "a pass — check that the scheduler is running."
    )

    strangers = {str(row["tenant_id"]) for row in rows} - {str(rows[0]["tenant_id"])}
    return SyntheticSubject(
        tenant_id=str(rows[0]["tenant_id"]),
        project_id=str(rows[0]["project_id"]),
        check_id=str(rows[0]["check_id"]),
        # A second real tenant is the sharper test; a random id proves the same
        # thing when the dev stack happens to hold only one.
        other_tenant_id=next(iter(strangers), str(uuid.uuid4())),
    )


def _since() -> datetime:
    return datetime.now(UTC) - LOOKBACK


def _window() -> tuple[datetime, datetime]:
    return _since(), datetime.now(UTC)


def _repository(client: AsyncClient, tenant_id: str) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=tenant_id)


async def test_a_check_that_ran_reports_its_runs_and_its_uptime(
    client: AsyncClient, subject: SyntheticSubject
) -> None:
    start, end = _window()

    results = await service.list_check_results(
        _repository(client, subject.tenant_id),
        project_id=subject.project_id,
        from_time=start,
        to_time=end,
    )

    checks = {check.check_id: check for check in results.checks}
    assert subject.check_id in checks, f"no summary for the check that has readings: {checks}"
    check = checks[subject.check_id]
    assert check.runs > 0
    assert 0.0 <= check.uptime_percent <= 100.0
    assert check.mean_duration_ms > 0.0
    assert check.up is not None


async def test_the_phase_breakdown_names_every_phase_of_a_request(
    client: AsyncClient, subject: SyntheticSubject
) -> None:
    """The reading that makes a check worth more than a ping."""
    start, end = _window()

    breakdown = await service.phase_breakdown(
        _repository(client, subject.tenant_id),
        project_id=subject.project_id,
        check_id=subject.check_id,
        from_time=start,
        to_time=end,
        bucket_seconds=service.resolve_bucket_seconds(start, end, None),
    )

    assert breakdown.buckets, "the check has status readings but no phase readings"
    assert set(breakdown.phases) == set(PHASE_ORDER)
    for bucket in breakdown.buckets:
        assert set(bucket.durations_ms) == set(PHASE_ORDER)
        assert bucket.total_ms == pytest.approx(sum(bucket.durations_ms.values()))


async def test_the_phases_of_a_probe_sum_to_the_duration_it_reported(
    client: AsyncClient, subject: SyntheticSubject
) -> None:
    """The arithmetic that says the breakdown describes the same request.

    A phase total that drifts from the reported duration means the two are
    measuring different things, and the chart would be attributing time to the
    wrong part of the request.
    """
    start, end = _window()

    runs = await service.list_runs(
        _repository(client, subject.tenant_id),
        project_id=subject.project_id,
        check_id=subject.check_id,
        from_time=start,
        to_time=end,
        limit=50,
    )

    successful = [run for run in runs.runs if run.up]
    assert successful, "no successful probe in the window to check the arithmetic against"
    for run in successful:
        assert run.phase_total_ms == pytest.approx(
            run.duration_ms, rel=PHASE_SUM_TOLERANCE, abs=1.0
        )


async def test_another_tenant_reads_none_of_these_checks(
    client: AsyncClient, subject: SyntheticSubject
) -> None:
    """Real rows, real ClickHouse, and the wrong tenant sees none of them."""
    start, end = _window()

    stolen = await service.list_check_results(
        _repository(client, subject.other_tenant_id),
        project_id=subject.project_id,
        from_time=start,
        to_time=end,
    )
    stolen_phases = await service.phase_breakdown(
        _repository(client, subject.other_tenant_id),
        project_id=subject.project_id,
        check_id=subject.check_id,
        from_time=start,
        to_time=end,
        bucket_seconds=300,
    )

    assert [check.check_id for check in stolen.checks] == []
    assert stolen_phases.buckets == []


async def test_a_window_with_no_readings_is_an_empty_list_and_not_an_error(
    client: AsyncClient, subject: SyntheticSubject
) -> None:
    """Against the real store: onboarding is a valid state, not a failure."""
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)

    results = await service.list_check_results(
        _repository(client, subject.tenant_id),
        project_id=subject.project_id,
        from_time=long_ago,
        to_time=long_ago + timedelta(hours=1),
    )

    assert results.checks == []


async def test_the_readings_this_suite_asserts_on_are_visible_in_the_store(
    client: AsyncClient, subject: SyntheticSubject
) -> None:
    """Names the counts the run reported, so a green result is not a mystery.

    Reads the raw client deliberately: this is the fixture's own evidence, not
    a second copy of the API's query.
    """
    result = await client.query(
        """
        SELECT metric_name, count() AS readings
        FROM metrics_gauge
        WHERE tenant_id = %(tenant_id)s AND timestamp >= %(since)s
        GROUP BY metric_name
        ORDER BY metric_name
        """,
        parameters={"tenant_id": subject.tenant_id, "since": _since()},
    )
    counts: dict[str, Any] = {row[0]: row[1] for row in result.result_rows}

    assert counts.get(queries.METRIC_STATUS, 0) > 0
    assert counts.get(queries.METRIC_DURATION, 0) > 0
    assert counts.get(queries.METRIC_PHASE_DURATION, 0) > 0
    # Five phase readings per probe, one status reading per probe.
    assert counts[queries.METRIC_PHASE_DURATION] == len(PHASE_ORDER) * counts[queries.METRIC_STATUS]


async def test_a_checks_buckets_account_for_every_run_its_summary_counted(
    client: AsyncClient, subject: SyntheticSubject
) -> None:
    """The sparkline and the uptime beside it must count the same probes.

    `runs` counts status points rather than rows, because one probe writes a status, a
    duration, five phases and a certificate. A trend that counted rows would report a
    check that ran once as having run eight times, and the row's own number would
    disagree with its line.
    """
    from_time, to_time = _window()
    repository = _repository(client, subject.tenant_id)

    summaries = await service.list_check_results(
        repository, project_id=subject.project_id, from_time=from_time, to_time=to_time
    )
    trends = await service.check_trends(
        repository, project_id=subject.project_id, from_time=from_time, to_time=to_time
    )

    summary = next(check for check in summaries.checks if check.check_id == subject.check_id)
    series = next(trend for trend in trends.checks if trend.check_id == subject.check_id)

    assert sum(point.runs for point in series.points) == summary.runs
    assert sum(point.successful_runs for point in series.points) == summary.successful_runs


async def test_a_bucket_nothing_succeeded_in_reports_no_duration_rather_than_zero(
    client: AsyncClient, subject: SyntheticSubject
) -> None:
    """A failed probe has no response time.

    Drawn as zero it would read as the site getting instantly fast at the moment it went
    down, which is the opposite of what happened. On a healthy stack there may be no
    failing bucket to find, so this asserts the invariant rather than the existence of a
    failure — it bites the first time one occurs.
    """
    from_time, to_time = _window()
    trends = await service.check_trends(
        _repository(client, subject.tenant_id),
        project_id=subject.project_id,
        from_time=from_time,
        to_time=to_time,
    )

    points = [point for trend in trends.checks for point in trend.points]
    assert points, "no synthetic buckets in the window; the scheduler is not running"

    for point in points:
        if point.successful_runs == 0:
            assert point.mean_duration_ms is None
        else:
            assert point.mean_duration_ms is not None


# A redelivered probe. The tests above assert on whatever the prober wrote; this
# one seeds the case they cannot rely on finding — a probe stored twice, which
# `metricwriting.Absorb` permits and development data has produced twice in three
# days. Too rare to wait for, too real to leave unproven.

_REDELIVERED_COLUMNS: Final = (
    "timestamp, start_timestamp, received_at, retention_days, tenant_id, project_id, "
    "service_name, scope_name, scope_version, metric_name, metric_unit, "
    "metric_description, resource_attrs, attrs, flags, value"
)

# The phases of one imagined request, in seconds, and the total it reported.
_SEEDED_PHASE_SECONDS: Final = {
    "dns": 0.004,
    "tcp": 0.011,
    "tls": 0.030,
    "ttfb": 0.050,
    "transfer": 0.005,
}
_SEEDED_DURATION_SECONDS: Final = sum(_SEEDED_PHASE_SECONDS.values())
_SEEDED_RETENTION_DAYS: Final = 30


@dataclass(frozen=True, slots=True)
class RedeliveredProbe:
    """One probe whose every point was written to ClickHouse twice."""

    tenant_id: str
    project_id: str
    check_id: str


@pytest.fixture
async def redelivered(client: AsyncClient) -> AsyncIterator[RedeliveredProbe]:
    probe = RedeliveredProbe(
        tenant_id=str(uuid.uuid4()), project_id=str(uuid.uuid4()), check_id=str(uuid.uuid4())
    )
    at = datetime.now(UTC).replace(microsecond=0)

    # Two blocks, not one statement run twice: ClickHouse drops a byte-identical
    # block on sight (ADR-0075, and observed the first time this fixture tried
    # it). The token models a re-batch — the same points in a block the store has
    # not seen, which is the shape a redelivery actually takes.
    rows = _probe_rows(probe, at=at)
    for attempt in ("first", "redelivered"):
        await client.command(
            _seed(rows),
            settings={"insert_deduplication_token": f"{probe.check_id}-{attempt}"},
        )
    try:
        yield probe
    finally:
        await client.command(
            f"ALTER TABLE metrics_gauge DELETE WHERE tenant_id = {_sql_string(probe.tenant_id)}",
            settings={"mutations_sync": 2},
        )


async def test_a_probe_written_twice_reports_the_phase_total_it_measured_once(
    client: AsyncClient, redelivered: RedeliveredProbe
) -> None:
    """The reader owes the deduplication the writer deliberately does not do.

    Summing the stored rows would report twice the time the request took, and the
    phase breakdown would then accuse every phase of time the request never spent
    there — on a probe that was fine.
    """
    from_time, to_time = _window()

    runs = await service.list_runs(
        _repository(client, redelivered.tenant_id),
        project_id=redelivered.project_id,
        check_id=redelivered.check_id,
        from_time=from_time,
        to_time=to_time,
        limit=50,
    )

    assert len(runs.runs) == 1, "one probe was written, twice; it is still one run"
    run = runs.runs[0]
    assert run.up is True
    assert run.duration_ms == pytest.approx(_SEEDED_DURATION_SECONDS * 1000.0, abs=0.001)
    assert run.phase_total_ms == pytest.approx(_SEEDED_DURATION_SECONDS * 1000.0, abs=0.001)


async def test_the_redelivered_rows_really_are_duplicated_in_the_store(
    client: AsyncClient, redelivered: RedeliveredProbe
) -> None:
    """The fixture's own premise, proved rather than assumed.

    Without this, ClickHouse silently dropping the second write would leave the
    test above passing for the wrong reason — the shape of a green suite that
    never exercised the bug.
    """
    result = await client.query(
        """
        SELECT metric_name, count() AS rows
        FROM metrics_gauge
        WHERE tenant_id = %(tenant)s
        GROUP BY metric_name
        """,
        parameters={"tenant": redelivered.tenant_id},
    )
    stored = {str(name): int(rows) for name, rows in result.result_rows}

    assert stored[queries.METRIC_STATUS] == 2
    assert stored[queries.METRIC_DURATION] == 2
    assert stored[queries.METRIC_PHASE_DURATION] == 2 * len(PHASE_ORDER)


def _probe_rows(probe: RedeliveredProbe, *, at: datetime) -> list[str]:
    """One probe: a status point, a duration point, and one point per phase."""
    resource = {"synthetic": {"check": {"id": probe.check_id, "type": "http"}}}
    rows = [
        _seed_row(
            probe,
            at=at,
            resource=resource,
            metric_name=queries.METRIC_STATUS,
            attributes={"http": {"response": {"status_code": 200}}},
            value=float(queries.UP_VALUE),
        ),
        _seed_row(
            probe,
            at=at,
            resource=resource,
            metric_name=queries.METRIC_DURATION,
            attributes={},
            value=_SEEDED_DURATION_SECONDS,
        ),
    ]
    rows.extend(
        _seed_row(
            probe,
            at=at,
            resource=resource,
            metric_name=queries.METRIC_PHASE_DURATION,
            attributes={"synthetic": {"check": {"step": phase}}},
            value=seconds,
        )
        for phase, seconds in _SEEDED_PHASE_SECONDS.items()
    )
    return rows


def _seed(rows: list[str]) -> str:
    return f"INSERT INTO metrics_gauge ({_REDELIVERED_COLUMNS}) VALUES\n" + ",\n".join(rows)


def _seed_row(
    probe: RedeliveredProbe,
    *,
    at: datetime,
    resource: dict[str, Any],
    metric_name: str,
    attributes: dict[str, Any],
    value: float,
) -> str:
    stamp = _sql_string(at.strftime("%Y-%m-%d %H:%M:%S"))
    fields = [
        stamp,
        stamp,
        stamp,
        str(_SEEDED_RETENTION_DAYS),
        _sql_string(probe.tenant_id),
        _sql_string(probe.project_id),
        _sql_string("nexovue-prober"),
        _sql_string("nexovue.scheduler.checks.http"),
        _sql_string(""),
        _sql_string(metric_name),
        _sql_string("s"),
        _sql_string(""),
        _sql_string(json.dumps(resource)),
        _sql_string(json.dumps(attributes)),
        "0",
        repr(float(value)),
    ]
    return "(" + ", ".join(fields) + ")"


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
