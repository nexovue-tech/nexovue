"""The seeded history's shape, its reproducibility and its buckets, without a store.

F19 plan T2.6: endpoint rows that add up to their service's, with a `/health`-style
probe every busy minute, and check readings holding every kind of run the uptime rule
tells apart: confirmed, overruled, in maintenance, and `prober_error`.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from seed import (
    HEALTH_ENDPOINT,
    LATENCY_BOUNDS,
    LATENCY_BUCKET_COUNT,
    Row,
    check_runs,
    endpoint_rows,
    generate,
    latency_bucket,
    live_peaks,
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


def test_the_history_sits_at_each_service_s_live_rate() -> None:
    """A random rate read as a traffic fall on the demo: checkout really sends 4 a minute and
    its seeded normal said 21, so every comparison opened a false finding."""
    now = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    peaks = live_peaks({"checkout": 4.0, "frontend": 400.0}, now, UTC)
    start = now - timedelta(days=7, minutes=30)
    rows = list(
        generate(["checkout", "frontend"], start, start + timedelta(hours=1), UTC, 120.0, 6, peaks)
    )

    def rate(service: str) -> float:
        counts = [row.request_count for row in rows if row.service_name == service]
        return sum(counts) / 60

    assert rate("checkout") == pytest.approx(4.0, rel=0.25)
    assert rate("frontend") == pytest.approx(400.0, rel=0.1)


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


# ------------------------------------------------------------- F19, plan T2.6


def test_endpoint_rows_add_up_to_their_service_and_a_probe_comes_every_busy_minute() -> None:
    rows = _hour(WEDNESDAY)
    endpoints = list(endpoint_rows(rows))
    probed = 0
    for row in rows:
        mine = [
            e for e in endpoints if e.service_name == row.service_name and e.minute == row.minute
        ]
        assert sum(e.request_count for e in mine) == row.request_count
        assert sum(e.error_count for e in mine) == row.error_count
        assert all(e.request_count > 0 and e.span_kind == "SERVER" for e in mine)
        probes = [e for e in mine if e.span_name == HEALTH_ENDPOINT]
        # A probe never fails, so a minute whose every request failed has none.
        assert len(probes) <= 1 and all(probe.error_count == 0 for probe in probes)
        probed += len(probes)
    assert probed >= 0.9 * len(rows)
    assert len({e.span_name for e in endpoints}) > 2


def test_check_readings_hold_every_kind_of_run_the_uptime_rule_tells_apart() -> None:
    start = WEDNESDAY - timedelta(weeks=1)
    runs = list(check_runs("check-1", start, WEDNESDAY, interval_seconds=60, seed=6))
    outcomes = {(run.outcome, run.final, run.maintenance) for run in runs}
    assert ("up", True, False) in outcomes
    assert ("down", False, False) in outcomes, "an attempt a retry overruled"
    assert ("prober_error", True, False) in outcomes
    assert any(run.maintenance for run in runs)
    # An overruled attempt is followed, at its own moment, by the confirmed retry.
    for overruled in (run for run in runs if not run.final):
        retries = [r for r in runs if r.at == overruled.at and r.attempt == overruled.attempt + 1]
        assert len(retries) == 1 and retries[0].final
    confirmed = [run for run in runs if run.final]
    assert len(confirmed) == 7 * 24 * 60 + 1


def test_the_same_seed_writes_the_same_check_readings() -> None:
    start = WEDNESDAY - timedelta(days=1)
    first = list(check_runs("check-1", start, WEDNESDAY, interval_seconds=300, seed=3))
    assert first == list(check_runs("check-1", start, WEDNESDAY, interval_seconds=300, seed=3))
    assert len([run for run in first if run.final]) == 24 * 12 + 1
