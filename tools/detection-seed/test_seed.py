"""The seeded history's shape, its reproducibility and its buckets, without a store.

F19 plan T2.6: endpoint rows that add up to their service's, with a `/health`-style
probe every busy minute, and check readings holding every kind of run the uptime rule
tells apart: confirmed, overruled, in maintenance, and `prober_error`.
"""

from __future__ import annotations

import itertools
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from seed import (
    DEPLOYS_ON_BUSIEST,
    HEALTH_ENDPOINT,
    LAST_RELEASE_BEFORE_END,
    LATENCY_BOUNDS,
    LATENCY_BUCKET_COUNT,
    ROLLOUT,
    PlannedRelease,
    Row,
    VersionRow,
    check_runs,
    endpoint_rows,
    generate,
    latency_bucket,
    live_peaks,
    main,
    release_plan,
    release_records,
    require_local,
    seen_minutes,
    spread,
    version_rows,
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


# ------------------------------------------------------------- F28, plan T3.0

SERVICES = ["checkout", "frontend", "search", "mailer"]
FOUR_WEEKS_END = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
FOUR_WEEKS_START = FOUR_WEEKS_END - timedelta(weeks=4)


def _plan() -> list[PlannedRelease]:
    return release_plan(SERVICES, FOUR_WEEKS_START, FOUR_WEEKS_END, retention_days=30)


def test_the_first_service_deploys_60_times_its_last_just_before_the_history_ends() -> None:
    """AC38's 60 deploys, and one release before a finding `fault.py` then opens."""
    plan = [r for r in _plan() if r.service_name == "checkout" and r.started_at >= FOUR_WEEKS_START]
    assert len(plan) == DEPLOYS_ON_BUSIEST
    assert all(release.reported for release in plan)
    assert max(r.started_at for r in plan) == FOUR_WEEKS_END - LAST_RELEASE_BEFORE_END


def test_one_release_is_older_than_the_project_s_limit_with_no_rows_to_count() -> None:
    """Its rollup rows are past the TTL: Postgres alone remembers it."""
    (expired,) = [r for r in _plan() if r.started_at < FOUR_WEEKS_START]
    assert expired.started_at < FOUR_WEEKS_END - timedelta(days=30)
    assert expired.reported


def test_coverage_takes_each_value_across_the_services() -> None:
    """A service sending no version and reporting nothing reads coverage none; one sending
    versions without reports reads versions only."""
    plan = _plan()
    assert not [r for r in plan if r.service_name == "mailer"]
    search = [r for r in plan if r.service_name == "search"]
    assert search and not any(r.reported for r in search)
    assert all(r.reported for r in plan if r.service_name == "frontend")


def test_each_minute_s_versions_add_up_to_the_service_s_row() -> None:
    """T3.0: the version rows sum to `service_stats_1m`, minute by minute, in requests,
    failures and every latency bucket, through each rollout."""
    plan = _plan()
    rows = list(generate(SERVICES, FOUR_WEEKS_START, FOUR_WEEKS_END, UTC, 300.0, 6))
    versions = list(version_rows(rows, plan))
    by_minute: dict[tuple[str, datetime], list[VersionRow]] = {}
    for version in versions:
        by_minute.setdefault((version.service_name, version.minute), []).append(version)
    assert len(by_minute) == len(rows)
    for row in rows:
        mine = by_minute[(row.service_name, row.minute)]
        assert sum(v.request_count for v in mine) == row.request_count
        assert sum(v.error_count for v in mine) == row.error_count
        assert [
            sum(column) for column in zip(*(v.latency_buckets for v in mine), strict=True)
        ] == list(row.latency_buckets)
        assert all(0 <= v.error_count <= v.request_count for v in mine)
    assert {v.version for v in versions if v.service_name == "mailer"} == {""}
    # A rollout splits minutes between two versions.
    assert any(len(group) == 2 for group in by_minute.values())


def test_the_records_follow_the_rows_and_the_periods_chain() -> None:
    """Each release's first and last counted minute, a period that ends where the next
    one's rollout finishes, the previous version, and the first one already running only
    where nothing came before."""
    plan = _plan()
    rows = list(generate(SERVICES, FOUR_WEEKS_START, FOUR_WEEKS_END, UTC, 300.0, 6))
    seen = seen_minutes(version_rows(rows, plan))
    records = release_records(plan, seen)
    checkout = sorted(
        (r for r in records if r.service_name == "checkout"), key=lambda r: r.started_at
    )
    assert checkout[0].first_seen_at is None and checkout[0].ended_at is not None
    assert checkout[1].previous_version == checkout[0].version
    assert not checkout[1].already_running
    assert checkout[-1].ended_at is None
    planned = {r.version: r.started_at for r in plan if r.service_name == "checkout"}
    for earlier, later in itertools.pairwise(checkout):
        overlap = ROLLOUT if earlier.first_seen_at is not None else timedelta(0)
        assert earlier.ended_at == planned[later.version] + overlap
    (first_search, *_) = sorted(
        (r for r in records if r.service_name == "search"), key=lambda r: r.started_at
    )
    assert first_search.already_running and first_search.previous_version is None
    for record in records:
        if record.first_seen_at is not None:
            assert (record.first_seen_at, record.last_seen_at) == seen[
                (record.service_name, record.version)
            ]
