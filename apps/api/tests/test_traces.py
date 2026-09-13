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
from nexovue_api.traces import queries, router, service
from nexovue_api.traces.cursor import TracePageCursor, decode_cursor
from nexovue_api.traces.queries import ENDPOINT_SEPARATOR, TraceFilters
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
    "exception_type",
    "exception_message",
    "deployment_version",
    "events_json",
    "link_trace_ids",
    "link_span_ids",
    "link_attributes",
    "attributes_json",
]

# The two shapes a span comes in. An HTTP span leaves the RPC columns empty and
# a gRPC span leaves the HTTP ones empty; neither is a null.
_HTTP_SPAN_ROW: tuple[Any, ...] = (
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
    # The HTTP span is the failing one, so it carries what it threw.
    "ValueError",
    "card declined",
    "2026.9.12-a1b2c3d",
    # Out of order on purpose: an exporter is not required to sort, and the
    # timeline has to.
    (
        '[{"timestamp":"2026-08-27T10:00:00.200Z","name":"exception",'
        '"attributes":{"exception":{"stacktrace":"...frames..."}}},'
        '{"timestamp":"2026-08-27T10:00:00.100Z","name":"retry","attributes":{}}]'
    ),
    ["4bf92f3577b34da6a3ce929d0e0e4736"],
    ["00f067aa0ba902b7"],
    ['{"kind":"queue"}'],
    '{"tier":"gold"}',
)

_GRPC_SPAN_ROW: tuple[Any, ...] = (
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
    # Failed without throwing: a status is not an exception, and the two halves
    # stay empty rather than being filled in from the status.
    "",
    "",
    "",
    "",
    [],
    [],
    [],
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
        # How many rows the ordered read returns for the one trace this store
        # owns. More than one is a trace with more than one summary row.
        self.page_rows = 1
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

        # The list's two reads over the per-trace summary. Checked before the
        # `spans` branches below, which the same markers would otherwise catch.
        if "FROM trace_summaries" in sql:
            if owned is None:
                return FakeQueryResult([], [])
            if "{trace_ids:Array(String)}" in sql:
                if owned not in set(bound.get("trace_ids", [])):
                    return FakeQueryResult([], [])
                return FakeQueryResult(
                    [
                        "trace_id",
                        "start_time_nanos",
                        "duration_ms",
                        "span_count",
                        "error_count",
                        "worst_severity",
                        "root_service",
                        "root_name",
                        "span_services",
                        "span_endpoints",
                    ],
                    [
                        (
                            owned.encode(),
                            _START_NANOS,
                            412.7,
                            2,
                            1,
                            2,
                            "storefront",
                            "GET /checkout",
                            ["storefront", "payments"],
                            # Stored packed, exactly as the worker writes them.
                            [
                                f"storefront{ENDPOINT_SEPARATOR}GET /checkout",
                                f"payments{ENDPOINT_SEPARATOR}POST /charge",
                            ],
                        )
                    ],
                )
            return FakeQueryResult(
                ["trace_id", "start_time_nanos"],
                [(owned.encode(), _START_NANOS)] * self.page_rows,
            )

        # Checked before the list: both group by `trace_id`, and only the root
        # lookup binds the page's ids.
        if "{trace_ids:Array(String)}" in sql:
            if owned is None or owned not in set(bound.get("trace_ids", [])):
                return FakeQueryResult([], [])
            return FakeQueryResult(
                ["trace_id", "root_service", "root_name", "span_services", "span_endpoints"],
                [
                    (
                        owned.encode(),
                        "storefront",
                        "GET /checkout",
                        ["storefront", "payments"],
                        # Read from `spans`, so a pair arrives as a tuple.
                        [("storefront", "GET /checkout"), ("payments", "POST /charge")],
                    )
                ],
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


def _repository_over(store: FakeTelemetry, tenant: str) -> TenantScopedRepository:
    """A repository over a store the test keeps, so it can read the calls back."""
    return TenantScopedRepository(store, tenant_id=tenant)  # type: ignore[arg-type]


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


async def test_the_second_read_is_bounded_by_the_page_and_not_by_the_window() -> None:
    """What makes it a primary-key range rather than fifty scattered probes.

    Fifty ids alone left the bloom filter reading 1.91M rows of the dev store;
    the page's own span of start times took the same answer to 27k. Losing this
    bound is not a wrong answer, so nothing else would catch it.
    """
    store = _store()
    page = await _page(TenantScopedRepository(store, tenant_id=TENANT_A))  # type: ignore[arg-type]

    values = [bound for sql, bound in store.calls if "{trace_ids:Array(String)}" in sql]
    assert values == [
        {
            "project_id": PROJECT,
            "trace_ids": [TRACE_A],
            "page_from_nanos": _START_NANOS,
            "page_to_nanos": _START_NANOS,
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
        queries.trace_page_sql(NO_FILTERS, with_cursor=False),
        queries.trace_page_sql(NO_FILTERS, with_cursor=True),
        queries.trace_page_sql(every_filter, with_cursor=True),
        queries.TRACE_PAGE_VALUES_SQL,
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


# ------------------------------------------------ the per-trace summary (P1-41)


async def test_the_list_reads_the_summary_and_never_groups_the_window() -> None:
    """The whole point of `trace_summaries`: the page costs the page.

    Grouping `spans` for the same answer held one hash-table entry per trace in
    the window — 292 MiB and 393 ms over a week of the dev store, and 1.8 GiB
    before the strings left the group, which the server killed.
    """
    store = _store()
    await _page(_repository_over(store, TENANT_A))

    statements = [sql for sql, _ in store.calls]
    assert all("FROM spans" not in sql for sql in statements), (
        "the unfiltered list still reads `spans`"
    )
    assert "GROUP BY" not in queries.trace_page_sql(NO_FILTERS, with_cursor=False), (
        "the page statement aggregates; its cost then grows with the window again"
    )


async def test_an_attribute_filter_falls_back_to_grouping_spans() -> None:
    """The one filter no per-trace row can carry, and it is answered rather than
    dropped: attribute paths are unbounded, so the summary cannot hold them."""
    store = _store()
    await _page(
        _repository_over(store, TENANT_A),
        filters=TraceFilters(attribute_path="db.system", attribute_value="postgresql"),
    )

    statements = [sql for sql, _ in store.calls]
    assert any("FROM spans" in sql for sql in statements)
    assert all("FROM trace_summaries" not in sql for sql in statements)


@pytest.mark.parametrize(
    ("filters", "clause"),
    [
        (TraceFilters(service_name="storefront"), queries.SUMMARY_SERVICE_CLAUSE),
        (TraceFilters(operation="GET /checkout"), queries.SUMMARY_OPERATION_CLAUSE),
        (
            TraceFilters(service_name="storefront", operation="GET /checkout"),
            queries.SUMMARY_ENDPOINT_CLAUSE,
        ),
        (TraceFilters(status=queries.STATUS_ERROR), queries.SUMMARY_STATUS_ERROR_CLAUSE),
        (TraceFilters(status=queries.STATUS_OK), queries.SUMMARY_STATUS_OK_CLAUSE),
        (TraceFilters(min_duration_ms=250.0), queries.MIN_DURATION_CLAUSE),
        (TraceFilters(max_duration_ms=250.0), queries.MAX_DURATION_CLAUSE),
    ],
)
def test_every_filter_the_summary_answers_puts_its_own_predicate_there(
    filters: TraceFilters, clause: str
) -> None:
    assert clause in queries.trace_page_sql(filters, with_cursor=False)


def test_a_service_and_an_operation_are_matched_as_one_pair() -> None:
    """A span filter matches when ONE span satisfies all of it together. Two
    independent tests would match a trace whose service and whose operation are
    on different spans, which is three unrelated spans reported as one."""
    sql = queries.trace_page_sql(
        TraceFilters(service_name="storefront", operation="GET /checkout"), with_cursor=False
    )

    assert queries.SUMMARY_ENDPOINT_CLAUSE in sql
    assert queries.SUMMARY_SERVICE_CLAUSE not in sql
    assert queries.SUMMARY_OPERATION_CLAUSE not in sql


async def test_the_pair_is_bound_joined_and_the_separator_never_enters_the_sql() -> None:
    store = _store()
    await _page(
        _repository_over(store, TENANT_A),
        filters=TraceFilters(service_name="storefront", operation="GET /checkout"),
    )

    page = next(bound for sql, bound in store.calls if "span_endpoints" in sql)
    assert page["endpoint"] == "storefront" + queries.ENDPOINT_SEPARATOR + "GET /checkout"
    assert "service_name" not in page and "operation" not in page
    assert queries.ENDPOINT_SEPARATOR not in queries.trace_page_sql(
        TraceFilters(service_name="a", operation="b"), with_cursor=False
    )


def test_an_operation_without_a_service_matches_the_pairs_half() -> None:
    sql = queries.trace_page_sql(TraceFilters(operation="GET /checkout"), with_cursor=False)

    assert queries.SUMMARY_OPERATION_CLAUSE in sql
    assert "{operation_suffix:String}" in sql


def test_an_unfiltered_page_carries_no_predicate_beyond_the_window() -> None:
    sql = queries.trace_page_sql(NO_FILTERS, with_cursor=False)

    assert "span_services" not in sql
    assert "span_endpoints" not in sql
    assert "worst_severity" not in sql
    assert "cursor_nanos" not in sql


def test_only_the_summary_filters_the_statement_names_are_bound() -> None:
    """An unbound parameter is a 400 from ClickHouse, not a wrong answer."""
    assert service._summary_parameters(NO_FILTERS) == {}
    assert service._summary_parameters(TraceFilters(service_name="storefront")) == {
        "service_name": "storefront"
    }
    assert service._summary_parameters(TraceFilters(min_duration_ms=1.0)) == {
        "min_duration_ms": 1.0
    }


async def test_one_trace_named_twice_by_the_page_is_one_row_on_the_screen() -> None:
    """A trace has more than one summary row when it gained a span after the
    sampler forgot it, and rows sharing a key stay two rows until a background
    merge. The page collapses them; the statement deliberately does not, because
    `LIMIT 1 BY` costs the read its sort-key order — 63.5 MiB against 17."""
    store = _store()
    store.page_rows = 3
    page = await _page(_repository_over(store, TENANT_A))

    assert [trace.trace_id for trace in page.traces] == [TRACE_A]


async def test_the_cursor_names_a_position_in_the_ordered_read() -> None:
    """Not a position in the response: a trace can drop out between the two
    reads, and a keyset built from what survived would skip everything between."""
    store = _store()
    store.page_rows = 2
    page = await _page(_repository_over(store, TENANT_A), limit=2)

    assert page.next_cursor is not None
    assert decode_cursor(page.next_cursor) == TracePageCursor(
        start_time_nanos=_START_NANOS, trace_id=TRACE_A
    )


# --------------------------------------------------- what a trace touched (T22)
#
# `span_services` and `span_endpoints` were already *filtering* the list —
# `SUMMARY_SERVICE_CLAUSE` and `SUMMARY_ENDPOINT_CLAUSE` read them — and were
# never returned, so a row could be narrowed by what a trace touched but could
# not say it. These put them on the wire.
#
# They arrive in two shapes, because the list is read two ways. From
# `trace_summaries` a pair is one string with a unit separator inside it, the
# way the worker packs it. From `spans` — the fallback an attribute filter
# forces — a pair is a tuple, because nothing packed it. Both must answer the
# same, or the same trace describes itself differently depending on which filter
# happened to be applied.


async def test_a_trace_says_which_services_it_touched_not_only_its_root() -> None:
    page = await _page(_repository(TENANT_A))

    assert page.traces[0].root_service == "storefront"
    assert page.traces[0].span_services == ["payments", "storefront"]


async def test_a_stored_endpoint_pair_is_split_rather_than_shipped_packed() -> None:
    """The separator is the worker's storage detail and stops at this boundary.

    A client that had to split these would be parsing our storage format, and
    `GET /charge` means nothing without the service that served it — which is
    why the pair is kept together at all rather than being two flat lists.
    """
    page = await _page(_repository(TENANT_A))

    assert [(pair.service_name, pair.operation) for pair in page.traces[0].span_endpoints] == [
        ("payments", "POST /charge"),
        ("storefront", "GET /checkout"),
    ]
    for pair in page.traces[0].span_endpoints:
        assert ENDPOINT_SEPARATOR not in pair.service_name
        assert ENDPOINT_SEPARATOR not in pair.operation


async def test_the_fallback_read_describes_a_trace_the_same_way() -> None:
    """An attribute filter groups `spans` instead, and must not answer differently.

    The two reads take the pair in different shapes — packed from
    `trace_summaries`, a tuple from `spans` — and a reader must not be able to
    tell which one served them.
    """
    attribute = TraceFilters(attribute_path="db.system", attribute_value="postgresql")
    grouped = await _page(_repository(TENANT_A), filters=attribute)
    summarised = await _page(_repository(TENANT_A))

    assert grouped.traces[0].span_services == summarised.traces[0].span_services
    assert grouped.traces[0].span_endpoints == summarised.traces[0].span_endpoints


def test_a_pair_missing_its_separator_is_dropped_rather_than_guessed_at() -> None:
    """The worker writes both halves together, so a bare string is not a pair.

    Inventing a service name for it — or taking the whole string as one — would
    put an endpoint on the row that never ran.
    """
    split = service._split_stored_endpoints(
        [f"storefront{ENDPOINT_SEPARATOR}GET /", "no-separator-here"]
    )

    assert [(pair.service_name, pair.operation) for pair in split] == [("storefront", "GET /")]


def test_both_shapes_come_back_in_one_order() -> None:
    """`groupUniqArray` has no order to preserve, and neither store sorts.

    Without this the same request twice answers with the same set arranged
    differently, which reads as a change and makes a response impossible to diff.
    """
    packed = service._split_stored_endpoints(
        [f"z-service{ENDPOINT_SEPARATOR}POST /b", f"a-service{ENDPOINT_SEPARATOR}GET /a"]
    )
    tupled = service._pairs_to_endpoints([("z-service", "POST /b"), ("a-service", "GET /a")])

    assert packed == tupled
    assert [pair.service_name for pair in packed] == ["a-service", "z-service"]


async def test_a_span_that_threw_reports_what_it_threw() -> None:
    """Stored on the span, not only inside `events`.

    A detail pane leads with the exception, and should not have to parse a JSON
    blob carrying a full stacktrace to find out what the exception was.
    """
    detail = await service.get_trace(_repository(TENANT_A), project_id=PROJECT, trace_id=TRACE_A)
    span = detail.spans[0]

    assert (span.exception_type, span.exception_message) == ("ValueError", "card declined")
    assert span.deployment_version == "2026.9.12-a1b2c3d"


async def test_a_span_that_failed_without_throwing_reports_empty_strings() -> None:
    """A gRPC status is an outcome, not an exception.

    Empty rather than null, and never filled in from the status: "UNAVAILABLE"
    is not something the service threw, and showing it as one would invent a
    stacktrace's worth of meaning that does not exist.
    """
    detail = await service.get_trace(
        _repository(TENANT_A, _GRPC_SPAN_ROW), project_id=PROJECT, trace_id=TRACE_A
    )
    span = detail.spans[0]

    assert span.rpc_status == "UNAVAILABLE"
    assert (span.exception_type, span.exception_message, span.deployment_version) == ("", "", "")


def test_the_summary_read_asks_for_both_columns() -> None:
    """They are merged with the function the column was declared with.

    A trace whose spans arrived in separate blocks has several rows in
    `trace_summaries`, and the union of their arrays is the trace's own set.
    """
    assert "groupUniqArrayArray(span_services)" in queries.TRACE_PAGE_VALUES_SQL
    assert "groupUniqArrayArray(span_endpoints)" in queries.TRACE_PAGE_VALUES_SQL


def test_the_window_read_still_lets_no_string_out_of_it() -> None:
    """The memory bound this module documents, held as a property of the source.

    The read that finds the page is bounded by the window rather than by a page
    of ids, so a string in its group-by is unbounded memory. Both columns are
    gathered by the page-scoped read instead — which is where every other string
    a row needs already comes from.
    """
    window_read = queries.list_traces_sql(NO_FILTERS, with_cursor=False)

    assert "span_services" not in window_read
    assert "span_endpoints" not in window_read
    assert "span_services" in queries.TRACE_ROOTS_SQL
    assert "{trace_ids:Array(String)}" in queries.TRACE_ROOTS_SQL


# ------------------------------------------------ events and links (T24)
#
# Both are stored in shapes that belong to the store, not to the contract:
# `events` is one JSON string in OTLP's own shape, `links` is three parallel
# arrays of a nested column. Taking them apart here is what stops a client
# parsing either.


async def test_a_spans_events_come_back_as_a_timeline() -> None:
    detail = await service.get_trace(_repository(TENANT_A), project_id=PROJECT, trace_id=TRACE_A)
    events = detail.spans[0].events

    assert [event.name for event in events] == ["retry", "exception"]
    # An exception's stacktrace stays inside its attributes, which is OTLP's
    # shape. Lifting it to a field would make ours disagree with the wire.
    assert events[1].attributes["exception"]["stacktrace"] == "...frames..."


async def test_events_are_ordered_however_the_exporter_sent_them() -> None:
    """A timeline out of order is not one.

    The store preserves the exporter's order and an exporter is not required to
    sort, so the fixture above is deliberately reversed.
    """
    detail = await service.get_trace(_repository(TENANT_A), project_id=PROJECT, trace_id=TRACE_A)
    times = [event.at for event in detail.spans[0].events]

    assert times == sorted(times)


def test_an_event_with_no_usable_timestamp_is_dropped_rather_than_placed() -> None:
    """Its position inside the span is the whole reason to draw a timeline.

    Putting an unplaced event at the span's start would invent that position,
    and a reader cannot tell an invented one from a measured one.
    """
    parsed = service._to_events(
        '[{"name":"no-clock"},'
        '{"timestamp":"not-a-time","name":"bad-clock"},'
        '{"timestamp":"2026-08-27T10:00:00Z","name":"kept"}]'
    )

    assert [event.name for event in parsed] == ["kept"]


def test_unreadable_events_are_no_events_rather_than_an_error() -> None:
    """A span whose events column is malformed still has a waterfall row."""
    assert service._to_events("not json at all") == []
    assert service._to_events("") == []


async def test_a_spans_links_are_zipped_back_out_of_their_parallel_arrays() -> None:
    detail = await service.get_trace(_repository(TENANT_A), project_id=PROJECT, trace_id=TRACE_A)
    links = detail.spans[0].links

    assert len(links) == 1
    assert links[0].trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert links[0].span_id == "00f067aa0ba902b7"
    assert links[0].attributes == {"kind": "queue"}


def test_a_link_pointing_at_no_trace_is_dropped() -> None:
    """It points nowhere, and offering it would be offering a dead end."""
    zipped = service._to_links(
        {
            "link_trace_ids": ["", "4bf92f3577b34da6a3ce929d0e0e4736"],
            "link_span_ids": ["00f067aa0ba902b7", "00f067aa0ba902b7"],
            "link_attributes": ["{}", "{}"],
        }
    )

    assert [link.trace_id for link in zipped] == ["4bf92f3577b34da6a3ce929d0e0e4736"]


def test_arrays_that_disagree_in_length_shorten_rather_than_raise() -> None:
    """They come from one nested column and cannot disagree.

    A strict zip would turn an impossible state into a 500 — a whole trace lost
    to a defect in one link — where a shorter list loses only the link itself.
    """
    zipped = service._to_links(
        {
            "link_trace_ids": ["4bf92f3577b34da6a3ce929d0e0e4736", "89862496c280c749485ae30629e97"],
            "link_span_ids": ["00f067aa0ba902b7"],
            "link_attributes": ["{}"],
        }
    )

    assert len(zipped) == 1


# ------------------------------------------------------------ exporting the list


class TestTheExportedFile:
    """The list as a file, which is the commonest ask after search itself.

    `data-and-filter-gaps.md` records it: *"Give me this as CSV." No export
    anywhere.*
    """

    async def _sheet(self, repository: Any) -> tuple[str, bool]:
        start, end = _window()
        return await service.traces_as_csv(
            repository,
            project_id=PROJECT,
            from_time=start,
            to_time=end,
            filters=TraceFilters(),
        )

    async def test_the_header_names_the_columns_the_screen_shows(self) -> None:
        sheet, _ = await self._sheet(_repository(TENANT_A))

        assert sheet.splitlines()[0].split(",") == list(service.TRACE_COLUMNS)

    async def test_the_traces_in_the_window_reach_the_file(self) -> None:
        sheet, truncated = await self._sheet(_repository(TENANT_A))

        # Header plus the rows the fake store holds for this tenant.
        assert len(sheet.strip().splitlines()) > 1
        assert truncated is False

    async def test_another_organization_exports_nothing_of_this_one(self) -> None:
        """The same isolation the list itself is held to — an export is the same
        read, more times, and a cap is not a tenancy boundary."""
        mine, _ = await self._sheet(_repository(TENANT_A))
        theirs, _ = await self._sheet(_repository(TENANT_B))

        assert mine != theirs

    def test_the_csv_route_is_declared_before_the_trace_id_wildcard(self) -> None:
        """Otherwise `/traces/traces.csv` is matched as a trace id and answers a
        404 nobody could explain."""
        # Paths carry the router prefix, so the constant is matched by suffix.
        paths = [getattr(route, "path", "") for route in router.router.routes]
        exported = next(i for i, path in enumerate(paths) if path.endswith(router.TRACES_CSV_PATH))
        wildcard = next(i for i, path in enumerate(paths) if path.endswith("/{trace_id}"))

        assert exported < wildcard
