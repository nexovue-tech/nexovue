"""The target mix and the arithmetic behind `slo_scale.py`, without a store (F19, plan T6.2)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from nexovue_platform import slo
from slo_scale import (
    CHECKS_PER_BIG_PROJECT,
    CHECKS_PER_PROJECT,
    Spec,
    percentile,
    plan_targets,
    summarise_ms,
)

SERVICES = tuple(f"service-{index}" for index in range(10))


def _key(spec: Spec, checks: list[str]) -> str:
    return slo.definition_key(
        spec.kind,
        objective=Decimal(spec.objective),
        window_days=spec.window_days,
        service_name=spec.service,
        endpoint_name=spec.endpoint,
        check_id=None if spec.check is None else checks[spec.check],
        boundary_index=spec.boundary_index,
    )


@pytest.mark.parametrize("big", [False, True])
def test_a_project_mix_holds_all_four_kinds_with_no_two_alike(big: bool) -> None:
    count = CHECKS_PER_BIG_PROJECT if big else CHECKS_PER_PROJECT
    checks = [f"check-{index}" for index in range(count)]
    specs = plan_targets(SERVICES, count, big=big)
    assert {spec.kind for spec in specs} == set(slo.KINDS)
    assert len({spec.name for spec in specs}) == len(specs)
    assert len({_key(spec, checks) for spec in specs}) == len(specs)
    assert all(len(spec.name) <= 120 for spec in specs)


def test_an_ordinary_project_has_one_target_per_service_and_the_big_one_has_the_limit() -> None:
    assert len(plan_targets(SERVICES, CHECKS_PER_PROJECT, big=False)) == len(SERVICES)
    assert len(plan_targets(SERVICES, CHECKS_PER_BIG_PROJECT, big=True)) == slo.TARGETS_PER_PROJECT


def test_every_target_is_one_the_api_would_accept() -> None:
    for spec in plan_targets(SERVICES, CHECKS_PER_BIG_PROJECT, big=True):
        assert slo.objective_refusal(Decimal(spec.objective)) is None
        assert spec.window_days in (7, 28)
        assert (spec.boundary_index is not None) == (spec.kind == slo.SERVICE_LATENCY)
        assert (spec.endpoint is not None) == (spec.kind == slo.ENDPOINT_AVAILABILITY)
        assert (spec.check is not None) == (spec.kind == slo.CHECK_AVAILABILITY)


def test_percentiles_and_summaries_are_read_off_the_sorted_values() -> None:
    values = [float(n) for n in range(1, 101)]
    assert percentile(values, 0.5) == 50.0
    assert percentile(values, 0.95) == 95.0
    summary = summarise_ms([0.5, 1.5, 2.0])
    assert (summary.count, summary.max_ms) == (3, 2000.0)
    assert summarise_ms([]).count == 0


def test_logged_queries_are_grouped_by_statement_with_time_and_memory() -> None:
    from slo_scale import slo_statements, statement_stats

    statements = slo_statements()
    assert "SLO_SERVICE_DAY_SQL" in statements and "SLO_ENDPOINT_MINUTES_SQL" in statements
    day = (
        "SELECT service_name, toUnixTimestamp(minute) AS minute_epoch, sum(request_count) AS "
        "requests, sum(error_count) AS errors FROM service_stats_1m WHERE tenant_id = 't' AND "
        "project_id = 'p' AND service_name IN ['a'] AND minute >= '2026-11-01 00:00:00' AND "
        "minute < '2026-11-02 00:00:00' GROUP BY service_name, minute"
    )
    stats = statement_stats(
        [(day, 10, 2 * 2**20, 1000), (day, 30, 4 * 2**20, 3000), ("SELECT 1", 1, 1, 1)],
        statements,
    )
    [only] = stats
    assert only.name == "SLO_SERVICE_DAY_SQL"
    assert (only.count, only.mean_ms, only.max_ms, only.mean_rows) == (2, 20.0, 30.0, 2000.0)
    assert (only.mean_mib, only.max_mib) == (3.0, 4.0)


def test_writes_between_two_readings_name_only_the_tables_that_moved() -> None:
    from slo_scale import writes_between

    before = {"slo_status": (10, 5), "incidents": (1, 1)}
    after = {"slo_status": (12, 9), "incidents": (1, 1), "notifications": (3, 0)}
    assert writes_between(before, after) == {"slo_status": (2, 4), "notifications": (3, 0)}


def test_a_job_summary_reports_the_largest_project_beside_the_average() -> None:
    from slo_scale import Job, job_summary

    jobs = [Job("a", 0.1, 4), Job("b", 0.3, 6), Job("big", 1.0, 20)]
    summary = job_summary(jobs, "big")
    assert summary["jobs"] == 3
    assert summary["largest_project_ms"] == 1000.0
    assert summary["mean_postgres_statements"] == 10.0
    assert summary["total_s"] == 1.4
