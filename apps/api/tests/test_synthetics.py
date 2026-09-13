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
    "latest_run_nanos",
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
    "mean_seconds",
]
_RUN_COLUMNS: Final = [
    "observed_nanos",
    "status_points",
    "up_value",
    "status_code",
    "duration_seconds",
    "phase_total_seconds",
]


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

    def __init__(self, owners: dict[str, tuple[str, str]]) -> None:
        # tenant -> (the project it owns, the check inside it)
        self._owners = owners
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

        # Dispatch on which statement was run, by identity rather than by
        # sniffing its text. A substring did this until a shared `GROUP BY
        # check_id, metric_name, series_hash, timestamp` appeared inside three
        # statements and every one of them started answering as the summary.
        if sql == queries.CHECK_SUMMARIES_SQL:
            return FakeQueryResult(_SUMMARY_COLUMNS, [_summary_row(check_id)])
        if sql == queries.CHECK_TRENDS_SQL:
            return FakeQueryResult(_TREND_COLUMNS, _trend_rows(check_id))

        if bound.get("check_id") != check_id:
            return FakeQueryResult([], [])
        if sql == queries.PHASE_BREAKDOWN_SQL:
            return FakeQueryResult(_PHASE_COLUMNS, _phase_rows())
        if sql == queries.CHECK_RUNS_SQL:
            return FakeQueryResult(_RUN_COLUMNS, _run_rows())
        raise AssertionError(f"the fake store was asked a statement it does not serve:\n{sql}")


def _trend_rows(check_id: str) -> list[tuple[Any, ...]]:
    """Two buckets: one healthy, one where nothing succeeded and so has no duration."""
    return [
        (check_id, _BUCKET_NANOS, 5, 5, _DURATION_SECONDS),
        (check_id, _BUCKET_NANOS + _BUCKET_WIDTH_NANOS, 5, 0, None),
    ]


def _summary_row(check_id: str) -> tuple[Any, ...]:
    return (
        check_id,
        "http",
        200,  # runs
        190,  # successful_runs
        1.0,  # latest_up
        200,  # latest_status_code
        _RUN_NANOS,
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
        (_RUN_NANOS, 1, 1.0, 200, _DURATION_SECONDS, sum(_PHASE_SECONDS.values())),
        (_RUN_NANOS - 60_000_000_000, 1, 0.0, 503, 0.2, 0.2),
        # A group whose status point has not landed: half a run, not a run.
        (_RUN_NANOS - 120_000_000_000, 0, 0.0, 0, 0.3, 0.3),
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
