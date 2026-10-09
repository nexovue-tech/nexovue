"""Trace search against the running stack.

The offline suite proves the code cannot construct a cross-tenant read and that
each filter puts its own predicate in the statement. This one proves those
statements are ones ClickHouse accepts and answers — which is not a formality
here, because three of the things this endpoint does are only decidable on a
real server:

* a bare JSON path is a `Dynamic`, and ClickHouse refuses one as a `GROUP BY`
  key with `ILLEGAL_COLUMN (44)` — which is why the facets are counted over the
  promoted columns and there is no attribute facet.
* `getSubcolumn` takes its path as a bound parameter, so an arbitrary customer
  attribute can be filtered without ever entering the SQL text. A fake store
  cannot tell whether the server accepts that.
* the list is two statements over `trace_summaries`, and only the server can
  show they agree — and that what the worker wrote there matches `spans`.

It fails when the stack is not up, because a skip here is indistinguishable
from a pass. It reads only; nothing is written, so nothing is cleaned up.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.traces import queries

# `service` is a submodule, and mypy only sees it as an attribute of the package
# when it is imported in its own right — the redundant alias is what says so.
from nexovue_api.traces import service as service
from nexovue_api.traces.queries import TraceFilters
from nexovue_api.traces.schemas import TraceFacetsResponse, TraceSummary

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

TENANT_THAT_NEVER_EXISTED: Final = "00000000-0000-0000-0000-000000000000"

# The widest range the screen offers. It is also the one that used to fail:
# grouping a week of a busy project once needed 1.8 GiB and was killed.
WIDEST_WINDOW: Final = timedelta(days=7)

ONE_HOUR: Final = timedelta(hours=1)

PAGE: Final = 25

# How many pages `test_the_summary_and_the_span_grouping_name_the_same_traces` searches before
# giving up on finding a trace wholly inside its window. Both listings order newest first, so page
# one is exactly the traces most likely to still be running past the window's edge — ordinarily
# rare enough to cost nothing, but a development stack left running for hours can fill a whole page
# with nothing short enough (`busiest-hour-streaming-rpc-trace`, 2026-10-09: a reconnecting gRPC
# stream and a long k6 session did exactly that). 20 pages is 500 traces, comfortably past the
# eighth page a wider scan found a candidate in that same incident, while still failing loudly if
# a window genuinely has nothing short enough anywhere in it.
MAX_PAGES_SEARCHED: Final = 20


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or 8123), REACHABILITY_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN),
    suite="the live trace search suite",
    remedy="ClickHouse is not running — start the dev stack with `just dev`",
)


@dataclass(frozen=True, slots=True)
class BusiestProject:
    """The project holding the most spans, and two windows over it.

    Every assertion but one runs over `window_start`..`window_end`, the hour
    that failed most. The widest window is what the memory regression is about
    and costs 640 MiB a time; running sixteen of those back to back exhausts
    the dev server and proves nothing about the filters.
    """

    tenant_id: str
    project_id: str
    window_start: datetime
    window_end: datetime
    widest_start: datetime
    widest_end: datetime


@pytest_asyncio.fixture
async def clickhouse() -> AsyncIterator[AsyncClient]:
    parts = urlsplit(CLICKHOUSE_DSN)
    client = await clickhouse_connect.get_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=(parts.path or "/").lstrip("/"),
    )
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def busiest(clickhouse: AsyncClient) -> BusiestProject:
    result = await clickhouse.query(
        "SELECT tenant_id, project_id, max(timestamp) AS newest "
        "FROM spans GROUP BY tenant_id, project_id ORDER BY count() DESC LIMIT 1"
    )
    assert result.result_rows, (
        "`spans` is empty — this suite would prove nothing. Run the load "
        "generator, or check that the trace pipeline is writing spans."
    )
    tenant_id, project_id, newest = result.result_rows[0]

    # The hour that failed most, rather than the busiest or the latest. What
    # these assertions need is variety — failures beside healthy traces, across
    # several services — and the busiest hour of a load test has one service and
    # no failures in it. This is also the smallest of the three by some way,
    # which is what lets the whole suite run without exhausting the server.
    hour = await clickhouse.query(
        "SELECT toStartOfHour(timestamp) AS window FROM spans "
        "WHERE tenant_id = {tenant_id:String} AND project_id = {project_id:String} "
        "GROUP BY window HAVING countIf(status_code = 'ERROR') > 0 "
        "ORDER BY countIf(status_code = 'ERROR') DESC LIMIT 1",
        parameters={"tenant_id": str(tenant_id), "project_id": str(project_id)},
    )
    assert hour.result_rows, (
        "no hour of the busiest project holds a failed span — the status filter "
        "below would prove nothing. Run the load generator against a failing service."
    )
    busiest_hour = hour.result_rows[0][0]

    return BusiestProject(
        tenant_id=str(tenant_id),
        project_id=str(project_id),
        window_start=busiest_hour,
        window_end=busiest_hour + ONE_HOUR,
        widest_start=newest - WIDEST_WINDOW,
        widest_end=newest + timedelta(seconds=1),
    )


def _repository(client: AsyncClient, tenant_id: str) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=tenant_id)


async def _list(
    clickhouse: AsyncClient, project: BusiestProject, filters: TraceFilters, limit: int = PAGE
) -> list[str]:
    page = await service.list_traces(
        _repository(clickhouse, project.tenant_id),
        project_id=project.project_id,
        from_time=project.window_start,
        to_time=project.window_end,
        filters=filters,
        limit=limit,
        cursor=None,
    )
    return [trace.trace_id for trace in page.traces]


@pytest_asyncio.fixture
async def facets(clickhouse: AsyncClient, busiest: BusiestProject) -> TraceFacetsResponse:
    return await service.trace_facets(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
    )


# ------------------------------------------------- the widest window the UI offers


async def test_the_widest_window_the_screen_offers_is_one_the_server_can_answer(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """The regression this endpoint was rebuilt for.

    Naming a trace after its root span inside the group-by held two strings per
    trace: 2.73M traces over a week reached 1.8 GiB and the server killed the
    query, so "7 days" on the busiest project was a 500 rather than a slow page.
    The roots are now read for the page alone.
    """
    page = await service.list_traces(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.widest_start,
        to_time=busiest.widest_end,
        filters=TraceFilters(),
        limit=PAGE,
        cursor=None,
    )

    assert page.traces, "the busiest project returned no traces over a week"
    assert all(trace.root_service and trace.root_name for trace in page.traces), (
        "a trace came back unnamed — the second read did not answer for this page"
    )


async def test_a_page_is_newest_first_and_the_cursor_continues_it(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    first = await service.list_traces(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        filters=TraceFilters(),
        limit=PAGE,
        cursor=None,
    )
    starts = [trace.start_time for trace in first.traces]
    assert starts == sorted(starts, reverse=True)
    assert first.next_cursor, "a full page offered no cursor"

    second = await service.list_traces(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        filters=TraceFilters(),
        limit=PAGE,
        cursor=first.next_cursor,
    )

    assert not {trace.trace_id for trace in first.traces} & {
        trace.trace_id for trace in second.traces
    }, "the keyset repeated a trace across two pages"
    assert max(trace.start_time for trace in second.traces) <= min(starts)


# ------------------------------------------------------------------ the filters


async def test_a_service_filter_returns_traces_that_contain_that_service(
    clickhouse: AsyncClient, busiest: BusiestProject, facets: TraceFacetsResponse
) -> None:
    """And the row still describes the whole trace, not the matching spans."""
    service_name = facets.services[0].service_name

    page = await service.list_traces(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        filters=TraceFilters(service_name=service_name),
        limit=PAGE,
        cursor=None,
    )
    assert page.traces, f"no traces matched the busiest service, {service_name}"

    # Counted straight out of `spans`, so a filter that matched the wrong traces
    # — or a row describing only the matching spans — shows. No time predicate:
    # the summary describes the whole trace, and a trace that started inside the
    # window can end outside it, which is exactly where the newest page sits.
    for trace in page.traces:
        answer = await clickhouse.query(
            "SELECT countIf(service_name = {service_name:String}) AS matching, count() AS spans "
            "FROM spans WHERE tenant_id = {tenant_id:String} "
            "AND project_id = {project_id:String} AND trace_id = {trace_id:String}",
            parameters={
                "tenant_id": busiest.tenant_id,
                "project_id": busiest.project_id,
                "trace_id": trace.trace_id,
                "service_name": service_name,
            },
        )
        matching, spans = answer.result_rows[0]
        assert matching > 0, f"{trace.trace_id} contains no span from {service_name}"
        assert trace.span_count == spans, "the row counted the matching spans, not the trace"


async def test_a_status_filter_separates_the_traces_that_failed_from_the_rest(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    failed = await service.list_traces(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        filters=TraceFilters(status=queries.STATUS_ERROR),
        limit=PAGE,
        cursor=None,
    )
    healthy = await service.list_traces(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        filters=TraceFilters(status=queries.STATUS_OK),
        limit=PAGE,
        cursor=None,
    )

    assert all(trace.error_count > 0 for trace in failed.traces)
    assert all(trace.status_code == queries.STATUS_ERROR for trace in failed.traces)
    assert healthy.traces, "no trace in the window came back without a failure"
    assert all(trace.error_count == 0 for trace in healthy.traces)
    assert not {t.trace_id for t in failed.traces} & {t.trace_id for t in healthy.traces}


async def test_a_duration_floor_returns_only_traces_that_took_at_least_that_long(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    floor_ms = 50.0

    page = await service.list_traces(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        filters=TraceFilters(min_duration_ms=floor_ms),
        limit=PAGE,
        cursor=None,
    )

    assert all(trace.duration_ms >= floor_ms for trace in page.traces)


async def test_an_arbitrary_attribute_path_is_filtered_without_entering_the_sql(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """The escape hatch, on a path taken from the data rather than assumed.

    `getSubcolumn` requires its second argument to be a constant — passing a
    column raises `ILLEGAL_COLUMN (44)`. A ClickHouse server-side parameter is
    substituted before the query is analysed, so it satisfies that while still
    being a bound value. That is the whole reason an unbounded attribute set can
    be filtered without building SQL from what a caller typed.
    """
    window = {
        "tenant_id": busiest.tenant_id,
        "project_id": busiest.project_id,
        "from_time": busiest.window_start,
        "to_time": busiest.window_end,
    }
    in_the_window = (
        "FROM spans WHERE tenant_id = {tenant_id:String} "
        "AND project_id = {project_id:String} "
        "AND timestamp >= {from_time:DateTime64(9)} AND timestamp < {to_time:DateTime64(9)}"
    )

    paths = await clickhouse.query(
        f"SELECT arrayJoin(JSONAllPaths(span_attrs)) AS path {in_the_window} "
        "GROUP BY path ORDER BY count() DESC LIMIT 1",
        parameters=window,
    )
    if not paths.result_rows:
        pytest.fail("no span attribute in the window — this test would prove nothing")
    path = str(paths.result_rows[0][0])

    # The path is bound here exactly as the endpoint binds it.
    sampled = await clickhouse.query(
        f"SELECT toString(getSubcolumn(span_attrs, {{path:String}})) AS value {in_the_window} "
        "AND toString(getSubcolumn(span_attrs, {path:String})) != '' LIMIT 1",
        parameters={**window, "path": path},
    )
    if not sampled.result_rows:
        pytest.fail(f"{path} is present but empty everywhere — this test would prove nothing")
    value = str(sampled.result_rows[0][0])

    matched = await _list(
        clickhouse, busiest, TraceFilters(attribute_path=path, attribute_value=value)
    )
    unmatchable = await _list(
        clickhouse,
        busiest,
        TraceFilters(attribute_path=path, attribute_value=f"{value}-no-span-carries-this"),
    )

    assert matched, f"no trace carried {path}={value}, which was read out of the window"
    assert unmatchable == [], "a value nothing carries still matched"


async def test_a_path_nothing_ever_wrote_is_answered_rather_than_refused(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """A JSON column past its path limit spills to shared data; an unknown path
    must come back empty, not as an error the screen shows as a failure."""
    assert (
        await _list(
            clickhouse,
            busiest,
            TraceFilters(attribute_path="nexovue.no.such.attribute", attribute_value="x"),
        )
        == []
    )


@pytest.mark.parametrize("path", sorted(queries.PROMOTED_ATTRIBUTE_CLAUSES))
async def test_every_promoted_attribute_path_produces_a_statement_the_server_accepts(
    clickhouse: AsyncClient, busiest: BusiestProject, path: str
) -> None:
    """`http_status` is a UInt16 and the filter value is text; the cast has to be
    there, and only the server says whether it is."""
    await _list(clickhouse, busiest, TraceFilters(attribute_path=path, attribute_value="200"))


async def test_the_filters_narrow_together_rather_than_replacing_one_another(
    clickhouse: AsyncClient, busiest: BusiestProject, facets: TraceFacetsResponse
) -> None:
    operation = facets.operations[0]

    by_service = await _list(
        clickhouse, busiest, TraceFilters(service_name=operation.service_name), limit=PAGE
    )
    by_both = await _list(
        clickhouse,
        busiest,
        TraceFilters(service_name=operation.service_name, operation=operation.operation),
        limit=PAGE,
    )

    assert by_service, "the busiest operation's service matched nothing"
    assert by_both, "the busiest operation matched nothing"
    # Deliberately not a subset check: each page is the newest of a different
    # result set, so the narrower one can name a trace the wider page cut off.
    for trace_id in by_both:
        answer = await clickhouse.query(
            "SELECT count() FROM spans WHERE tenant_id = {tenant_id:String} "
            "AND project_id = {project_id:String} AND trace_id = {trace_id:String} "
            "AND service_name = {service_name:String} AND span_name = {operation:String}",
            parameters={
                "tenant_id": busiest.tenant_id,
                "project_id": busiest.project_id,
                "trace_id": trace_id,
                "service_name": operation.service_name,
                "operation": operation.operation,
            },
        )
        assert int(answer.result_rows[0][0]) > 0, (
            "a trace matched without one span satisfying both filters together"
        )


# ------------------------------------------------------------------- isolation


async def test_a_tenant_with_nothing_reads_nothing_however_it_filters(
    clickhouse: AsyncClient, busiest: BusiestProject, facets: TraceFacetsResponse
) -> None:
    """The same window and the same filters, under an organization that owns none
    of it. The tenant predicate is the only difference."""
    stranger = _repository(clickhouse, TENANT_THAT_NEVER_EXISTED)

    for filters in (
        TraceFilters(),
        TraceFilters(service_name=facets.services[0].service_name),
        TraceFilters(status=queries.STATUS_ERROR),
        TraceFilters(min_duration_ms=0.0),
        TraceFilters(attribute_path="http.request.method", attribute_value="GET"),
        TraceFilters(attribute_path="db.system", attribute_value="postgresql"),
    ):
        page = await service.list_traces(
            stranger,
            project_id=busiest.project_id,
            from_time=busiest.window_start,
            to_time=busiest.window_end,
            filters=filters,
            limit=PAGE,
            cursor=None,
        )
        assert page.traces == [], f"another organization's traces leaked through {filters}"


async def test_the_facets_offer_only_what_this_window_holds(
    clickhouse: AsyncClient, busiest: BusiestProject, facets: TraceFacetsResponse
) -> None:
    assert facets.services, "the busiest project reported no services"

    named = await clickhouse.query(
        "SELECT DISTINCT service_name FROM spans WHERE tenant_id = {tenant_id:String} "
        "AND project_id = {project_id:String} AND timestamp >= {from_time:DateTime64(9)} "
        "AND timestamp < {to_time:DateTime64(9)}",
        parameters={
            "tenant_id": busiest.tenant_id,
            "project_id": busiest.project_id,
            "from_time": busiest.window_start,
            "to_time": busiest.window_end,
        },
    )
    in_the_window = {str(row[0]) for row in named.result_rows}

    offered = {facet.service_name for facet in facets.services}
    assert offered <= in_the_window, "a filter was offered for a service that sent nothing"


async def test_a_stranger_is_offered_no_filters_at_all(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """The facet list is a list of another tenant's service names."""
    stranger = await service.trace_facets(
        _repository(clickhouse, TENANT_THAT_NEVER_EXISTED),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
    )

    assert stranger.services == []
    assert stranger.operations == []


# ------------------------------------------- the summary against what it indexes


async def test_the_summary_row_says_what_the_spans_of_that_trace_say(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """`trace_summaries` is derived in the worker and read here, so nothing but
    this compares the two. A drift in the derivation — a duration measured on the
    root span, a status folded the wrong way — is invisible to every other test.
    """
    page = await service.list_traces(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        filters=TraceFilters(),
        limit=PAGE,
        cursor=None,
    )
    assert page.traces, "the busiest hour returned no traces"

    for trace in page.traces:
        answer = await clickhouse.query(
            "SELECT count() AS spans, countIf(status_code = 'ERROR') AS errors, "
            "  toUnixTimestamp64Nano(min(timestamp)) AS start_nanos, "
            "  toFloat64(max(toUnixTimestamp64Nano(timestamp) "
            "    + toInt64(duration_ms * 1000000)) - min(toUnixTimestamp64Nano(timestamp))) "
            "    / 1000000 AS duration_ms "
            "FROM spans WHERE tenant_id = {tenant_id:String} "
            "AND project_id = {project_id:String} AND trace_id = {trace_id:String}",
            parameters={
                "tenant_id": busiest.tenant_id,
                "project_id": busiest.project_id,
                "trace_id": trace.trace_id,
            },
        )
        spans, errors, start_nanos, duration_ms = answer.result_rows[0]
        assert trace.span_count == spans, f"{trace.trace_id} counts {trace.span_count}, not {spans}"
        assert trace.error_count == errors
        assert values.nanos_to_datetime(start_nanos) == trace.start_time
        # Earliest start to latest end, not the root span's own duration: a root
        # that returns before its children finish would be called fast.
        assert trace.duration_ms == pytest.approx(duration_ms, abs=0.001)


async def test_the_list_never_reads_spans_unless_an_attribute_was_asked_for(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """The regression this was built for, as a property of the statements rather
    than of a timing. Grouping a week of `spans` cost 292 MiB and 393 ms here,
    and 1.8 GiB before the strings left the group, which the server killed."""
    for filters in (
        TraceFilters(),
        TraceFilters(status=queries.STATUS_ERROR),
        TraceFilters(min_duration_ms=1.0),
    ):
        assert "FROM spans" not in queries.trace_page_sql(filters, with_cursor=False)
    assert "FROM spans" not in queries.TRACE_PAGE_VALUES_SQL
    # And the fallback still exists, for the one filter no per-trace row can hold.
    assert "FROM spans" in queries.list_traces_sql(
        TraceFilters(attribute_path="db.system", attribute_value="postgresql"), with_cursor=False
    )


async def test_the_summary_and_the_span_grouping_name_the_same_traces(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """Both paths are live — one for the attribute filter, one for everything
    else — so they have to agree. They are compared over the traces that fall
    wholly inside the window, because the two answer subtly different questions
    at its edges: the summary keys on when a trace *started*, the grouping on
    whether a span of it lies inside.
    """
    inner_start = busiest.window_start + timedelta(minutes=5)
    inner_end = busiest.window_end - timedelta(minutes=5)

    # The docstring above said the comparison was over traces wholly inside the
    # window and the code never did it — it narrowed the window by five minutes
    # and then compared everything shared, which is not the same thing. A trace
    # longer than that slack still straddles the inner edge, and the demo has
    # one: a seven-minute k6 checkout. Observed 2026-09-07, trace `024c5ad0…`
    # with 70 spans in its summary and 3 in the grouping, naming different roots.
    # Neither read is wrong; they answer different questions at an edge, so the
    # comparison has to stay away from edges. This is T3-7.
    def _utc(moment: datetime) -> datetime:
        """Both sides in one zone. The window and the summary do not agree on
        whether a timestamp carries an offset, and comparing them raw raises."""
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)

    def _spans_the_whole_trace(summary: TraceSummary) -> bool:
        started = _utc(summary.start_time)
        return started >= _utc(inner_start) and started + timedelta(
            milliseconds=summary.duration_ms
        ) <= _utc(inner_end)

    # Both listings order newest first (`queries.py`), so page one is exactly
    # the traces most likely to still be running past the window's edge — rare
    # enough on an ordinary run to cost nothing, but not rare on a development
    # stack left running for hours: a reconnecting gRPC stream and a long k6
    # session filled a whole page with nothing short enough to qualify
    # (`busiest-hour-streaming-rpc-trace`, 2026-10-09). Paging past a page with
    # nothing wholly inside, rather than failing on page one, is what survives
    # that — a wider scan during that incident found good candidates well
    # within the first ten pages.
    repository = _repository(clickhouse, busiest.tenant_id)
    summarised: dict[str, TraceSummary] = {}
    grouped: dict[str, TraceSummary] = {}
    summary_cursor: str | None = None
    spans_cursor: str | None = None
    summary_exhausted = spans_exhausted = False
    wholly_inside: set[str] = set()

    for _ in range(MAX_PAGES_SEARCHED):
        if not summary_exhausted:
            from_summary = await service.list_traces(
                repository,
                project_id=busiest.project_id,
                from_time=inner_start,
                to_time=inner_end,
                filters=TraceFilters(),
                limit=PAGE,
                cursor=summary_cursor,
            )
            summarised.update({trace.trace_id: trace for trace in from_summary.traces})
            summary_cursor = from_summary.next_cursor
            summary_exhausted = summary_cursor is None

        if not spans_exhausted:
            from_spans = await service._list_by_grouping_spans(
                repository,
                project_id=busiest.project_id,
                from_time=inner_start,
                to_time=inner_end,
                filters=TraceFilters(),
                limit=PAGE,
                cursor=spans_cursor,
            )
            grouped.update({trace.trace_id: trace for trace in from_spans.traces})
            spans_cursor = from_spans.next_cursor
            spans_exhausted = spans_cursor is None

        shared = summarised.keys() & grouped.keys()
        wholly_inside = {
            trace_id for trace_id in shared if _spans_the_whole_trace(summarised[trace_id])
        }
        if wholly_inside or (summary_exhausted and spans_exhausted):
            break

    assert summarised, "the summary answered nothing for a window that holds traces"
    shared = summarised.keys() & grouped.keys()
    assert shared, "the two paths named no trace in common"
    # Without this the suite could pass having compared nothing, which is the
    # failure mode the filter itself introduces.
    assert wholly_inside, (
        f"searched {len(summarised)} summarised / {len(grouped)} grouped traces and none of the "
        f"{len(shared)} shared ones lies wholly inside "
        f"{inner_start.isoformat()}..{inner_end.isoformat()}, so this compared nothing"
    )

    for trace_id in wholly_inside:
        assert summarised[trace_id].span_count == grouped[trace_id].span_count
        assert summarised[trace_id].error_count == grouped[trace_id].error_count
        assert summarised[trace_id].status_code == grouped[trace_id].status_code
        assert summarised[trace_id].root_service == grouped[trace_id].root_service
        assert summarised[trace_id].root_name == grouped[trace_id].root_name


async def test_a_service_and_an_operation_together_match_one_span_not_two(
    clickhouse: AsyncClient, busiest: BusiestProject, facets: TraceFacetsResponse
) -> None:
    """The pair is stored joined for this reason. A service from one span and an
    operation from another is three unrelated spans reported as one trace."""
    operation = facets.operations[0]
    other = next(
        (
            facet
            for facet in facets.operations
            if facet.service_name != operation.service_name
            and facet.operation != operation.operation
        ),
        None,
    )
    if other is None:
        pytest.fail("the window holds one service only — this test would prove nothing")

    crossed = await _list(
        clickhouse,
        busiest,
        TraceFilters(service_name=operation.service_name, operation=other.operation),
    )
    for trace_id in crossed:
        answer = await clickhouse.query(
            "SELECT count() FROM spans WHERE tenant_id = {tenant_id:String} "
            "AND project_id = {project_id:String} AND trace_id = {trace_id:String} "
            "AND service_name = {service_name:String} AND span_name = {operation:String}",
            parameters={
                "tenant_id": busiest.tenant_id,
                "project_id": busiest.project_id,
                "trace_id": trace_id,
                "service_name": operation.service_name,
                "operation": other.operation,
            },
        )
        assert int(answer.result_rows[0][0]) > 0, (
            "a trace matched with the service on one span and the operation on another"
        )


async def test_a_real_gen_ai_span_is_marked_a_model_call(clickhouse: AsyncClient) -> None:
    """F29: against a real, stored span — not a fake row standing in for one.

    Reads whichever `gateseed`-seeded gen_ai trace is found, rather than one
    `tools/gateseed` happens to produce first: the rule being proved is
    "a real stored attribute reaches `is_model_call`", not which fixture does.
    """
    found = await clickhouse.query(
        "SELECT tenant_id, project_id, trace_id FROM spans "
        "WHERE service_name = {service:String} "
        "AND toString(getSubcolumn(span_attrs, {attr:String})) != '' "
        # Newest, not merely any — an older run from before a worker restart
        # is still in the store and is not what this suite is meant to prove.
        "ORDER BY timestamp DESC LIMIT 1",
        parameters={"service": "gateseed", "attr": "gen_ai.provider.name"},
    )
    assert found.result_rows, (
        "no gen_ai span from service_name='gateseed' — run `go run ./tools/gateseed` "
        "against the dev stack first"
    )
    tenant_id, project_id, trace_id = (values.text(value) for value in found.result_rows[0])

    detail = await service.get_trace(
        TenantScopedRepository(clickhouse, tenant_id=tenant_id),
        project_id=project_id,
        trace_id=trace_id,
    )

    assert any(span.is_model_call for span in detail.spans), (
        "a span known to carry a gen_ai.* attribute was not marked a model call"
    )
