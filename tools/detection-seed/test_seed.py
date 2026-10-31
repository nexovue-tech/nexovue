"""The seeded history's shape, its reproducibility and its buckets, without a store."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from seed import (
    LATENCY_BOUNDS,
    LATENCY_BUCKET_COUNT,
    Row,
    generate,
    latency_bucket,
    main,
    require_local,
    spread,
    window,
)

SERVICES_GO = (
    Path(__file__).resolve().parents[2] / "apps/workers/internal/pipeline/derive/services.go"
)
# A Wednesday and the Saturday after it, both at 14:00 UTC.
WEDNESDAY = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)
SATURDAY = datetime(2026, 10, 31, 14, 0, tzinfo=UTC)


def _hour(at: datetime, seed: int = 1) -> list[Row]:
    end = at + timedelta(minutes=59)
    return list(generate(["checkout", "frontend"], at, end, UTC, 300.0, seed))


def _go_constant(source: str, name: str) -> float:
    found = re.search(rf"\b{name}\s*=\s*([\d_.]+)", source)
    assert found, f"{name} is no longer a literal in services.go"
    return float(found.group(1).replace("_", ""))


def test_the_bucket_edges_are_the_workers() -> None:
    source = SERVICES_GO.read_text()
    count = int(_go_constant(source, "LatencyBucketCount"))
    floor = _go_constant(source, "latencyFloorMS")
    ceiling = _go_constant(source, "latencyCeilingMS")
    # The formula itself, so a change to how the bounds are placed fails here too.
    assert "math.Pow(latencyCeilingMS/latencyFloorMS, float64(i+1)/LatencyBucketCount)" in source
    assert "sort.SearchFloat64s(latencyBucketBounds[:], durationMS)" in source

    assert count == LATENCY_BUCKET_COUNT
    assert len(LATENCY_BOUNDS) == count - 1
    for i, bound in enumerate(LATENCY_BOUNDS):
        want = floor * math.pow(ceiling / floor, (i + 1) / count)
        assert abs(bound - want) <= want * 1e-12
        # A bucket includes its upper bound, as SearchFloat64s places it.
        assert latency_bucket(bound) == i
        assert latency_bucket(math.nextafter(bound, math.inf)) == i + 1
    assert [latency_bucket(ms) for ms in (math.nan, -3.0, 0.0, 120_000.0)] == [0, 0, 0, 63]


def test_a_weekday_afternoon_is_busier_than_the_same_hour_at_the_weekend() -> None:
    weekday, weekend = _hour(WEDNESDAY), _hour(SATURDAY)
    for service in ("checkout", "frontend"):
        busy = sum(r.request_count for r in weekday if r.service_name == service)
        quiet = sum(r.request_count for r in weekend if r.service_name == service)
        assert busy > 1.5 * quiet


def test_the_afternoon_is_busier_than_the_night() -> None:
    night = _hour(WEDNESDAY.replace(hour=3))
    assert sum(r.request_count for r in _hour(WEDNESDAY)) > 3 * sum(r.request_count for r in night)


def test_the_same_seed_writes_the_same_rows_and_another_does_not() -> None:
    assert _hour(WEDNESDAY, seed=7) == _hour(WEDNESDAY, seed=7)
    assert _hour(WEDNESDAY, seed=7) != _hour(WEDNESDAY, seed=8)


def test_every_request_is_in_one_bucket_and_failures_are_rare() -> None:
    rows = _hour(WEDNESDAY)
    assert rows
    for row in rows:
        assert row.request_count > 0
        assert sum(row.latency_buckets) == row.request_count
        assert len(row.latency_buckets) == LATENCY_BUCKET_COUNT
        assert 0 <= row.error_count <= row.request_count
    assert sum(r.error_count for r in rows) < 0.05 * sum(r.request_count for r in rows)


def test_a_large_minute_spreads_around_its_median() -> None:
    import random

    buckets = spread(random.Random(1), 10_000, math.log(50.0), 0.5)
    assert sum(buckets) == 10_000
    assert max(range(LATENCY_BUCKET_COUNT), key=lambda i: buckets[i]) == latency_bucket(50.0)
    assert sum(1 for count in buckets if count) > 5


def test_the_history_ends_before_the_live_minutes() -> None:
    now = datetime(2026, 10, 31, 12, 30, 41, tzinfo=UTC)
    start, end = window(now, 4, None)
    assert end == datetime(2026, 10, 31, 12, 25, tzinfo=UTC)
    assert end - start == timedelta(weeks=4)

    live_from = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
    assert window(now, 4, live_from) == (start, live_from - timedelta(minutes=1))
    with pytest.raises(SystemExit, match="seeded already"):
        window(now, 4, start)


@pytest.mark.parametrize(
    "url",
    ["http://nexovue:pw@clickhouse.example.com:8123/nexovue", "http://10.0.0.5:18123/nexovue"],
)
def test_any_host_but_localhost_is_refused(url: str) -> None:
    with pytest.raises(SystemExit, match="refusing"):
        require_local(url, "ClickHouse")
    with pytest.raises(SystemExit, match="refusing"):
        main(["--clickhouse", url])
    with pytest.raises(SystemExit, match="refusing Postgres"):
        main(["--postgres", url.replace("http", "postgresql")])


def test_localhost_is_allowed() -> None:
    for host in ("localhost", "127.0.0.1", "[::1]"):
        require_local(f"http://{host}:18123/nexovue", "ClickHouse")
