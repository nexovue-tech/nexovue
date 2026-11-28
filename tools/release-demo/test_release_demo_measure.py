"""The gate's arithmetic in `release_measure.py`, without a store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import release_measure
from release_measure import (
    DISK_RATIO_MAX,
    PEAK_MEMORY_INCREASE_MAX,
    Check,
    Rollout,
    Spread,
    check,
    exact,
    expected_side_by_side,
    gate,
    ratio_checks,
    rollouts,
)

M = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def test_a_spread_gives_least_median_and_most() -> None:
    assert Spread.of([3.0, 1.0, 2.0, 10.0]) == Spread(1.0, 2.5, 10.0)
    with pytest.raises(ValueError):
        Spread.of([])


def test_a_rollout_is_two_release_versions_serving_the_same_minutes() -> None:
    rows = [
        ("checkout", M, "v1", 50),
        ("checkout", M + MINUTE, "v1", 30),
        ("checkout", M + MINUTE, "v2", 10),
        ("checkout", M + 2 * MINUTE, "v1", 15),
        ("checkout", M + 2 * MINUTE, "v2", 25),
        # Not releases, so they neither make nor break a rollout.
        ("checkout", M + 2 * MINUTE, "", 4),
        ("checkout", M + 2 * MINUTE, "__other__", 4),
        ("checkout", M + 3 * MINUTE, "v2", 60),
    ]
    assert rollouts(rows) == [Rollout("checkout", ("v1", "v2"), 2, (45, 35))]


def test_side_by_side_needs_the_failure_floor_on_both_sides() -> None:
    assert Rollout("a", ("v1", "v2"), 3, (20, 20)).side_by_side
    assert not Rollout("a", ("v1", "v2"), 3, (500, 19)).side_by_side


def test_churn_minutes_with_many_versions_are_not_rollouts() -> None:
    rows = [("a", M, f"build-{index}", 30) for index in range(3)]
    assert rollouts(rows) == []


def test_expected_side_by_side_follows_requests_times_minutes() -> None:
    # About R * L / 2 a side: 10 a minute over 5 minutes is 25 a side; over 3 it is 15.
    assert expected_side_by_side(10, 5)
    assert not expected_side_by_side(10, 3)
    assert not expected_side_by_side(1, 20)


def test_a_check_without_a_figure_is_not_measured_rather_than_passed() -> None:
    assert check("x", None, 1.0).passed is None
    assert check("x", 1.0, 1.0).passed is True
    assert check("x", 1.01, 1.0).passed is False


def _run(
    label: str,
    *,
    peak: int,
    versions: int = 0,
    services: int = 100,
    disk: int = 0,
    mode: str = "many-services",
) -> dict[str, Any]:
    return {
        "label": label,
        "settings": {"mode": mode},
        "requests_sent": 100,
        "requests_drained": 100,
        "memory_rss_bytes": [peak // 2, peak],
        "rows": {
            "service_stats_1m": {"merged": services},
            "service_versions_1m": {"merged": versions},
        },
        "steady": {"service_minutes_with_one_version": 90 if versions else 0},
        "disk": {
            "service_stats_1m": {"bytes_on_disk": 1000, "requests": 100},
            "service_versions_1m": {"bytes_on_disk": disk, "requests": 100 if disk else 0},
        },
        "mismatches": [],
        "unsummed": [],
    }


def test_the_demo_traffic_is_judged_by_the_four_ratio_rows() -> None:
    runs = [_run("baseline", peak=100), _run("versions", peak=120, versions=108, disk=1500)]
    assert gate(runs, "many-services") == ratio_checks(runs, "many-services")


def test_the_ratio_rows_compare_peaks_rows_and_bytes_per_request() -> None:
    runs = [
        _run("baseline", peak=100),
        _run("baseline", peak=110),
        _run("versions", peak=120, versions=108, disk=1500),
    ]
    found = {result.name: result for result in ratio_checks(runs, "many-services")}

    memory = found["Worker peak memory, increase over the baseline"]
    assert memory.measured == pytest.approx(10 / 110) and memory.passed
    assert found["Rows with one rollout an hour on every service"].measured == pytest.approx(1.08)
    disk = found["Bytes on disk per request, versions over services"]
    assert disk.measured == pytest.approx(1.5) and disk.threshold == DISK_RATIO_MAX and disk.passed


def test_the_ratio_rows_fail_a_peak_past_its_bound_and_read_nothing_they_lack() -> None:
    over = 1 + PEAK_MEMORY_INCREASE_MAX + 0.01
    runs = [
        _run("baseline", peak=10_000),
        _run("versions", peak=round(10_000 * over), versions=100, disk=1000),
    ]
    found = {result.name: result for result in ratio_checks(runs, "many-services")}
    assert found["Worker peak memory, increase over the baseline"].passed is False

    alone = {
        result.name: result
        for result in ratio_checks([_run("versions", peak=1, versions=1)], "many-services")
    }
    assert alone["Worker peak memory, increase over the baseline"].passed is None


def test_totals_are_exact_only_when_no_versioned_run_disagrees() -> None:
    clean = _run("versions", peak=1, versions=1)
    assert exact([clean, _run("baseline", peak=1)])
    assert not exact([clean, _run("versions", peak=1) | {"unsummed": ["checkout"]}])


MIB = 2**20
LIMIT = 1088 * MIB


def _mismatch(version: str, sent: tuple[int, int], stored: tuple[int, int]) -> dict[str, Any]:
    return {"service": "a", "version": version, "sent": list(sent), "stored": list(stored)}


def _churn(*, peak: int = 150 * MIB, other: tuple[int, int] = (11, 1)) -> dict[str, Any]:
    """A churn run where the caps folded part of `v1` and all of `v2` into `__other__`."""
    run = _run("versions", peak=peak, versions=2100, disk=2100, mode="churn")
    run["mismatches"] = [
        _mismatch("__other__", (0, 0), other),
        _mismatch("v1", (10, 1), (4, 0)),
        _mismatch("v2", (5, 0), (0, 0)),
    ]
    return run


def test_a_churn_run_whose_versions_were_folded_is_exact_and_conserved() -> None:
    run = _churn()
    assert exact([run])
    assert release_measure.folded_services(run) == ["a"]
    assert release_measure.folded_services(_run("versions", peak=1, versions=1)) == []


def _overcounted() -> dict[str, Any]:
    """Totals conserved, but one version holds more than was sent: not folding, a wrong count."""
    run = _churn()
    run["mismatches"] += [_mismatch("v3", (5, 0), (7, 0)), _mismatch("v4", (5, 0), (3, 0))]
    return run


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(_churn(other=(10, 1)), id="a request lost"),
        pytest.param(_churn(other=(11, 0)), id="an error lost"),
        pytest.param(_churn() | {"requests_drained": 99}, id="not all drained"),
        pytest.param(_churn() | {"unsummed": ["a"]}, id="versions not summing to the service"),
        pytest.param(_overcounted(), id="a version counted above what was sent"),
    ],
)
def test_a_run_whose_totals_differ_from_what_was_sent_is_not_exact(run: dict[str, Any]) -> None:
    assert not exact([run])


def test_churn_is_judged_against_gomemlimit_and_exact_totals_not_the_demo_ratios() -> None:
    runs = [
        _run("baseline", peak=100 * MIB, mode="churn"),
        _churn(peak=130 * MIB),  # 30% over the baseline, 21 rows a service-minute: information
    ]
    found = {row.name: row for row in gate(runs, "churn", memory_limit_bytes=LIMIT)}

    assert set(found) == {"Worker peak memory, under GOMEMLIMIT", "Totals exact"}
    memory = found["Worker peak memory, under GOMEMLIMIT"]
    assert isinstance(memory, Check)
    assert memory.measured == pytest.approx(130) and memory.threshold == 1088
    assert all(row.passed for row in found.values())

    over = gate([_churn(peak=LIMIT + MIB)], "churn", memory_limit_bytes=LIMIT)
    assert {row.name: row.passed for row in over}["Worker peak memory, under GOMEMLIMIT"] is False
    lost = gate([_churn(other=(10, 1))], "churn", memory_limit_bytes=LIMIT)
    assert {row.name: row.passed for row in lost}["Totals exact"] is False


def test_churn_without_a_versioned_run_is_not_measured_rather_than_passed() -> None:
    rows = gate([_run("baseline", peak=MIB, mode="churn")], "churn", memory_limit_bytes=LIMIT)
    assert [row.passed for row in rows] == [None, None]


def test_the_memory_limit_is_the_workers_gomemlimit_as_compose_ships_it() -> None:
    assert release_measure.WORKER_GOMEMLIMIT_BYTES == 1088 * MIB
    assert release_measure.go_bytes("${WORKER_GOMEMLIMIT:-1088MiB}") == 1088 * MIB
    assert release_measure.go_bytes("2GiB") == 2 * 1024 * MIB
    with pytest.raises(ValueError):
        release_measure.go_bytes("${WORKER_GOMEMLIMIT}")
