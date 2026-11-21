"""Metric series against the real ClickHouse (F20, plan Step 8).

**T8.1** first records whether the metrics screen's two histogram reads, `HISTOGRAM_SQL`
and `HEATMAP_SQL`, subtract one instance's running total from another's when two
instances of one service report equal attributes (design section 8, *A separate suspected
bug*). They do: each is a strict `xfail` here asserting the true rise, so the day the bug
record [`metrics-histogram-subtracts-across-instances`] is fixed the case fails and must be
turned into a plain test. Not fixed here (plan T8.1).

The rest (T8.2, T8.3) proves the `metric_series` panel and `GET /metrics/{name}/series`
against a fixture this suite writes itself to `metrics_sum` and `metrics_gauge`: a restart,
two equal-attribute instances, two units, a changed attribute set, and more series than the
cap, each with its expected value worked out here (design section 8, *Testing*).

**No clock.** Every row is stamped inside one fixed window, on a fixed day, with a 10-year
retention so the store's TTL cannot age it out; each tenant is new and its rows are
deleted at the end of the run (`delete_after_run`).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack
from sqlalchemy.ext.asyncio import AsyncSession
from test_dashboard_panels_live import _app_client, _dashboard, _panel
from test_findings_cases_live import Team, _reachable, _team, db  # noqa: F401

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import ServiceLastSeen
from nexovue_api.metrics import service as metrics
from nexovue_api.settings import API_PREFIX

READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
WRITER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)

require_stack(
    reachable=_reachable(READER_DSN.replace("clickhouse://", "http://"), 8123),
    suite="apps/api/tests/test_metric_series_live.py",
    remedy="ClickHouse is not reachable — run `just dev`",
)

# A fixed day, far from any edge, and rows kept 10 years: nothing here reads the clock.
DAY: Final = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
RETENTION_DAYS: Final = 3650
WINDOW: Final = (DAY, DAY + timedelta(minutes=10))
BEFORE_WINDOW: Final = DAY - timedelta(hours=1)
DURATION_METRIC: Final = "http.server.request.duration"
BOUNDS: Final = [10.0, 100.0]


@dataclass(frozen=True, slots=True)
class Tenant:
    org_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = field(default_factory=lambda: str(uuid.uuid4()))


async def _connect(dsn: str) -> AsyncClient:
    parts = urlsplit(dsn)
    return await clickhouse_connect.get_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=parts.path.lstrip("/"),
    )


@pytest.fixture
async def reader() -> AsyncIterator[AsyncClient]:
    client = await _connect(READER_DSN)
    try:
        yield client
    finally:
        await client.close()


def _tenant(*tables: str) -> Tenant:
    tenant = Tenant()
    for table in tables:
        delete_after_run(table, tenant.org_id)
    return tenant


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


async def _write(table: str, rows: list[dict[str, Any]]) -> None:
    """Rows as the workers write them, in one insert, `JSONEachRow` so attributes stay JSON."""
    writer = await _connect(WRITER_DSN)
    try:
        lines = "\n".join(json.dumps(row) for row in rows).encode()
        await writer.raw_insert(table, list(rows[0]), insert_block=lines, fmt="JSONEachRow")
    finally:
        await writer.close()


def _base(tenant: Tenant, service: str, metric: str, at: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "timestamp": _stamp(at),
        "start_timestamp": _stamp(extra.pop("started", BEFORE_WINDOW)),
        "received_at": _stamp(at),
        "retention_days": RETENTION_DAYS,
        "tenant_id": tenant.org_id,
        "project_id": tenant.project_id,
        "service_name": service,
        "scope_name": "",
        "scope_version": "",
        "metric_name": metric,
        "metric_unit": extra.pop("unit", ""),
        "metric_description": "",
        "resource_attrs": extra.pop("resource", {}),
        "attrs": extra.pop("attrs", {}),
        "flags": 0,
        **extra,
    }


def _repository(reader: AsyncClient, tenant: Tenant) -> TenantScopedRepository:
    return TenantScopedRepository(reader, tenant_id=tenant.org_id, project_id=tenant.project_id)


# -------------------------------------------------------------------- T8.1

# Two instances of `api` with equal attributes, cumulative: (offset from the window's start,
# count). Each instance's own rise in the window is 60 and 30, so the true rise is 90.
INSTANCE_POINTS: Final = {
    "a": (
        (timedelta(seconds=-40), 90),
        (timedelta(seconds=5), 100),
        (timedelta(minutes=5, seconds=5), 160),
    ),
    "b": (
        (timedelta(seconds=-50), 990),
        (timedelta(seconds=10), 1000),
        (timedelta(minutes=5, seconds=10), 1030),
    ),
}
TRUE_RISE_IN_WINDOW: Final = 90
# Per one-minute column of the heatmap: the 10:00 column (10 + 10) and the 10:05 one (60 + 30).
TRUE_COLUMNS: Final = {DAY: 20, DAY + timedelta(minutes=5): 90}


async def _two_instances_of_one_histogram(tenant: Tenant) -> None:
    rows = []
    for instance, points in INSTANCE_POINTS.items():
        for offset, count in points:
            # The buckets hold the count, so a column's total is the count's rise.
            rows.append(
                _base(
                    tenant,
                    "api",
                    DURATION_METRIC,
                    DAY + offset,
                    unit="ms",
                    resource={"service.instance.id": instance},
                    attrs={"http.route": "/orders"},
                    count=count,
                    sum=float(count),
                    bucket_counts=[count, 0, 0],
                    explicit_bounds=BOUNDS,
                    min=1.0,
                    max=5.0,
                    aggregation_temporality="CUMULATIVE",
                )
            )
    await _write("metrics_histogram", rows)


@pytest.mark.xfail(
    strict=True,
    reason="HISTOGRAM_SQL groups by series_hash only, so two instances' totals are "
    "subtracted from each other (docs/changes/metrics-histogram-subtracts-across-instances)",
)
async def test_t8_1_histogram_sql_reads_each_instance_s_own_rise(reader: AsyncClient) -> None:
    tenant = _tenant("metrics_histogram")
    await _two_instances_of_one_histogram(tenant)

    answer = await metrics.read_histogram(
        _repository(reader, tenant),
        project_id=tenant.project_id,
        metric_name=DURATION_METRIC,
        from_time=WINDOW[0],
        to_time=WINDOW[1],
    )

    [layout] = answer.layouts
    print(f"T8.1 HISTOGRAM_SQL: count {layout.count}, true {TRUE_RISE_IN_WINDOW}")  # noqa: T201
    assert layout.count == TRUE_RISE_IN_WINDOW


@pytest.mark.xfail(
    strict=True,
    reason="HEATMAP_SQL partitions by series_hash within one service, so a column subtracts "
    "one instance's state from another's "
    "(docs/changes/metrics-histogram-subtracts-across-instances)",
)
async def test_t8_1_heatmap_sql_reads_each_instance_s_own_rise(reader: AsyncClient) -> None:
    tenant = _tenant("metrics_histogram")
    await _two_instances_of_one_histogram(tenant)

    answer = await metrics.read_duration_heatmap(
        _repository(reader, tenant),
        project_id=tenant.project_id,
        service_name="api",
        from_time=WINDOW[0],
        to_time=WINDOW[1],
    )

    columns = {column.bucket: column.total for column in answer.columns}
    print(f"T8.1 HEATMAP_SQL: columns {columns}, true {TRUE_COLUMNS}")  # noqa: T201
    assert columns == TRUE_COLUMNS


# -------------------------------------------------------------------- T8.2

SERVICE: Final = "shop"
COUNTER: Final = "orders_processed"
STEP: Final = timedelta(seconds=30)
BUCKET: Final = timedelta(minutes=1)
# Points from two minutes before the window to its end, every 30 seconds.
FIRST_POINT: Final = DAY - timedelta(minutes=2)
POINTS: Final = int((WINDOW[1] - FIRST_POINT) / STEP)
# One restart inside a bucket, one on a bucket's first reading: two rules (design section 8).
RESTART_AT: Final = DAY + timedelta(minutes=4, seconds=30)
RESTART_ON_THE_BUCKET: Final = DAY + timedelta(minutes=6)
EU: Final = {"region": "eu"}
US: Final = {"region": "us"}
APAC: Final = {"region": "apac"}


@dataclass(frozen=True, slots=True)
class CounterSeries:
    """One series of the counter fixture: its instance, attributes, and readings."""

    instance: str
    attrs: dict[str, str]
    readings: tuple[tuple[datetime, float], ...]
    restarted_at: datetime | None = None


def _counting(
    start: float, step: float, *, restart_to: float | None = None, at: datetime = RESTART_AT
) -> tuple[tuple[datetime, float], ...]:
    """A running total from `start`, rising `step` a reading; restarted at `at` to
    `restart_to` when given, rising again from there."""
    readings = []
    value = start
    restart = at
    for index in range(POINTS):
        at = FIRST_POINT + index * STEP
        if restart_to is not None and at == restart:
            value = restart_to
        elif index:
            value += step
        readings.append((at, value))
    return tuple(readings)


COUNTER_SERIES: Final = (
    CounterSeries("a", EU, _counting(1_000, 5, restart_to=3), RESTART_AT),
    # The same attributes from another instance: a series of its own (AC50).
    CounterSeries("b", EU, _counting(500, 2)),
    CounterSeries("a", US, _counting(50, 1)),
    CounterSeries(
        "c",
        APAC,
        _counting(2_000, 4, restart_to=6, at=RESTART_ON_THE_BUCKET),
        RESTART_ON_THE_BUCKET,
    ),
)


def _true_rise(series: CounterSeries) -> dict[datetime, float]:
    """Each in-window bucket's real rise, worked out reading by reading: a fall is a
    restart, and the reading after it is what the counter rose since (AC48, AC49)."""
    rise: dict[datetime, float] = {}
    for (_, before), (at, value) in zip(series.readings, series.readings[1:], strict=False):
        bucket = DAY + BUCKET * ((at - DAY) // BUCKET)
        if bucket >= DAY:
            rise[bucket] = rise.get(bucket, 0.0) + (value - before if value >= before else value)
    return rise


def _added(*rises: dict[datetime, float]) -> dict[datetime, float]:
    total: dict[datetime, float] = {}
    for rise in rises:
        for bucket, value in rise.items():
            total[bucket] = total.get(bucket, 0.0) + value
    return total


async def _write_counter(tenant: Tenant) -> None:
    rows = [
        _base(
            tenant,
            SERVICE,
            COUNTER,
            at,
            unit="1",
            resource={"service.instance.id": series.instance},
            attrs=series.attrs,
            # A restart begins a new accumulation, and says so in its start time.
            started=series.restarted_at
            if series.restarted_at is not None and at >= series.restarted_at
            else BEFORE_WINDOW,
            value=value,
            aggregation_temporality="CUMULATIVE",
            is_monotonic=True,
        )
        for series in COUNTER_SERIES
        for at, value in series.readings
    ]
    # A redelivered batch is kept, so one reading arrives twice.
    await _write("metrics_sum", [*rows, rows[len(rows) // 2]])


async def _series_hash(reader: AsyncClient, attrs: dict[str, str]) -> str:
    """The store's own hash of an attribute set, as the series list returns it."""
    result = await reader.query(
        "SELECT toString(cityHash64(toJSONString(CAST({attrs:String} AS JSON)))) AS hash",
        parameters={"attrs": json.dumps(attrs)},
    )
    return str(result.result_rows[0][0])


async def _line(
    reader: AsyncClient,
    tenant: Tenant,
    metric: str,
    *,
    shape: str = "sum",
    service: str | None = SERVICE,
    series_hash: str | None = None,
    aggregation: str = "sum",
) -> metrics.MetricLine:
    return await metrics.metric_series(
        _repository(reader, tenant),
        project_id=tenant.project_id,
        metric_name=metric,
        shape=shape,  # type: ignore[arg-type]
        service_name=service,
        series_hash=series_hash,
        aggregation=aggregation,  # type: ignore[arg-type]
        from_time=WINDOW[0],
        to_time=WINDOW[1],
    )


async def test_ac48_ac49_a_counter_draws_its_real_rise_with_no_spike_at_a_restart(
    reader: AsyncClient,
) -> None:
    tenant = _tenant("metrics_sum")
    await _write_counter(tenant)

    line = await _line(reader, tenant, COUNTER)

    expected = _added(*(_true_rise(series) for series in COUNTER_SERIES))
    assert dict(line.points) == pytest.approx(expected)
    assert sum(value for _, value in line.points) == pytest.approx(sum(expected.values()))
    # Each restart's bucket reads what was really counted, never a fall or a whole history.
    for restart_bucket in (DAY + timedelta(minutes=4), RESTART_ON_THE_BUCKET):
        assert dict(line.points)[restart_bucket] == pytest.approx(expected[restart_bucket])
    assert max(value for _, value in line.points) < 30
    assert (line.series, line.units) == (4, ["1"])


async def test_ac50_two_instances_with_equal_attributes_are_two_series(
    reader: AsyncClient,
) -> None:
    tenant = _tenant("metrics_sum")
    await _write_counter(tenant)
    eu = await _series_hash(reader, EU)

    added = await _line(reader, tenant, COUNTER, series_hash=eu)
    mean = await _line(reader, tenant, COUNTER, series_hash=eu, aggregation="mean")

    eu_a, eu_b = (_true_rise(series) for series in COUNTER_SERIES[:2])
    assert added.series == 2
    assert dict(added.points) == pytest.approx(_added(eu_a, eu_b))
    assert dict(mean.points) == pytest.approx(
        {bucket: (eu_a[bucket] + eu_b[bucket]) / 2 for bucket in eu_a}
    )
    # A chosen series is labelled by its attributes at read time (FR61).
    assert [(item.key, item.value) for item in added.attributes] == [("region", "eu")]


GAUGE: Final = "queue.depth"
# Two series, a reading every 20 s: each bucket's three readings average to these.
GAUGE_SERIES: Final = {
    "q1": (2.0, 4.0, 6.0),
    "q2": (10.0, 20.0, 30.0),
}
GAUGE_EVERY: Final = timedelta(seconds=20)


async def _write_gauge(tenant: Tenant) -> None:
    rows = [
        _base(
            tenant,
            SERVICE,
            GAUGE,
            DAY + minute * BUCKET + index * GAUGE_EVERY,
            unit="{jobs}",
            attrs={"queue": queue},
            value=reading,
        )
        for queue, readings in GAUGE_SERIES.items()
        for minute in range(10)
        for index, reading in enumerate(readings)
    ]
    # A redelivered reading would pull a plain mean towards itself.
    await _write("metrics_gauge", [*rows, rows[0], rows[0], rows[0]])


@pytest.mark.parametrize(
    ("aggregation", "expected"),
    [("mean", (4.0 + 20.0) / 2), ("max", 20.0), ("min", 4.0), ("sum", 24.0)],
)
async def test_a_gauge_is_each_series_mean_then_the_chosen_aggregation(
    reader: AsyncClient, aggregation: str, expected: float
) -> None:
    tenant = _tenant("metrics_gauge")
    await _write_gauge(tenant)

    line = await _line(reader, tenant, GAUGE, shape="gauge", aggregation=aggregation)

    assert [at for at, _ in line.points] == [DAY + minute * BUCKET for minute in range(10)]
    assert [value for _, value in line.points] == [pytest.approx(expected)] * 10
    assert (line.series, line.units, line.attributes) == (2, ["{jobs}"], [])


async def test_a_delta_sum_adds_each_reading_once(reader: AsyncClient) -> None:
    tenant = _tenant("metrics_sum")
    rows = [
        _base(
            tenant,
            SERVICE,
            "jobs.finished",
            DAY + index * STEP,
            value=4.0,
            aggregation_temporality="DELTA",
            is_monotonic=True,
        )
        for index in range(20)
    ]
    await _write("metrics_sum", [*rows, rows[3]])

    line = await _line(reader, tenant, "jobs.finished")

    assert {value for _, value in line.points} == {8.0}
    assert len(line.points) == 10


async def test_ac51_two_units_in_the_window_are_both_said(reader: AsyncClient) -> None:
    tenant = _tenant("metrics_gauge")
    await _write(
        "metrics_gauge",
        [
            _base(
                tenant, SERVICE, "payload.size", DAY, unit="By", attrs={"route": "/a"}, value=1.0
            ),
            _base(
                tenant, SERVICE, "payload.size", DAY, unit="KiBy", attrs={"route": "/b"}, value=1.0
            ),
        ],
    )

    line = await _line(reader, tenant, "payload.size", shape="gauge")

    assert line.units == ["By", "KiBy"]


async def test_ac52_every_series_is_counted_past_the_cap(reader: AsyncClient) -> None:
    tenant = _tenant("metrics_gauge")
    beyond = metrics.MAX_COMBINED_SERIES + 1
    await _write(
        "metrics_gauge",
        [
            _base(tenant, SERVICE, "cache.entries", DAY, attrs={"key": str(index)}, value=1.0)
            for index in range(beyond)
        ],
    )

    line = await _line(reader, tenant, "cache.entries", shape="gauge")

    assert line.series == beyond


async def test_a_metric_reports_under_other_attributes_or_not_at_all(reader: AsyncClient) -> None:
    tenant = _tenant("metrics_sum")
    await _write_counter(tenant)
    repository = _repository(reader, tenant)

    async def reports(metric: str, service: str | None) -> bool:
        return await metrics.metric_reports(
            repository,
            project_id=tenant.project_id,
            metric_name=metric,
            shape="sum",
            service_name=service,
            from_time=WINDOW[0],
            to_time=WINDOW[1],
        )

    assert await reports(COUNTER, SERVICE)
    assert await reports(COUNTER, None)
    assert not await reports(COUNTER, "another")
    assert not await reports("never.sent", SERVICE)


# ------------------------------------------------- T8.2, T8.3 through the real app

METRIC_TABLES: Final = ("metrics_sum", "metrics_gauge")
EARLIER: Final = (DAY - timedelta(hours=1), DAY - timedelta(minutes=30))


@pytest.fixture
async def team(db: AsyncSession) -> Team:  # noqa: F811
    """A real organization and project, whose metric rows go at the end of the run."""
    made = await _team(db, f"Series{uuid.uuid4().hex[:8]}")
    for table in METRIC_TABLES:
        delete_after_run(table, str(made.org_id))
    db.add(
        ServiceLastSeen(
            org_id=made.org_id,
            project_id=made.project_id,
            service_name=SERVICE,
            last_seen_at=WINDOW[1],
        )
    )
    await db.flush()
    return made


def _as_tenant(team: Team) -> Tenant:
    return Tenant(org_id=str(team.org_id), project_id=str(team.project_id))


def _window(start: datetime, end: datetime) -> dict[str, str]:
    return {"from": start.isoformat(), "to": end.isoformat()}


def _series_panel(panel_id: str, series: str | None, **settings: Any) -> dict[str, Any]:
    return _panel(
        panel_id,
        "metric_series",
        **(
            {
                "metric": COUNTER,
                "shape": "sum",
                "service": SERVICE,
                "series": series,
                "aggregation": "sum",
            }
            | settings
        ),
    )


async def test_ac83_two_label_values_draw_as_separately_chosen_panels(
    db: AsyncSession,  # noqa: F811
    team: Team,
    reader: AsyncClient,
) -> None:
    await _write_counter(_as_tenant(team))

    async with _app_client(db, team, team.member, reader) as client:
        listed = await client.get(
            f"{API_PREFIX}/metrics/{COUNTER}/series",
            params={"project_id": str(team.project_id), "service_name": SERVICE, "shape": "sum"}
            | _window(*WINDOW),
        )
        assert listed.status_code == 200, listed.text
        by_region = {
            entry["attributes"][0]["value"]: entry["series_hash"]
            for entry in listed.json()["series"]
        }
        dashboard = await _dashboard(
            db,
            team,
            _series_panel("p_eu", by_region["eu"]),
            _series_panel("p_us", by_region["us"]),
        )
        batch = (
            await client.get(
                f"{API_PREFIX}/dashboard-panels/read",
                params={"dashboard_id": str(dashboard.id), "project_id": str(team.project_id)}
                | _window(*WINDOW),
            )
        ).json()

    assert listed.json()["truncated"] is False
    assert sorted(by_region) == ["apac", "eu", "us"]
    eu, us = batch["panels"]["p_eu"], batch["panels"]["p_us"]
    assert (eu["state"], us["state"]) == ("ok", "ok")
    assert (eu["series_combined"], us["series_combined"]) == (2, 1)
    eu_a, eu_b, us_a = (_true_rise(series) for series in COUNTER_SERIES[:3])

    def drawn(panel: dict[str, Any]) -> dict[datetime, float]:
        return {datetime.fromisoformat(at): value for at, value in panel["series"][0]["points"]}

    assert drawn(eu) == pytest.approx(_added(eu_a, eu_b))
    assert drawn(us) == pytest.approx(us_a)
    assert eu["series"][0]["attributes"] == [{"key": "region", "value": "eu"}]


async def test_ac115_a_metric_gaining_an_attribute_reads_series_changed_and_the_list_counts_it(
    db: AsyncSession,  # noqa: F811
    team: Team,
    reader: AsyncClient,
) -> None:
    tenant = _as_tenant(team)
    before = {"queue": "q1"}
    after = {"queue": "q1", "shard": "2"}
    await _write(
        "metrics_sum",
        [
            *(
                _base(
                    tenant,
                    SERVICE,
                    "jobs_done",
                    EARLIER[0] + index * STEP,
                    attrs=before,
                    value=float(index),
                    aggregation_temporality="CUMULATIVE",
                    is_monotonic=True,
                )
                for index in range(4)
            ),
            *(
                _base(
                    tenant,
                    SERVICE,
                    "jobs_done",
                    DAY + index * STEP,
                    attrs=after,
                    value=float(index),
                    aggregation_temporality="CUMULATIVE",
                    is_monotonic=True,
                )
                for index in range(4)
            ),
        ],
    )

    async with _app_client(db, team, team.member, reader) as client:
        earlier = (
            await client.get(
                f"{API_PREFIX}/metrics/jobs_done/series",
                params={"project_id": str(team.project_id), "service_name": SERVICE, "shape": "sum"}
                | _window(*EARLIER),
            )
        ).json()
        [old] = earlier["series"]
        dashboard = await _dashboard(
            db,
            team,
            _series_panel("p_old", old["series_hash"], metric="jobs_done"),
            _series_panel("p_gone", old["series_hash"], metric="never_sent"),
        )
        batch = (
            await client.get(
                f"{API_PREFIX}/dashboard-panels/read",
                params={"dashboard_id": str(dashboard.id), "project_id": str(team.project_id)}
                | _window(*WINDOW),
            )
        ).json()
        listed = (await client.get(f"{API_PREFIX}/dashboards")).json()

    assert batch["panels"]["p_old"] == {"state": "series_changed", "kind": "metric_series"}
    # A metric with no rows at all has not reported: the service's record says when it was.
    assert batch["panels"]["p_gone"]["state"] == "empty"
    [row] = [row for row in listed["dashboards"] if row["id"] == str(dashboard.id)]
    assert row["panels_not_reading"] == 1


async def test_ac121_the_list_binds_one_service_caps_its_combinations_and_its_window(
    db: AsyncSession,  # noqa: F811
    team: Team,
    reader: AsyncClient,
) -> None:
    tenant = _as_tenant(team)
    beyond = metrics.MAX_SERIES_COMBINATIONS + 1
    await _write(
        "metrics_gauge",
        [
            *(
                _base(tenant, SERVICE, "cache.entries", DAY, attrs={"key": str(index)}, value=1.0)
                for index in range(beyond)
            ),
            _base(tenant, "another", "cache.entries", DAY, attrs={"key": "elsewhere"}, value=1.0),
        ],
    )
    asked = {"project_id": str(team.project_id), "service_name": SERVICE, "shape": "gauge"}

    async with _app_client(db, team, team.member, reader) as client:
        capped = await client.get(
            f"{API_PREFIX}/metrics/cache.entries/series", params=asked | _window(*WINDOW)
        )
        too_wide = await client.get(
            f"{API_PREFIX}/metrics/cache.entries/series",
            params=asked | _window(WINDOW[1] - timedelta(days=7, minutes=1), WINDOW[1]),
        )
        seven_days = await client.get(
            f"{API_PREFIX}/metrics/cache.entries/series",
            params=asked | _window(WINDOW[1] - timedelta(days=7), WINDOW[1]),
        )
        histogram = await client.get(
            f"{API_PREFIX}/metrics/cache.entries/series",
            params=asked | {"shape": "histogram"} | _window(*WINDOW),
        )

    answer = capped.json()
    assert len(answer["series"]) == metrics.MAX_SERIES_COMBINATIONS
    assert answer["truncated"] is True
    assert "elsewhere" not in capped.text
    # Every hash is a decimal string, never a JSON number a browser would round.
    assert all(entry["series_hash"].isdigit() for entry in answer["series"])
    assert too_wide.status_code == 422, too_wide.text
    assert too_wide.json()["max_days"] == metrics.SERIES_LIST_MAX_DAYS
    assert seven_days.status_code == 200
    assert histogram.status_code == 422
