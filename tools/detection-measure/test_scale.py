"""The lag, read and size arithmetic behind `scale.py`, without a store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from scale import (
    Lag,
    Sweep,
    by_minute,
    by_project,
    cgroup_memory,
    classify,
    detection_statements,
    ingest_key,
    judged_minutes,
    lags,
    parse_size,
    settled_from,
    summarise,
)

M = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def test_a_minute_handed_out_on_time_settles_at_m_plus_three() -> None:
    sweeps = [Sweep(M + timedelta(minutes=2, seconds=59), M - MINUTE), Sweep(M + 3 * MINUTE, M)]
    assert settled_from(M, sweeps) == M + 3 * MINUTE


def test_a_minute_the_pipeline_held_settles_after_the_last_sweep_that_left_it_out() -> None:
    late = M + timedelta(minutes=4, seconds=1)
    sweeps = [Sweep(M + 3 * MINUTE, M - MINUTE), Sweep(late, M - MINUTE), Sweep(late + MINUTE, M)]
    assert settled_from(M, sweeps) == late


def test_a_run_that_moves_a_service_on_two_minutes_judged_both() -> None:
    seen = M + 4 * MINUTE
    assert judged_minutes([(seen, M - MINUTE, M + MINUTE)]) == {M: seen, M + MINUTE: seen}


def test_a_first_judging_judges_only_its_minute() -> None:
    assert judged_minutes([(M + 4 * MINUTE, None, M)]) == {M: M + 4 * MINUTE}


def test_the_first_time_a_minute_was_seen_judged_is_kept() -> None:
    first, again = M + 4 * MINUTE, M + 5 * MINUTE
    assert judged_minutes([(first, M - MINUTE, M), (again, M, M + MINUTE)])[M] == first


def test_a_minute_never_judged_counts_against_the_target() -> None:
    sweeps = [Sweep(M + 3 * MINUTE, M + MINUTE)]
    judged = {("p", "a"): {M: M + timedelta(minutes=3, seconds=40)}}
    found = lags(judged, sweeps, [M, M + MINUTE])
    assert found[0].seconds == 40.0
    assert found[1].judged_at is None
    summary = summarise([lag.seconds for lag in found])
    assert (summary.never_judged, summary.over_target, summary.max_seconds) == (1, 1, 40.0)


def test_a_projects_lag_is_its_slowest_service() -> None:
    def lag(service: str, seconds: float | None) -> Lag:
        return Lag("p", service, M, None, seconds, seconds)

    assert by_project([lag("a", 10.0), lag("b", 95.0), lag("c", 20.0)]) == [95.0]
    assert by_project([lag("a", 10.0), lag("b", None), lag("c", 20.0)]) == [None]


def test_the_p95_is_the_nearest_rank() -> None:
    summary = summarise([float(n) for n in range(1, 101)])
    assert (summary.p95_seconds, summary.max_seconds, summary.over_target) == (95.0, 100.0, 10)


def test_detections_statements_are_recognised_in_the_query_log() -> None:
    statements = detection_statements()
    window = statements["WINDOW_MINUTES_SQL"]
    assert classify(window.replace(" ", "\n  ") + " FORMAT Native", statements) == (
        "WINDOW_MINUTES_SQL"
    )
    assert classify("INSERT INTO detection_readings (a) FORMAT Native", statements) == (
        "READINGS_INSERT"
    )
    assert classify("SELECT 1", statements) is None


def test_docker_sizes_are_read_in_bytes() -> None:
    assert parse_size("512MiB ") == 512 * 2**20
    assert parse_size(" 1.5GiB") == int(1.5 * 2**30)
    assert parse_size("--") == 0


def test_each_project_has_a_key_of_its_own_prefix() -> None:
    keys = [ingest_key(number) for number in (1, 200)]
    assert all(len(key) == len("nvk_live_") + 22 for key in keys)
    assert keys[0][:16] != keys[1][:16]


def test_a_v1_memory_cgroup_is_read(tmp_path: Path) -> None:
    for name, value in {
        "memory.max_usage_in_bytes": "700",
        "memory.limit_in_bytes": "768",
        "memory.failcnt": "3",
        "memory.oom_control": "oom_kill_disable 0\nunder_oom 0\noom_kill 2\n",
    }.items():
        (tmp_path / name).write_text(value)
    assert cgroup_memory(tmp_path) == {
        "peak_bytes": 700,
        "limit_bytes": 768,
        "failcnt": 3,
        "oom_kills": 2,
    }


def test_a_v2_memory_cgroup_is_read(tmp_path: Path) -> None:
    for name, value in {
        "memory.peak": "700\n",
        "memory.max": "max\n",
        "memory.events": "low 0\nhigh 0\nmax 4\noom 1\noom_kill 1\n",
    }.items():
        (tmp_path / name).write_text(value)
    assert cgroup_memory(tmp_path) == {"peak_bytes": 700, "limit_bytes": 0, "oom_kills": 1}


# As ClickHouse 26.3 logs the window read: values bound on the server, conditions bracketed.
LOGGED_WINDOW = (
    "SELECT service_name, toUnixTimestamp(minute) AS minute_epoch, sum(request_count) AS "
    "requests, sum(error_count) AS errors, sumForEachMerge(latency_buckets) AS buckets FROM "
    "service_stats_1m WHERE (tenant_id = '01a1932b-d7bd-7c36-a436-c8bc0540b6f9') AND "
    "(project_id = '01a1932b-d811-781c-97b0-1a5da8b2ec36') AND (minute >= _CAST(1793498940, "
    "'DateTime')) AND (minute < _CAST(1793499240, 'DateTime')) GROUP BY service_name, minute "
    "FORMAT Native"
)
LOGGED_SERVICES = (
    "SELECT service_name, toUnixTimestamp(minute) AS minute_epoch, sum(request_count) AS "
    "requests, sum(error_count) AS errors FROM service_stats_1m WHERE (tenant_id = 'a') AND "
    "(project_id = 'b') AND (service_name IN _CAST(['cart', 'ad'], 'Array(String)')) "
    "AND (minute >= _CAST(1, "
    "'DateTime')) AND (minute < _CAST(2, 'DateTime')) GROUP BY service_name, minute FORMAT Native"
)


def test_a_query_as_the_server_logs_it_is_recognised() -> None:
    statements = detection_statements()
    assert classify(LOGGED_WINDOW, statements) == "WINDOW_MINUTES_SQL"
    assert classify(LOGGED_SERVICES, statements) == "SERVICE_COUNTS_SQL"
    assert classify("INSERT INTO `detection_readings` (`tenant_id`) FORMAT Native", statements) == (
        "READINGS_INSERT"
    )
    assert classify("DESCRIBE TABLE `detection_readings` FORMAT Native", statements) == (
        "READINGS_DESCRIBE"
    )


def test_lags_are_grouped_by_minute_in_order() -> None:
    later = Lag("p", "a", M + MINUTE, None, 50.0, 60.0)
    found = by_minute(
        [later, Lag("p", "a", M, None, 10.0, 20.0), Lag("p", "b", M, None, None, None)]
    )
    assert list(found) == [M, M + MINUTE]
    assert found[M] == [10.0, None]
