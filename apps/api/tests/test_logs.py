"""Log↔trace correlation, both directions, and what happens without a trace.

The fake store answers from the values the repository *bound*, never from the
statement text, so a query that lost its tenant predicate finds nothing and the
isolation test below fails rather than quietly passing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexovue_api.data.clickhouse.base import TENANT_BINDING, TenantScopedRepository
from nexovue_api.data.clickhouse.values import nanos_to_datetime
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.logs import queries, service
from nexovue_api.traces import service as traces_service
from nexovue_api.window import RECEIVE_CLOCK_SLACK

TENANT_A = "org-a"
TENANT_B = "org-b"
PROJECT = "project-1"

TRACE_A = "aa" * 16
TRACE_B = "bb" * 16
UNTRACED = "0" * 32

_SPAN_START_NANOS = 1_787_825_704_123_000_000
_SPAN_END_NANOS = _SPAN_START_NANOS + 500_000_000
_RECEIVED_FROM_NANOS = _SPAN_START_NANOS + 1_000_000
_RECEIVED_TO_NANOS = _SPAN_END_NANOS + 2_000_000

_LOG_COLUMNS = [
    "observed_nanos",
    "service_name",
    "severity",
    "severity_number",
    "body",
    "trace_ref",
    "span_ref",
    "attributes_json",
]


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class FakeLogStore:
    """Serves log rows, honouring the tenant the repository bound."""

    def __init__(self, traces: dict[str, str], *, trace_is_known: bool = True) -> None:
        # tenant -> the one trace id that tenant owns
        self._traces = traces
        self._trace_is_known = trace_is_known
        self.last_parameters: dict[str, Any] = {}

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.last_parameters = bound
        owned = self._traces.get(bound["tenant_id"])
        matches = owned is not None and owned == bound.get("trace_id")

        if "span_trace_lookup" in sql:
            found = 1 if matches and self._trace_is_known else 0
            return FakeQueryResult(
                ["matches", "min_nanos", "max_nanos", "received_from_nanos", "received_to_nanos"],
                [
                    (
                        found,
                        _SPAN_START_NANOS,
                        _SPAN_END_NANOS,
                        _RECEIVED_FROM_NANOS,
                        _RECEIVED_TO_NANOS,
                    )
                ],
            )

        if owned is None or not matches:
            return FakeQueryResult([], [])
        return FakeQueryResult(
            _LOG_COLUMNS,
            [
                (
                    _SPAN_START_NANOS,
                    "checkout",
                    "ERROR",
                    17,
                    "payment declined",
                    owned.encode(),
                    b"bb11bb11bb11bb11",
                    '{"tier":"gold"}',
                ),
                (
                    _SPAN_START_NANOS + 1_000,
                    "checkout",
                    "INFO",
                    9,
                    "retrying",
                    owned.encode(),
                    b"0" * 16,
                    "{}",
                ),
            ],
        )


def _repository(tenant: str, store: FakeLogStore | None = None) -> TenantScopedRepository:
    store = store or FakeLogStore({TENANT_A: TRACE_A, TENANT_B: TRACE_B})
    return TenantScopedRepository(store, tenant_id=tenant)  # type: ignore[arg-type]


def _window() -> tuple[datetime, datetime]:
    end = datetime(2026, 8, 27, 11, 0, tzinfo=UTC)
    return end - timedelta(hours=1), end


async def _read(tenant: str, trace_id: str, store: FakeLogStore | None = None) -> Any:
    start, end = _window()
    return await service.logs_for_trace(
        _repository(tenant, store),
        project_id=PROJECT,
        trace_id=trace_id,
        from_time=start,
        to_time=end,
        limit=200,
    )


# ------------------------------------------------------------------- isolation


async def test_two_organizations_cannot_read_each_others_logs() -> None:
    for_a = await _read(TENANT_A, TRACE_A)
    for_b = await _read(TENANT_A, TRACE_B)

    assert [record.trace_id for record in for_a.logs] == [TRACE_A, TRACE_A]
    assert for_b.logs == [], "another tenant's trace must return this tenant's nothing"


def test_the_log_correlation_query_carries_the_tenant_binding() -> None:
    """The repository would refuse it, but failing here names the culprit."""
    assert TENANT_BINDING in queries.LOGS_FOR_TRACE_SQL


# ----------------------------------------------------- logs for this trace


async def test_the_logs_of_a_trace_come_back_with_the_context_they_carried() -> None:
    answer = await _read(TENANT_A, TRACE_A)

    assert [record.body for record in answer.logs] == ["payment declined", "retrying"]
    assert answer.logs[0].span_id == "bb11bb11bb11bb11"
    assert answer.logs[0].attributes == {"tier": "gold"}


async def test_a_record_emitted_outside_any_span_reports_an_empty_span_id() -> None:
    answer = await _read(TENANT_A, TRACE_A)

    assert answer.logs[1].span_id == "", "an absent span id is empty, never sixteen zeroes"


# ------------------------------------------------- the trace this log belongs to


async def test_the_answer_names_the_trace_the_records_point_back_at() -> None:
    answer = await _read(TENANT_A, TRACE_A)

    assert answer.trace.trace_id == TRACE_A
    assert answer.trace.exists is True
    assert answer.trace.start_time is not None


async def test_a_trace_we_no_longer_hold_is_reported_as_a_dead_link_not_a_404() -> None:
    """Logs outlive spans: tail sampling drops traces, and retentions differ."""
    store = FakeLogStore({TENANT_A: TRACE_A}, trace_is_known=False)

    answer = await _read(TENANT_A, TRACE_A, store)

    assert answer.trace.exists is False
    assert answer.trace.start_time is None
    assert [record.body for record in answer.logs] == ["payment declined", "retrying"]


@pytest.mark.parametrize("malformed", ["", "nope", "aa" * 15, "zz" * 16])
async def test_a_trace_id_that_cannot_match_is_rejected_before_the_query(malformed: str) -> None:
    with pytest.raises(InvalidRequestError):
        await _read(TENANT_A, malformed)


async def test_a_log_with_no_trace_context_is_told_so_rather_than_searched_for() -> None:
    """An all-zero trace id is well formed and means "no trace", not "not found"."""
    with pytest.raises(InvalidRequestError) as rejected:
        await _read(TENANT_A, UNTRACED)

    assert str(rejected.value) == service.NO_TRACE_CONTEXT


# --------------------------------------------------------- partition pruning


async def test_a_known_trace_bounds_the_read_by_when_that_trace_arrived() -> None:
    """`logs` is partitioned on `received_at`, so only this prunes.

    The bounds come from when the *trace* arrived, not from the window the
    caller asked about — opening a trace from three weeks ago must not read
    three weeks of partitions.
    """
    store = FakeLogStore({TENANT_A: TRACE_A})

    await _read(TENANT_A, TRACE_A, store)

    arrived_first = nanos_to_datetime(_RECEIVED_FROM_NANOS) - traces_service.WINDOW_SLACK
    arrived_last = nanos_to_datetime(_RECEIVED_TO_NANOS) + traces_service.WINDOW_SLACK
    assert store.last_parameters["received_from"] == arrived_first - RECEIVE_CLOCK_SLACK
    assert store.last_parameters["received_to"] == arrived_last + RECEIVE_CLOCK_SLACK


async def test_an_unknown_trace_falls_back_to_the_callers_window_still_bounded() -> None:
    store = FakeLogStore({TENANT_A: TRACE_A}, trace_is_known=False)

    await _read(TENANT_A, TRACE_A, store)

    start, end = _window()
    assert store.last_parameters["received_from"] == start - RECEIVE_CLOCK_SLACK
    assert store.last_parameters["received_to"] == end + RECEIVE_CLOCK_SLACK
