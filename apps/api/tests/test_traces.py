"""Trace reads: the page marker, the row mapping, and tenant isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.traces import queries, service
from nexovue_api.traces.cursor import TracePageCursor, decode_cursor
from nexovue_api.traces.schemas import TraceSummary

TENANT_A = "org-a"
TENANT_B = "org-b"
PROJECT = "project-1"

TRACE_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TRACE_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

_START_NANOS = 1_787_825_704_123_000_000

_SPAN_COLUMNS = [
    "span_id",
    "parent_span_id",
    "service_name",
    "span_name",
    "span_kind",
    "start_time_nanos",
    "duration_ms",
    "status_code",
    "status_message",
    "http_method",
    "http_route",
    "http_status",
    "rpc_system",
    "rpc_method",
    "rpc_status",
    "attributes_json",
]

# The two shapes a span comes in. An HTTP span leaves the RPC columns empty and
# a gRPC span leaves the HTTP ones empty; neither is a null.
_HTTP_SPAN_ROW = (
    b"00f067aa0ba902b7",
    "",
    "storefront",
    "GET /checkout",
    "SERVER",
    _START_NANOS,
    412.7,
    "ERROR",
    "boom",
    "GET",
    "/checkout",
    500,
    "",
    "",
    "",
    '{"tier":"gold"}',
)

_GRPC_SPAN_ROW = (
    b"00f067aa0ba902b7",
    "",
    "product-catalog",
    "oteldemo.ProductCatalogService/ListProducts",
    "SERVER",
    _START_NANOS,
    412.7,
    "ERROR",
    "boom",
    "",
    "",
    0,
    "grpc",
    "oteldemo.ProductCatalogService/ListProducts",
    "UNAVAILABLE",
    "{}",
)


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class FakeTelemetry:
    """Serves trace rows, honouring the tenant the repository bound."""

    def __init__(self, traces: dict[str, str], span_row: tuple[Any, ...] = _HTTP_SPAN_ROW) -> None:
        # tenant -> the one trace id that tenant owns
        self._traces = traces
        self._span_row = span_row

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        owned = self._traces.get(bound["tenant_id"])

        if "span_trace_lookup" in sql:
            matches = 1 if owned == bound.get("trace_id") else 0
            return FakeQueryResult(
                ["matches", "min_nanos", "max_nanos", "received_from_nanos", "received_to_nanos"],
                [
                    (
                        matches,
                        _START_NANOS,
                        _START_NANOS + 500_000_000,
                        _START_NANOS,
                        _START_NANOS + 500_000_000,
                    )
                ],
            )

        if "GROUP BY trace_id" in sql:
            if owned is None:
                return FakeQueryResult([], [])
            return FakeQueryResult(
                [
                    "trace_id",
                    "root_service",
                    "root_name",
                    "start_time_nanos",
                    "duration_ms",
                    "span_count",
                    "error_count",
                    "worst_severity",
                ],
                [(owned.encode(), "storefront", "GET /checkout", _START_NANOS, 412.7, 2, 1, 2)],
            )

        if owned != bound.get("trace_id"):
            return FakeQueryResult([], [])
        return FakeQueryResult(_SPAN_COLUMNS, [self._span_row])


def _repository(tenant: str, span_row: tuple[Any, ...] = _HTTP_SPAN_ROW) -> TenantScopedRepository:
    store = FakeTelemetry({TENANT_A: TRACE_A, TENANT_B: TRACE_B}, span_row)
    return TenantScopedRepository(store, tenant_id=tenant)  # type: ignore[arg-type]


def _window() -> tuple[datetime, datetime]:
    end = datetime(2026, 8, 27, 11, 0, tzinfo=UTC)
    return end - timedelta(hours=1), end


# ------------------------------------------------------------------- isolation


async def test_two_organizations_cannot_read_each_others_traces() -> None:
    start, end = _window()

    for_a = await service.list_traces(
        _repository(TENANT_A),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=50,
        cursor=None,
    )
    for_b = await service.list_traces(
        _repository(TENANT_B),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=50,
        cursor=None,
    )

    assert [trace.trace_id for trace in for_a.traces] == [TRACE_A]
    assert [trace.trace_id for trace in for_b.traces] == [TRACE_B]


async def test_another_tenants_trace_is_indistinguishable_from_one_that_never_existed() -> None:
    missing = "1" * 32

    with pytest.raises(NotFoundError) as other_tenant:
        await service.get_trace(_repository(TENANT_A), project_id=PROJECT, trace_id=TRACE_B)
    with pytest.raises(NotFoundError) as never_existed:
        await service.get_trace(_repository(TENANT_A), project_id=PROJECT, trace_id=missing)

    assert str(other_tenant.value) == str(never_existed.value)


async def test_a_trace_id_that_is_not_32_hex_characters_is_a_404_not_a_query() -> None:
    with pytest.raises(NotFoundError):
        await service.get_trace(_repository(TENANT_A), project_id=PROJECT, trace_id="nope")


# ---------------------------------------------------------------------- shapes


async def test_the_detail_response_reports_the_trace_as_the_contract_names_it() -> None:
    detail = await service.get_trace(_repository(TENANT_A), project_id=PROJECT, trace_id=TRACE_A)

    assert detail.trace_id == TRACE_A
    assert detail.span_count == 1
    assert detail.error_count == 1
    assert detail.spans[0].span_id == "00f067aa0ba902b7"
    assert detail.spans[0].parent_span_id == "", "a root span carries an empty string, never null"
    assert detail.spans[0].attributes == {"tier": "gold"}


async def test_a_grpc_span_reports_its_rpc_identity_and_not_an_http_one() -> None:
    """Five of the demo's twelve services emit no HTTP attributes at all."""
    detail = await service.get_trace(
        _repository(TENANT_A, _GRPC_SPAN_ROW), project_id=PROJECT, trace_id=TRACE_A
    )
    span = detail.spans[0]

    assert span.rpc_system == "grpc"
    assert span.rpc_method == "oteldemo.ProductCatalogService/ListProducts"
    assert span.rpc_status == "UNAVAILABLE"
    assert (span.http_method, span.http_route, span.http_status) == ("", "", 0), (
        "an RPC status must never be reported as an HTTP one"
    )


async def test_an_http_span_reports_no_rpc_identity() -> None:
    detail = await service.get_trace(_repository(TENANT_A), project_id=PROJECT, trace_id=TRACE_A)
    span = detail.spans[0]

    assert (span.rpc_system, span.rpc_method, span.rpc_status) == ("", "", "")
    assert span.http_status == 500


async def test_an_empty_window_is_an_empty_list_and_not_a_404() -> None:
    start, end = _window()
    empty = TenantScopedRepository(FakeTelemetry({}), tenant_id="org-with-nothing")  # type: ignore[arg-type]

    page = await service.list_traces(
        empty, project_id=PROJECT, from_time=start, to_time=end, limit=50, cursor=None
    )

    assert page.traces == []
    assert page.next_cursor is None


def test_a_timestamp_serializes_as_iso_8601_with_milliseconds_and_a_z() -> None:
    summary = TraceSummary(
        trace_id=TRACE_A,
        root_service="storefront",
        root_name="GET /checkout",
        start_time=datetime(2026, 8, 27, 10, 15, 4, 123_456, tzinfo=UTC),
        duration_ms=412.7,
        span_count=2,
        error_count=1,
        status_code="ERROR",
    )

    assert summary.model_dump(mode="json")["start_time"] == "2026-08-27T10:15:04.123Z"


# ---------------------------------------------------------------------- cursor


def test_a_cursor_survives_a_round_trip() -> None:
    original = TracePageCursor(start_time_nanos=_START_NANOS, trace_id=TRACE_A)

    assert decode_cursor(original.encode()) == original


def test_a_cursor_is_opaque_and_carries_no_padding() -> None:
    encoded = TracePageCursor(start_time_nanos=_START_NANOS, trace_id=TRACE_A).encode()

    assert "=" not in encoded
    assert TRACE_A not in encoded


@pytest.mark.parametrize("value", ["", "not-base64!!", "Zm9v", "MTIz"])
def test_a_cursor_this_endpoint_did_not_issue_is_rejected(value: str) -> None:
    with pytest.raises(InvalidRequestError):
        decode_cursor(value)


async def test_a_full_page_offers_a_cursor_and_a_short_one_does_not() -> None:
    start, end = _window()

    full = await service.list_traces(
        _repository(TENANT_A),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=1,
        cursor=None,
    )
    short = await service.list_traces(
        _repository(TENANT_A),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=50,
        cursor=None,
    )

    assert full.next_cursor is not None
    assert decode_cursor(full.next_cursor).trace_id == TRACE_A
    assert short.next_cursor is None


# -------------------------------------------------------------------- severity


def test_the_worst_status_in_a_trace_is_the_one_reported() -> None:
    assert queries.SEVERITY_ERROR > queries.SEVERITY_OK > queries.SEVERITY_UNSET


def test_every_severity_the_query_can_return_has_a_name() -> None:
    severities = {queries.SEVERITY_UNSET, queries.SEVERITY_OK, queries.SEVERITY_ERROR}

    assert severities == set(service._SEVERITY_NAMES)


def test_every_trace_query_carries_the_tenant_binding() -> None:
    """The repository would refuse these, but failing here names the culprit."""
    from nexovue_api.data.clickhouse.base import TENANT_BINDING

    for sql in (
        queries.list_traces_sql(with_cursor=False),
        queries.list_traces_sql(with_cursor=True),
        queries.TRACE_SPANS_SQL,
        queries.TRACE_TIME_BOUNDS_SQL,
    ):
        assert TENANT_BINDING in sql
