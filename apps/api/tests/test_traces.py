"""Trace reads: the page marker, the filters, the row mapping, and isolation.

The fake store answers from what a statement *names*: a predicate the SQL stops
carrying stops being applied here too, so a filter that quietly disappears fails
a test rather than passing one. What only a real server can settle — that
`getSubcolumn` takes a bound path, that `toString` over a JSON value compares as
text, that the two statements agree — is in `test_traces_live.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexovue_api.data.clickhouse.base import TENANT_BINDING, TenantScopedRepository
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.traces import queries, service
from nexovue_api.traces.cursor import TracePageCursor, decode_cursor
from nexovue_api.traces.queries import TraceFilters
from nexovue_api.traces.schemas import TraceSummary

TENANT_A = "org-a"
TENANT_B = "org-b"
PROJECT = "project-1"

TRACE_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TRACE_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

_START_NANOS = 1_787_825_704_123_000_000

NO_FILTERS = TraceFilters()

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
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.calls.append((sql, bound))
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

        # Checked before the list: both group by `trace_id`, and only the root
        # lookup binds the page's ids.
        if "{trace_ids:Array(String)}" in sql:
            if owned is None or owned not in set(bound.get("trace_ids", [])):
                return FakeQueryResult([], [])
            return FakeQueryResult(
                ["trace_id", "root_service", "root_name"],
                [(owned.encode(), "storefront", "GET /checkout")],
            )

        if "GROUP BY trace_id" in sql:
            if owned is None:
                return FakeQueryResult([], [])
            return FakeQueryResult(
                [
                    "trace_id",
                    "start_time_nanos",
                    "duration_ms",
                    "span_count",
                    "error_count",
                    "worst_severity",
                ],
                [(owned.encode(), _START_NANOS, 412.7, 2, 1, 2)],
            )

        if owned != bound.get("trace_id"):
            return FakeQueryResult([], [])
        return FakeQueryResult(_SPAN_COLUMNS, [self._span_row])


def _store(span_row: tuple[Any, ...] = _HTTP_SPAN_ROW) -> FakeTelemetry:
    return FakeTelemetry({TENANT_A: TRACE_A, TENANT_B: TRACE_B}, span_row)


def _repository(tenant: str, span_row: tuple[Any, ...] = _HTTP_SPAN_ROW) -> TenantScopedRepository:
    return TenantScopedRepository(_store(span_row), tenant_id=tenant)  # type: ignore[arg-type]


def _window() -> tuple[datetime, datetime]:
    end = datetime(2026, 8, 27, 11, 0, tzinfo=UTC)
    return end - timedelta(hours=1), end


async def _page(
    repository: TenantScopedRepository,
    *,
    filters: TraceFilters = NO_FILTERS,
    limit: int = 50,
    cursor: str | None = None,
) -> Any:
    start, end = _window()
    return await service.list_traces(
        repository,
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        filters=filters,
        limit=limit,
        cursor=cursor,
    )


# ------------------------------------------------------------------- isolation


async def test_two_organizations_cannot_read_each_others_traces() -> None:
    for_a = await _page(_repository(TENANT_A))
    for_b = await _page(_repository(TENANT_B))

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
    empty = TenantScopedRepository(FakeTelemetry({}), tenant_id="org-with-nothing")  # type: ignore[arg-type]

    page = await _page(empty)

    assert page.traces == []
    assert page.next_cursor is None


async def test_a_page_with_no_rows_asks_the_store_for_no_root_spans() -> None:
    """The second read exists to name a page; there is nothing to name."""
    store = FakeTelemetry({})
    repository = TenantScopedRepository(store, tenant_id="org-with-nothing")  # type: ignore[arg-type]

    await _page(repository)

    assert not [sql for sql, _ in store.calls if "{trace_ids:Array(String)}" in sql]


async def test_the_root_service_and_name_come_from_the_second_read() -> None:
    """They are no longer in the group-by; losing that read would blank a row."""
    store = _store()
    page = await _page(TenantScopedRepository(store, tenant_id=TENANT_A))  # type: ignore[arg-type]

    roots = [bound for sql, bound in store.calls if "{trace_ids:Array(String)}" in sql]
    assert roots == [
        {
            "project_id": PROJECT,
            "from_time": _window()[0],
            "to_time": _window()[1],
            "trace_ids": [TRACE_A],
            "tenant_id": TENANT_A,
        }
    ]
    assert (page.traces[0].root_service, page.traces[0].root_name) == (
        "storefront",
        "GET /checkout",
    )


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
    full = await _page(_repository(TENANT_A), limit=1)
    short = await _page(_repository(TENANT_A), limit=50)

    assert full.next_cursor is not None
    assert decode_cursor(full.next_cursor).trace_id == TRACE_A
    assert short.next_cursor is None


# --------------------------------------------------------------- what a filter is


def test_an_unfiltered_list_carries_no_predicate_it_does_not_need() -> None:
    sql = queries.list_traces_sql(NO_FILTERS, with_cursor=False)

    assert "matched" not in sql
    assert "HAVING" not in sql


def test_a_span_filter_never_becomes_a_set_of_trace_ids() -> None:
    """`trace_id IN (SELECT …)` is 20x cheaper on a selective filter and fatal on
    a broad one: matching an attribute carried by 9.8M spans needed 1.68 GiB for
    the set alone and was killed. A filter must not fail where none succeeds."""
    sql = queries.list_traces_sql(
        TraceFilters(service_name="storefront", attribute_path="db.system", attribute_value="pg"),
        with_cursor=False,
    )

    assert "trace_id IN (" not in sql
    assert sql.count("FROM spans") == 1


@pytest.mark.parametrize(
    ("filters", "clause"),
    [
        (TraceFilters(service_name="storefront"), queries.SERVICE_CLAUSE),
        (TraceFilters(operation="GET /checkout"), queries.OPERATION_CLAUSE),
        (TraceFilters(status=queries.STATUS_ERROR), queries.STATUS_ERROR_CLAUSE),
        (TraceFilters(status=queries.STATUS_OK), queries.STATUS_OK_CLAUSE),
        (TraceFilters(min_duration_ms=250.0), queries.MIN_DURATION_CLAUSE),
        (TraceFilters(max_duration_ms=250.0), queries.MAX_DURATION_CLAUSE),
        (
            TraceFilters(attribute_path="db.system", attribute_value="postgresql"),
            queries.ATTRIBUTE_JSON_CLAUSE,
        ),
    ],
)
def test_each_filter_puts_its_own_predicate_in_the_statement(
    filters: TraceFilters, clause: str
) -> None:
    assert clause in queries.list_traces_sql(filters, with_cursor=False)


@pytest.mark.parametrize(
    ("filters", "clause"),
    [
        (TraceFilters(service_name="storefront"), queries.OPERATION_CLAUSE),
        (TraceFilters(status=queries.STATUS_ERROR), queries.STATUS_OK_CLAUSE),
        (TraceFilters(min_duration_ms=1.0), queries.MAX_DURATION_CLAUSE),
        (NO_FILTERS, queries.ATTRIBUTE_JSON_CLAUSE),
        (NO_FILTERS, queries.CURSOR_CLAUSE),
    ],
)
def test_a_filter_nobody_asked_for_is_not_in_the_statement(
    filters: TraceFilters, clause: str
) -> None:
    """An unasked predicate would narrow a page the caller believes is complete."""
    assert clause not in queries.list_traces_sql(filters, with_cursor=False)


def test_several_filters_are_conjoined_rather_than_replacing_one_another() -> None:
    sql = queries.list_traces_sql(
        TraceFilters(
            service_name="storefront",
            operation="GET /checkout",
            attribute_path="db.system",
            attribute_value="postgresql",
            status=queries.STATUS_ERROR,
            min_duration_ms=10.0,
            max_duration_ms=99.0,
        ),
        with_cursor=True,
    )

    for clause in (
        queries.SERVICE_CLAUSE,
        queries.OPERATION_CLAUSE,
        queries.ATTRIBUTE_JSON_CLAUSE,
        queries.SPAN_MATCH_CLAUSE,
        queries.STATUS_ERROR_CLAUSE,
        queries.MIN_DURATION_CLAUSE,
        queries.MAX_DURATION_CLAUSE,
        queries.CURSOR_CLAUSE,
    ):
        assert clause in sql
    assert sql.count("HAVING") == 1


def test_a_span_filter_marks_rows_rather_than_removing_them_from_the_count() -> None:
    """Filtering the counted rows would report a trace's duration as one span's."""
    sql = queries.list_traces_sql(TraceFilters(service_name="storefront"), with_cursor=False)
    selected, _, scanned = sql.partition("FROM spans")

    assert f"({queries.SERVICE_CLAUSE})" in selected, "the predicate is not the flag"
    assert queries.SERVICE_CLAUSE not in scanned, "the filter removed spans from the trace"
    assert queries.SPAN_MATCH_CLAUSE in sql


def test_two_span_filters_must_be_satisfied_by_one_span_together() -> None:
    """Otherwise "a failing POST on checkout" matches three unrelated spans.

    The outer parentheses are the load-bearing part: ClickHouse binds `AS`
    tighter than `AND`, so `a AND b AS matched` names `b` and drops `a`.
    """
    sql = queries.list_traces_sql(
        TraceFilters(service_name="storefront", operation="GET /checkout"), with_cursor=False
    )

    assert f"(({queries.SERVICE_CLAUSE}) AND ({queries.OPERATION_CLAUSE})) AS matched" in sql


async def test_a_filter_value_is_bound_and_never_written_into_the_statement() -> None:
    hostile = "storefront' OR 1=1 --"
    store = _store()

    await _page(
        TenantScopedRepository(store, tenant_id=TENANT_A),  # type: ignore[arg-type]
        filters=TraceFilters(
            service_name=hostile, attribute_path="db.system", attribute_value=hostile
        ),
    )

    listed = [(sql, bound) for sql, bound in store.calls if "GROUP BY trace_id" in sql]
    assert listed, "no list statement ran, so this proves nothing"
    assert all(hostile not in sql for sql, _ in listed)
    _, bound = listed[0]
    assert bound["service_name"] == hostile
    assert bound["attr_value"] == hostile
    assert bound["attr_path"] == "db.system"


async def test_only_the_filters_the_statement_names_are_bound() -> None:
    """A bound value with no predicate is a filter the caller thinks is applied."""
    store = _store()

    await _page(
        TenantScopedRepository(store, tenant_id=TENANT_A),  # type: ignore[arg-type]
        filters=TraceFilters(service_name="storefront"),
    )

    _, bound = next((sql, b) for sql, b in store.calls if "GROUP BY trace_id" in sql)
    assert "operation" not in bound
    assert "attr_path" not in bound
    assert "min_duration_ms" not in bound


# ------------------------------------- promoted column against the JSON column


@pytest.mark.parametrize(
    "path",
    ["http.request.method", "http.method", "http.route", "http.response.status_code"],
)
def test_an_attribute_the_worker_promoted_verbatim_filters_the_typed_column(path: str) -> None:
    """Measured over a 7-day window: 496 MiB read against 3.57 GiB for the JSON.

    It is also the only spelling-independent answer — the column carries both
    semantic-convention generations, and each JSON path carries only one.
    """
    clause = queries.attribute_clause(path)

    assert clause == queries.PROMOTED_ATTRIBUTE_CLAUSES[path]
    assert "span_attrs" not in clause
    assert "{attr_value:String}" in clause


@pytest.mark.parametrize(
    "path",
    ["rpc.system", "rpc.method", "rpc.grpc.status_code", "rpc.response.status_code"],
)
def test_an_rpc_attribute_is_answered_from_the_json_and_not_its_promoted_column(path: str) -> None:
    """The worker rewrites these on the way in — `connect_rpc` is stored as
    `connectrpc`, gRPC status 14 as `UNAVAILABLE`, `rpc.method` qualified with
    `rpc.service`. The column would be compared against something else."""
    assert queries.attribute_clause(path) == queries.ATTRIBUTE_JSON_CLAUSE


def test_an_attribute_nobody_promoted_is_answered_from_the_json() -> None:
    assert queries.attribute_clause("demo.order.id") == queries.ATTRIBUTE_JSON_CLAUSE


def test_the_json_attribute_path_is_a_bound_parameter_and_not_part_of_the_text() -> None:
    """125 distinct paths in one hour of the demo: the set cannot be enumerated."""
    assert "{attr_path:String}" in queries.ATTRIBUTE_JSON_CLAUSE


def test_a_json_attribute_is_compared_as_text() -> None:
    """Both sides in one type rather than relying on how `Dynamic` coerces.

    This is the one assertion in the file that a negative control does not
    break: ClickHouse 26.3 coerces a Dynamic against a String on `=`, so
    dropping the cast changes no answer today. It is asserted anyway, because
    the coercion is a property of the type — the same type the server refuses
    outright as a GROUP BY key — and not a contract this filter should rest on.
    """
    assert queries.ATTRIBUTE_JSON_CLAUSE.startswith("toString(getSubcolumn(span_attrs,")


# ---------------------------------------------------- what a caller may ask for


def test_a_filter_a_caller_left_blank_is_not_a_filter() -> None:
    filters = service.build_filters(
        service_name="   ",
        operation="",
        status=None,
        min_duration_ms=None,
        max_duration_ms=None,
        attribute_path=None,
        attribute_value=None,
    )

    assert filters == TraceFilters()
    assert not filters.has_span_filter


def _built(**overrides: Any) -> TraceFilters:
    arguments: dict[str, Any] = {
        "service_name": None,
        "operation": None,
        "status": None,
        "min_duration_ms": None,
        "max_duration_ms": None,
        "attribute_path": None,
        "attribute_value": None,
    }
    return service.build_filters(**{**arguments, **overrides})


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "BROKEN"},
        {"min_duration_ms": -1.0},
        {"max_duration_ms": -1.0},
        {"min_duration_ms": 900.0, "max_duration_ms": 100.0},
        {"attribute_path": "db.system"},
        {"attribute_path": "not a path", "attribute_value": "x"},
        {"attribute_path": "a" * 300, "attribute_value": "x"},
    ],
)
def test_a_filter_that_cannot_mean_anything_is_refused_rather_than_queried(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(InvalidRequestError):
        _built(**overrides)


@pytest.mark.parametrize("status", [queries.STATUS_ERROR, queries.STATUS_OK])
def test_the_two_statuses_a_caller_may_filter_on_are_the_stored_spellings(status: str) -> None:
    """`STATUS_CODE_ERROR` is the protobuf name; storage holds the short one."""
    assert _built(status=status).status == status
    assert status in service.STATUS_FILTERS


def test_a_duration_range_the_right_way_round_is_kept() -> None:
    filters = _built(min_duration_ms=100.0, max_duration_ms=900.0)

    assert (filters.min_duration_ms, filters.max_duration_ms) == (100.0, 900.0)


# ---------------------------------------------------------------------- facets


async def test_the_facets_are_the_services_and_operations_the_window_holds() -> None:
    rows = [
        ("storefront", "GET /checkout", 90, 40),
        ("storefront", "GET /browse", 10, 8),
        ("payments", "POST /charge", 50, 30),
    ]

    class FacetStore:
        async def query(
            self,
            sql: str,
            parameters: dict[str, Any] | None = None,
            settings: dict[str, Any] | None = None,
        ) -> FakeQueryResult:
            return FakeQueryResult(["service_name", "span_name", "spans", "traces"], rows)

    start, end = _window()
    facets = await service.trace_facets(
        TenantScopedRepository(FacetStore(), tenant_id=TENANT_A),  # type: ignore[arg-type]
        project_id=PROJECT,
        from_time=start,
        to_time=end,
    )

    assert [facet.service_name for facet in facets.services] == ["storefront", "payments"]
    assert facets.services[0].spans == 100
    assert [facet.operation for facet in facets.operations] == [
        "GET /checkout",
        "GET /browse",
        "POST /charge",
    ]
    assert facets.operations[0].service_name == "storefront"


def test_the_facet_statement_offers_no_attribute_list() -> None:
    """Attribute paths are unbounded — 125 from 15 services in one hour."""
    assert "span_attrs" not in queries.TRACE_FACETS_SQL


# -------------------------------------------------------------------- severity


def test_the_worst_status_in_a_trace_is_the_one_reported() -> None:
    assert queries.SEVERITY_ERROR > queries.SEVERITY_OK > queries.SEVERITY_UNSET


def test_every_severity_the_query_can_return_has_a_name() -> None:
    severities = {queries.SEVERITY_UNSET, queries.SEVERITY_OK, queries.SEVERITY_ERROR}

    assert severities == set(service._SEVERITY_NAMES)


def test_a_trace_with_no_error_but_no_ok_is_not_filtered_out_as_a_failure() -> None:
    """`UNSET` is the status nobody set; treating it as a failure hides good traces."""
    assert f"worst_severity < {queries.SEVERITY_ERROR}" == queries.STATUS_OK_CLAUSE
    assert queries.SEVERITY_UNSET < queries.SEVERITY_ERROR


def test_every_trace_query_carries_the_tenant_binding() -> None:
    """The repository would refuse these, but failing here names the culprit."""
    every_filter = TraceFilters(
        service_name="storefront",
        operation="GET /checkout",
        attribute_path="db.system",
        attribute_value="postgresql",
        status=queries.STATUS_ERROR,
        min_duration_ms=1.0,
        max_duration_ms=2.0,
    )

    for sql in (
        queries.list_traces_sql(NO_FILTERS, with_cursor=False),
        queries.list_traces_sql(NO_FILTERS, with_cursor=True),
        queries.list_traces_sql(every_filter, with_cursor=True),
        queries.TRACE_ROOTS_SQL,
        queries.TRACE_FACETS_SQL,
        queries.TRACE_SPANS_SQL,
        queries.TRACE_TIME_BOUNDS_SQL,
    ):
        assert TENANT_BINDING in sql


def test_a_filtered_list_reads_the_table_once_and_scopes_that_read() -> None:
    """A second read of `spans` would be a second place to forget the tenant."""
    sql = queries.list_traces_sql(
        TraceFilters(service_name="storefront", attribute_path="db.system", attribute_value="pg"),
        with_cursor=True,
    )

    assert sql.count("FROM spans") == 1
    assert sql.count(TENANT_BINDING) == 1
    assert "{project_id:String}" in sql
