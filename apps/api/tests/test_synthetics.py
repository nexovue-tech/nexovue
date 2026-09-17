"""Synthetic check readings: tenant isolation, the phase pivot, and the units.

The fake store answers from the values the repository *bound*, never from the
statement text, so a query that lost its tenant predicate finds nothing and the
isolation tests below fail rather than quietly passing. Asserting on a store
that held only one tenant's rows would prove the fixture was lucky.

`test_synthetics_live.py` runs the same reads against the real ClickHouse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest

from nexovue_api.data.clickhouse.base import TENANT_BINDING, TenantScopedRepository
from nexovue_api.synthetics import queries, service
from nexovue_api.synthetics.schemas import PHASE_ORDER
from nexovue_api.window import RECEIVE_CLOCK_SLACK

TENANT_A: Final = "org-a"
TENANT_B: Final = "org-b"
PROJECT_A: Final = "project-a"
PROJECT_B: Final = "project-b"
CHECK_A: Final = "check-a"
CHECK_B: Final = "check-b"

_RUN_NANOS: Final = 1_788_038_991_898_000_000
_BUCKET_NANOS: Final = 1_788_038_700_000_000_000
_BUCKET_WIDTH_NANOS: Final = 300_000_000_000

# The phase split of one real probe, in seconds, as the prober published it.
_PHASE_SECONDS: Final = {
    "dns": 0.067645,
    "tcp": 0.086936,
    "tls": 0.251007,
    "ttfb": 0.115359,
    "transfer": 0.000134,
}
_DURATION_SECONDS: Final = 0.521081

_SUMMARY_COLUMNS: Final = [
    "check_id",
    "check_type",
    "runs",
    "successful_runs",
    "latest_up",
    "latest_status_code",
    "latest_outcome",
    "latest_run_nanos",
    "duration_points",
    "mean_seconds",
    "p95_seconds",
    "max_seconds",
    "tls_readings",
    "tls_days_remaining",
    "tls_issuer",
    "tls_server",
]
_PHASE_COLUMNS: Final = ["bucket_nanos", "phase", "mean_seconds", "samples"]
_TREND_COLUMNS: Final = [
    "check_id",
    "bucket_nanos",
    "runs",
    "successful_runs",
    "duration_points",
    "mean_seconds",
]
_RUN_COLUMNS: Final = [
    "observed_nanos",
    "run_id",
    "status_points",
    "up_value",
    "status_code",
    "duration_points",
    "duration_seconds",
    "phase_points",
    "phase_total_seconds",
    "outcome",
    "final_flag",
    "maintenance_flag",
    "attempt",
    "location",
]


def _statements() -> dict[str, str]:
    """Every statement the module can build, mapped to what answers it.

    Built rather than listed: the summary statement is assembled from its
    filters, so comparing against the unfiltered constant would stop matching
    the moment a screen asked for one.
    """
    served = {
        queries.CHECK_TRENDS_SQL: "trends",
        queries.PHASE_BREAKDOWN_SQL: "breakdown",
        queries.CHECK_RUNS_SQL: "runs",
        queries.CHECK_RUN_COUNT_SQL: "run_count",
        queries.CHECK_RUN_EVIDENCE_SQL: "evidence",
    }
    for check_type in (False, True):
        for down_only in (False, True):
            served[queries.check_summaries_sql(check_type=check_type, down_only=down_only)] = (
                "summaries"
            )
    return served


_STATEMENTS: Final = _statements()


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class FakeGaugeStore:
    """Serves synthetic readings, honouring the tenant and check the query bound.

    Each tenant owns exactly one project and one check. A read that arrives
    without the right tenant, project or check id gets nothing back — which is
    what makes a dropped predicate visible as an empty response.
    """

    def __init__(
        self,
        owners: dict[str, tuple[str, str]],
        trend_rows: list[tuple[Any, ...]] | None = None,
    ) -> None:
        # tenant -> (the project it owns, the check inside it)
        self._owners = owners
        self._trend_rows = trend_rows
        self.last_parameters: dict[str, Any] = {}

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.last_parameters = bound
        owned = self._owners.get(bound["tenant_id"])
        if owned is None or owned[0] != bound.get("project_id"):
            return FakeQueryResult([], [])
        check_id = owned[1]

        # Dispatch on which statement was run, matched against every statement
        # the module can build rather than by sniffing its text. A substring did
        # this until a shared `GROUP BY check_id, metric_name, series_hash,
        # timestamp` appeared inside three statements and all of them started
        # answering as the summary.
        served = _STATEMENTS.get(sql)
        if served is None:
            raise AssertionError(f"the fake store was asked a statement it does not serve:\n{sql}")

        if served == "summaries":
            return FakeQueryResult(_SUMMARY_COLUMNS, [_summary_row(check_id)])
        if served == "trends":
            return FakeQueryResult(_TREND_COLUMNS, self._trend_rows or _trend_rows(check_id))

        if bound.get("check_id") != check_id:
            return FakeQueryResult([], [])
        if served == "breakdown":
            return FakeQueryResult(_PHASE_COLUMNS, _phase_rows())
        return FakeQueryResult(_RUN_COLUMNS, _run_rows())


def _trend_rows(check_id: str) -> list[tuple[Any, ...]]:
    """Two buckets: one healthy, one where nothing succeeded and so has no duration."""
    return [
        (check_id, _BUCKET_NANOS, 5, 5, 5, _DURATION_SECONDS),
        (check_id, _BUCKET_NANOS + _BUCKET_WIDTH_NANOS, 5, 0, 0, None),
    ]


def _untimed_trend_row(check_id: str) -> tuple[Any, ...]:
    """A bucket a heartbeat produces: judged up, never probed, so never timed.

    `avg` over no rows is NaN in ClickHouse, which is what the count beside it
    exists to catch — a success is not the same fact as a measurement.
    """
    return (check_id, _BUCKET_NANOS, 1, 1, 0, float("nan"))


def _summary_row(check_id: str) -> tuple[Any, ...]:
    return (
        check_id,
        "api",
        200,  # runs
        190,  # successful_runs
        1.0,  # latest_up
        200,  # latest_status_code
        "up",  # latest_outcome
        _RUN_NANOS,
        200,  # duration_points
        _DURATION_SECONDS,
        1.1562705,
        3.77763,
        200,  # tls_readings
        58.0,
        "C = US, O = SSL Corporation, CN = Cloudflare TLS Issuing ECC CA 3",
        "example.org",
    )


def _phase_rows() -> list[tuple[Any, ...]]:
    """Two buckets. The second is missing `tls`, which the pivot must fill."""
    first = [(_BUCKET_NANOS, phase, seconds, 5) for phase, seconds in _PHASE_SECONDS.items()]
    second = [
        (_BUCKET_NANOS + _BUCKET_WIDTH_NANOS, phase, seconds, 5)
        for phase, seconds in _PHASE_SECONDS.items()
        if phase != "tls"
    ]
    return first + second


def _run_rows() -> list[tuple[Any, ...]]:
    return [
        (
            _RUN_NANOS,
            "r-3",
            1,
            1.0,
            200,
            1,  # duration_points
            _DURATION_SECONDS,
            len(_PHASE_SECONDS),  # phase_points
            sum(_PHASE_SECONDS.values()),
            "up",
            "true",
            "false",
            2,
            "mumbai",
        ),
        (
            _RUN_NANOS - 60_000_000_000,
            "r-2",
            1,
            0.0,
            503,
            1,  # duration_points
            0.2,
            1,  # phase_points
            0.2,
            "down",
            "false",
            "false",
            1,
            "hyderabad",
        ),
        # A group whose status point has not landed: half a run, not a run.
        (_RUN_NANOS - 120_000_000_000, "r-1", 0, 0.0, 0, 1, 0.3, 1, 0.3, "", "", "", 0, ""),
    ]


def _store() -> FakeGaugeStore:
    return FakeGaugeStore({TENANT_A: (PROJECT_A, CHECK_A), TENANT_B: (PROJECT_B, CHECK_B)})


def _repository(tenant: str, store: FakeGaugeStore) -> TenantScopedRepository:
    return TenantScopedRepository(store, tenant_id=tenant)  # type: ignore[arg-type]


def _window() -> tuple[datetime, datetime]:
    end = datetime(2026, 8, 29, 22, 0, tzinfo=UTC)
    return end - timedelta(hours=6), end


def _every_statement() -> list[str]:
    return [
        value
        for name, value in vars(queries).items()
        if name.endswith("_SQL") and isinstance(value, str)
    ]


# ------------------------------------------------------------------- tenancy


def test_every_synthetic_statement_carries_the_tenant_binding() -> None:
    """The repository refuses one that does not; this names which one it is."""
    statements = _every_statement()

    assert statements, "no statements found — this test would prove nothing"
    for sql in statements:
        assert TENANT_BINDING in sql


async def test_two_organizations_cannot_read_each_others_check_results() -> None:
    store = _store()
    start, end = _window()

    mine = await service.list_check_results(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end
    )
    theirs = await service.list_check_results(
        _repository(TENANT_B, store), project_id=PROJECT_B, from_time=start, to_time=end
    )

    assert [check.check_id for check in mine.checks] == [CHECK_A]
    assert [check.check_id for check in theirs.checks] == [CHECK_B]


async def test_another_tenants_project_returns_nothing_rather_than_their_rows() -> None:
    """`project_id` is a caller value; `require_project` gates it at the router.

    Even past that gate the read is bound to the session's tenant, so pointing
    it at another organization's project finds nothing.
    """
    store = _store()
    start, end = _window()

    stolen = await service.list_check_results(
        _repository(TENANT_A, store), project_id=PROJECT_B, from_time=start, to_time=end
    )

    assert stolen.checks == []


async def test_another_tenants_check_id_returns_no_phase_breakdown() -> None:
    store = _store()
    start, end = _window()

    stolen = await service.phase_breakdown(
        _repository(TENANT_A, store),
        project_id=PROJECT_A,
        check_id=CHECK_B,
        from_time=start,
        to_time=end,
        bucket_seconds=300,
    )

    assert stolen.buckets == []
    assert stolen.phases == []


async def test_no_read_binds_a_tenant_it_was_not_given() -> None:
    """Guards the tests above: presence alone would pass if tenant were ignored."""
    store = _store()
    start, end = _window()

    await service.list_check_results(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end
    )

    assert store.last_parameters["tenant_id"] == TENANT_A
    assert TENANT_B not in store.last_parameters.values()


async def test_a_read_bounds_both_clocks() -> None:
    """`metrics_gauge` is partitioned on `received_at`, so only that prunes."""
    store = _store()
    start, end = _window()

    await service.list_check_results(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end
    )

    assert store.last_parameters["from_time"] == start
    assert store.last_parameters["to_time"] == end
    assert store.last_parameters["received_from"] == start - RECEIVE_CLOCK_SLACK
    assert store.last_parameters["received_to"] == end + RECEIVE_CLOCK_SLACK


# ------------------------------------------------- the readings on the wire


async def test_uptime_is_the_share_of_probes_that_reported_up() -> None:
    store = _store()
    start, end = _window()

    results = await service.list_check_results(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end
    )

    check = results.checks[0]
    assert check.runs == 200
    assert check.successful_runs == 190
    assert check.uptime_percent == pytest.approx(95.0)
    assert check.up is True


async def test_seconds_in_the_store_are_milliseconds_on_the_wire() -> None:
    """The store keeps OTLP seconds; every duration the API names is in ms."""
    store = _store()
    start, end = _window()

    results = await service.list_check_results(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end
    )

    assert results.checks[0].mean_duration_ms == pytest.approx(521.081)


async def test_a_check_that_reported_nothing_is_absent_rather_than_zeroed() -> None:
    """An empty window is an empty list, not a 404 and not an invented row."""
    store = FakeGaugeStore({})
    start, end = _window()

    results = await service.list_check_results(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end
    )

    assert results.checks == []


def test_a_summary_without_a_certificate_reading_reports_no_certificate() -> None:
    """`argMaxIf` over no row returns zero; a zero-day certificate is a lie."""
    row = dict(zip(_SUMMARY_COLUMNS, _summary_row(CHECK_A), strict=True))
    row["tls_readings"] = 0

    assert service._to_certificate(row) is None


def test_a_check_that_timed_nothing_reports_no_duration_rather_than_zero() -> None:
    """A heartbeat is never probed, so it has no response time at all.

    The store's answer over no rows is not one value: `avg` and `quantile` return
    NaN while `max` returns zero. Neither is a reading — a zero p95 reads as a
    check answering instantly, the one reading a heartbeat can never earn, and a
    NaN would reach the wire as a `null` decided by a serialiser rather than here.
    """
    row = dict(zip(_SUMMARY_COLUMNS, _summary_row(CHECK_A), strict=True))
    row["check_type"] = "heartbeat"
    row["duration_points"] = 0
    row["mean_seconds"] = float("nan")
    row["p95_seconds"] = float("nan")
    row["max_seconds"] = 0.0

    summary = service._to_summary(row)

    assert summary.mean_duration_ms is None
    assert summary.p95_duration_ms is None
    assert summary.max_duration_ms is None
    # The run still counted: silence is a reading, it just has no duration.
    assert summary.runs == 200


async def test_a_trend_bucket_that_succeeded_without_being_timed_has_no_duration() -> None:
    """A heartbeat's bucket: a success is not a measurement.

    `successful_runs` was the proxy for "something was timed" until heartbeats
    stopped publishing a duration. The count of duration points is the fact, and
    without it this bucket would carry the store's NaN into the response.
    """
    store = FakeGaugeStore(
        {TENANT_A: (PROJECT_A, CHECK_A)}, trend_rows=[_untimed_trend_row(CHECK_A)]
    )
    start, end = _window()

    trends = await service.check_trends(
        _repository(TENANT_A, store), project_id=PROJECT_A, from_time=start, to_time=end
    )

    (point,) = trends.checks[0].points
    assert point.successful_runs == 1
    assert point.mean_duration_ms is None


def test_a_run_that_never_sent_its_request_reports_no_duration() -> None:
    """`anyIf` over no duration point returns zero; the request was never sent."""
    row = dict(zip(_RUN_COLUMNS, _run_rows()[0], strict=True))
    row["duration_points"] = 0
    row["duration_seconds"] = 0.0
    row["phase_points"] = 0
    row["phase_total_seconds"] = 0.0

    run = service._to_run(row)

    assert run.duration_ms is None
    assert run.phase_total_ms is None


def test_a_summary_with_no_probe_in_the_window_reports_up_as_unknown() -> None:
    row = dict(zip(_SUMMARY_COLUMNS, _summary_row(CHECK_A), strict=True))
    row["runs"] = 0
    row["latest_up"] = 0.0

    summary = service._to_summary(row)

    assert summary.up is None
    assert summary.latest_run_at is None
    assert summary.uptime_percent == 0.0


# -------------------------------------------------------- the phase pivot


async def _breakdown() -> Any:
    start, end = _window()
    return await service.phase_breakdown(
        _repository(TENANT_A, _store()),
        project_id=PROJECT_A,
        check_id=CHECK_A,
        from_time=start,
        to_time=end,
        bucket_seconds=300,
    )


async def test_phases_are_named_in_the_order_a_request_performs_them() -> None:
    """A stacked chart plots them in this order or it plots a lie about causality."""
    breakdown = await _breakdown()

    assert breakdown.phases == list(PHASE_ORDER)


async def test_every_bucket_carries_every_phase_so_a_chart_never_guesses() -> None:
    """The second bucket has no `tls` row; it must still have a `tls` key."""
    breakdown = await _breakdown()

    assert len(breakdown.buckets) == 2
    for bucket in breakdown.buckets:
        assert set(bucket.durations_ms) == set(PHASE_ORDER)
    assert breakdown.buckets[1].durations_ms["tls"] == 0.0


async def test_a_buckets_total_is_the_sum_of_its_phases() -> None:
    breakdown = await _breakdown()

    first = breakdown.buckets[0]
    assert first.total_ms == pytest.approx(sum(first.durations_ms.values()))
    assert first.total_ms == pytest.approx(_DURATION_SECONDS * 1000, rel=1e-6)


async def test_buckets_are_ordered_oldest_first() -> None:
    breakdown = await _breakdown()

    timestamps = [bucket.timestamp for bucket in breakdown.buckets]
    assert timestamps == sorted(timestamps)


def test_a_phase_the_prober_adds_later_is_served_after_the_known_ones() -> None:
    """Dropping it would hide the very step that turned slow."""
    ordered = service._phases_in_request_order({"tls", "dns", "handshake_retry"})

    assert ordered == ["dns", "tls", "handshake_retry"]


# ------------------------------------------------------------ bucket width


def test_the_bucket_width_fits_the_window_when_the_caller_does_not_choose() -> None:
    start, end = datetime(2026, 8, 29, tzinfo=UTC), datetime(2026, 8, 30, tzinfo=UTC)

    assert service.resolve_bucket_seconds(start, end, None) == 86_400 // service.TARGET_BUCKETS


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(1, service.MIN_BUCKET_SECONDS), (999_999, service.MAX_BUCKET_SECONDS), (600, 600)],
)
def test_a_requested_bucket_width_is_clamped_to_what_the_data_supports(
    requested: int, expected: int
) -> None:
    """Below the minimum check interval a bucket holds at most one probe."""
    start, end = _window()

    assert service.resolve_bucket_seconds(start, end, requested) == expected


def test_a_short_window_never_produces_a_bucket_of_zero_seconds() -> None:
    """Zero would divide by zero in ClickHouse rather than return a sparse chart."""
    end = datetime(2026, 8, 29, 22, 0, tzinfo=UTC)

    assert service.resolve_bucket_seconds(end - timedelta(seconds=30), end, None) > 0


# ------------------------------------------------------------------- runs


async def test_runs_are_newest_first_and_carry_the_status_they_reported() -> None:
    start, end = _window()

    runs = await service.list_runs(
        _repository(TENANT_A, _store()),
        project_id=PROJECT_A,
        check_id=CHECK_A,
        from_time=start,
        to_time=end,
        limit=100,
    )

    assert [run.up for run in runs.runs] == [True, False]
    assert [run.status_code for run in runs.runs] == [200, 503]
    assert runs.runs[0].duration_ms == pytest.approx(521.081)
    # The overruled attempt is listed, and says it was not the confirmed one.
    assert [(run.final, run.attempt, run.location) for run in runs.runs] == [
        (True, 2, "mumbai"),
        (False, 1, "hyderabad"),
    ]


async def test_a_probe_whose_status_has_not_landed_is_not_reported_as_a_run() -> None:
    """Half a run reported as a run would move the uptime number."""
    start, end = _window()

    runs = await service.list_runs(
        _repository(TENANT_A, _store()),
        project_id=PROJECT_A,
        check_id=CHECK_A,
        from_time=start,
        to_time=end,
        limit=100,
    )

    assert len(runs.runs) == 2


# ---------------------------------------------- the contract with the writer


def test_the_metric_names_read_here_are_the_ones_the_prober_publishes() -> None:
    """The API and the scheduler are separate packages with one shared fact.

    Restating a name is how two services drift apart; this is the assertion
    that turns a rename into a failing test instead of an empty chart.
    """
    import nexovue_scheduler.publish as publish

    assert queries.METRIC_STATUS == publish.METRIC_STATUS
    assert queries.METRIC_DURATION == publish.METRIC_DURATION
    assert queries.METRIC_PHASE_DURATION == publish.METRIC_PHASE_DURATION
    assert queries.METRIC_TLS_REMAINING == publish.METRIC_CERT_REMAINING


def test_the_attribute_paths_read_here_are_the_keys_the_prober_writes() -> None:
    """ClickHouse's JSON type nests on the dots, so the key *is* the path."""
    import nexovue_scheduler.publish as publish

    assert f"attrs.{publish.ATTR_STEP}" in queries.PHASE
    assert f"attrs.{publish.ATTR_STATUS_CODE}" in queries.HTTP_STATUS
    assert f"attrs.{publish.ATTR_ISSUER}" in queries.TLS_ISSUER
    assert f"attrs.{publish.ATTR_SERVER_ADDRESS}" in queries.SERVER_ADDRESS
    assert f"attrs.{publish.ATTR_OUTCOME}" in queries.OUTCOME
    assert f"attrs.{publish.ATTR_FINAL}" in queries.FINAL
    assert f"attrs.{publish.ATTR_MAINTENANCE}" in queries.MAINTENANCE
    assert f"attrs.{publish.ATTR_RUN_ID}" in queries.RUN_ID
    assert f"attrs.{publish.ATTR_ATTEMPT}" in queries.ATTEMPT
    assert f"attrs.{publish.ATTR_LOCATION}" in queries.LOCATION
    assert f"log_attrs.{publish.ATTR_RUN_ID}" in queries.EVIDENCE_RUN_ID
    assert queries.PROBER_SERVICE_NAME == publish.PROBER_SERVICE_NAME


def test_uptime_counts_only_confirmed_measured_runs_outside_maintenance() -> None:
    """AC19, AC22, AC23 at the read: an overruled attempt, a misconfigured run and a
    run during maintenance are listed and never counted."""
    for statement in (
        queries.check_summaries_sql(check_type=False, down_only=False),
        queries.CHECK_TRENDS_SQL,
    ):
        assert "final_flag != 'false'" in statement
        assert "maintenance_flag != 'true'" in statement
        assert "'misconfigured', 'prober_error'" in statement


# ---------------------------------------------------------------------- filters
#
# Both predicates are inside the statement, ahead of its `LIMIT`: asking "what is
# down" of the rows that came back would answer "the down ones among the first
# hundred checks" with nothing able to say so.


def test_an_unfiltered_list_carries_neither_predicate() -> None:
    statement = queries.check_summaries_sql(check_type=False, down_only=False)

    assert "check_type:String" not in statement
    assert "HAVING" not in statement


async def test_one_check_type_narrows_the_rows_that_are_read() -> None:
    """The type is a resource attribute, so this is a `WHERE` in the inner read."""
    store = _store()
    start, end = _window()

    await service.list_check_results(
        _repository(TENANT_A, store),
        project_id=PROJECT_A,
        from_time=start,
        to_time=end,
        check_type="dns",
    )

    assert store.last_parameters["check_type"] == "dns"


def test_down_only_refuses_to_call_a_check_with_no_probe_down() -> None:
    """`argMaxIf` over no matching row returns zero, which would read as down.

    A check holding only a certificate reading has not failed — it has not run.
    `_to_summary` reports `up` as null in exactly that case, and this predicate
    has to agree with it or the filter and the column contradict each other.
    """
    statement = queries.check_summaries_sql(check_type=False, down_only=True)

    assert "runs > 0" in statement
    assert statement.index("HAVING") < statement.index("LIMIT")
