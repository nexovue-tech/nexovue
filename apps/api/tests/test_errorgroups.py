"""Error grouping: the group key, the counting, and tenant isolation.

The fake store answers from the values the repository *bound*, never from the
statement text, so a query that lost its tenant predicate finds nothing and the
isolation tests below fail rather than quietly passing.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TENANT_BINDING, TenantScopedRepository
from nexovue_api.deploys.schemas import DeployEventSummary
from nexovue_api.deploys.service import DeployHistory
from nexovue_api.errorgroups import queries, service
from nexovue_api.errorgroups.schemas import GROUPED_BY_SDK, GROUPED_BY_TEMPLATE
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.window import RECEIVE_CLOCK_SLACK

TENANT_A = "org-a"
TENANT_B = "org-b"
PROJECT = "project-1"

GROUP_A = "1111aaaa1111aaaa"
GROUP_B = "2222bbbb2222bbbb"

# The services the one fake group happens in. A name outside this set reads as silent.
GROUP_SERVICES = ("checkout", "payments")
SILENT_SERVICE = "never-deployed"

TRACE_A = "aa" * 16
SPAN_A = "bb" * 8

_OCCURRED_NANOS = 1_787_825_704_123_000_000

_STACKTRACE = 'Traceback (most recent call last):\n  File "checkout.py", line 12'

# `events` is a String column, so the flat OTLP keys survive verbatim.
_SPAN_EVENTS = json.dumps(
    [
        {
            "timestamp": "2026-08-27T10:15:04Z",
            "name": "exception",
            "attributes": {
                "exception.type": "ValueError",
                "exception.message": "User <uuid> not found",
                "exception.stacktrace": _STACKTRACE,
            },
        }
    ]
)

# What ClickHouse returns for `toJSONString(log_attrs)`: the JSON type reads a
# dot as a path separator, so the flat OTLP key comes back nested.
_LOG_ATTRIBUTES = json.dumps({"exception": {"type": "ValueError", "stacktrace": _STACKTRACE}})

_SUMMARY_COLUMNS = [
    "group_id",
    "grouped_by_sdk",
    "exception_type",
    "message_template",
    "latest_message",
    "first_seen_nanos",
    "started_in_window",
    "last_seen_nanos",
    "occurrences",
    "services",
    "sample_trace_id",
    "sample_span_id",
    "sample_source",
]

_OCCURRENCE_COLUMNS = [
    "source",
    "service_name",
    "occurred_nanos",
    "trace_ref",
    "span_ref",
    "release",
    "exception_type",
    "exception_message",
    "detail_json",
]


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class FakeErrorStore:
    """Serves error rows, honouring the tenant the repository bound."""

    def __init__(self, groups: dict[str, str]) -> None:
        # tenant -> the one group id that tenant owns
        self._groups = groups
        self.last_sql = ""
        self.last_parameters: dict[str, Any] = {}

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.last_sql = sql
        self.last_parameters = bound
        owned = self._groups.get(bound["tenant_id"])
        if owned is None:
            return FakeQueryResult([], [])

        asked_for = bound.get("group_id")
        if asked_for is not None and asked_for != owned:
            return FakeQueryResult([], [])

        named = bound.get("service_name")
        if named is not None and named not in GROUP_SERVICES:
            return FakeQueryResult([], [])

        if "GROUP BY group_id" in sql:
            return FakeQueryResult(_SUMMARY_COLUMNS, [_summary_row(owned)])
        return FakeQueryResult(_OCCURRENCE_COLUMNS, _occurrence_rows())


def _summary_row(group_id: str) -> tuple[Any, ...]:
    return (
        group_id,
        0,
        "ValueError",
        "User <uuid> not found",
        "User 9c8d7e6f-aaaa-bbbb-cccc-ddddeeeeffff not found",
        _OCCURRED_NANOS,
        1,
        _OCCURRED_NANOS + 500_000_000,
        7,
        ["checkout", "payments"],
        TRACE_A.encode(),
        SPAN_A.encode(),
        "span",
    )


def _occurrence_rows() -> list[tuple[Any, ...]]:
    return [
        (
            "span",
            "checkout",
            _OCCURRED_NANOS,
            TRACE_A.encode(),
            SPAN_A.encode(),
            # A span carries its build as a promoted column.
            "2026.09.06-1",
            "ValueError",
            "User <uuid> not found",
            _SPAN_EVENTS,
        ),
        (
            "log",
            "payments",
            _OCCURRED_NANOS - 1_000,
            b"0" * 32,
            b"0" * 16,
            # A sender that stamped no version. Symbolication is impossible for
            # this one, and the screen has to be able to say so.
            "",
            "ValueError",
            "User <uuid> not found",
            _LOG_ATTRIBUTES,
        ),
    ]


def _repository(tenant: str, store: FakeErrorStore | None = None) -> TenantScopedRepository:
    store = store or FakeErrorStore({TENANT_A: GROUP_A, TENANT_B: GROUP_B})
    return TenantScopedRepository(store, tenant_id=tenant)  # type: ignore[arg-type]


def _window() -> tuple[datetime, datetime]:
    end = datetime(2026, 8, 27, 11, 0, tzinfo=UTC)
    return end - timedelta(hours=1), end


# ------------------------------------------------------------------- isolation


async def test_two_organizations_cannot_read_each_others_error_groups() -> None:
    start, end = _window()

    for_a = await service.list_error_groups(
        _repository(TENANT_A),
        project_id=PROJECT,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )
    for_b = await service.list_error_groups(
        _repository(TENANT_B),
        project_id=PROJECT,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert [group.group_id for group in for_a.groups] == [GROUP_A]
    assert [group.group_id for group in for_b.groups] == [GROUP_B]


async def test_another_tenants_group_is_indistinguishable_from_one_that_never_existed() -> None:
    start, end = _window()

    with pytest.raises(NotFoundError) as other_tenant:
        await service.get_error_group(
            _repository(TENANT_A),
            project_id=PROJECT,
            group_id=GROUP_B,
            from_time=start,
            to_time=end,
        )
    with pytest.raises(NotFoundError) as never_existed:
        await service.get_error_group(
            _repository(TENANT_A),
            project_id=PROJECT,
            group_id="ffffffffffffffff",
            from_time=start,
            to_time=end,
        )

    assert str(other_tenant.value) == str(never_existed.value)


async def test_an_organization_with_no_errors_gets_an_empty_list_and_not_a_404() -> None:
    start, end = _window()
    empty = TenantScopedRepository(FakeErrorStore({}), tenant_id="org-with-nothing")  # type: ignore[arg-type]

    page = await service.list_error_groups(
        empty,
        project_id=PROJECT,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert page.groups == []


def test_every_error_grouping_query_carries_the_tenant_binding() -> None:
    """The repository would refuse these, but failing here names the culprit."""
    for sql in (
        queries.error_groups_sql(one_group=False, one_service=False),
        queries.error_groups_sql(one_group=True, one_service=False),
        queries.GROUP_OCCURRENCES_SQL,
    ):
        assert TENANT_BINDING in sql


# -------------------------------------------------------------- narrowing to one


async def test_naming_a_service_narrows_the_statement_rather_than_the_result() -> None:
    """Filtering a capped page in Python would silently drop a quiet service's groups."""
    start, end = _window()
    store = FakeErrorStore({TENANT_A: GROUP_A})

    await service.list_error_groups(
        TenantScopedRepository(store, tenant_id=TENANT_A),  # type: ignore[arg-type]
        project_id=PROJECT,
        service_name=GROUP_SERVICES[0],
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert queries.ONE_SERVICE_CLAUSE in store.last_sql
    assert store.last_parameters["service_name"] == GROUP_SERVICES[0]


def test_the_service_filter_reaches_both_signals_and_not_only_the_spans() -> None:
    """An exception arrives as a span event or as a log record; one filter misses half."""
    narrowed = queries.error_groups_sql(one_group=False, one_service=True)

    assert narrowed.count(queries.ONE_SERVICE_CLAUSE) == 2


def test_the_service_name_is_bound_and_never_spliced_into_the_statement() -> None:
    """A name is a caller value: it narrows a read, and can never widen a tenant scope."""
    assert queries.ONE_SERVICE_CLAUSE == "AND service_name = {service_name:String}"


async def test_asking_for_every_service_binds_no_service_name() -> None:
    start, end = _window()
    store = FakeErrorStore({TENANT_A: GROUP_A})

    await service.list_error_groups(
        TenantScopedRepository(store, tenant_id=TENANT_A),  # type: ignore[arg-type]
        project_id=PROJECT,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert queries.ONE_SERVICE_CLAUSE not in store.last_sql
    assert "service_name" not in store.last_parameters


async def test_a_service_that_threw_nothing_is_an_empty_list_and_not_a_404() -> None:
    """Silent in this window is not the same fact as no such service."""
    start, end = _window()

    page = await service.list_error_groups(
        _repository(TENANT_A),
        project_id=PROJECT,
        service_name=SILENT_SERVICE,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert page.groups == []


async def test_the_group_detail_is_never_narrowed_to_one_service() -> None:
    """The other services a group happens in are the point of opening it."""
    start, end = _window()
    store = FakeErrorStore({TENANT_A: GROUP_A})

    await service.get_error_group(
        TenantScopedRepository(store, tenant_id=TENANT_A),  # type: ignore[arg-type]
        project_id=PROJECT,
        group_id=GROUP_A,
        from_time=start,
        to_time=end,
    )

    assert queries.ONE_SERVICE_CLAUSE not in queries.GROUP_OCCURRENCES_SQL
    # The marker is spliced, so a statement that still carries one was never built.
    assert queries._ONE_SERVICE_PLACEHOLDER not in queries.GROUP_OCCURRENCES_SQL
    assert "service_name" not in store.last_parameters


def test_both_signals_are_read_because_an_exception_arrives_either_way() -> None:
    """Span events are being deprecated in favour of log records; both persist."""
    sql = queries.error_groups_sql(one_group=False, one_service=False)

    assert "FROM spans" in sql
    assert "FROM logs" in sql


# ---------------------------------------------------------------------- shapes


async def test_the_group_reports_the_template_the_grouping_was_done_on() -> None:
    start, end = _window()

    page = await service.list_error_groups(
        _repository(TENANT_A),
        project_id=PROJECT,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )
    group = page.groups[0]

    assert group.exception_type == "ValueError"
    assert group.message_template == "User <uuid> not found"
    assert group.latest_message.startswith("User 9c8d7e6f")
    assert group.grouped_by == GROUPED_BY_TEMPLATE
    assert group.occurrences == 7
    assert group.services == ["checkout", "payments"]
    assert group.sample_trace_id == TRACE_A
    assert group.sample_source == "span"


async def test_a_group_the_sdk_named_says_so() -> None:
    start, end = _window()
    store = FakeErrorStore({TENANT_A: GROUP_A})

    async def sdk_summary(
        sql: str, parameters: dict[str, Any] | None = None, settings: Any = None
    ) -> FakeQueryResult:
        row = list(_summary_row(GROUP_A))
        row[_SUMMARY_COLUMNS.index("grouped_by_sdk")] = 1
        return FakeQueryResult(_SUMMARY_COLUMNS, [tuple(row)])

    store.query = sdk_summary  # type: ignore[method-assign]
    page = await service.list_error_groups(
        TenantScopedRepository(store, tenant_id=TENANT_A),  # type: ignore[arg-type]
        project_id=PROJECT,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert page.groups[0].grouped_by == GROUPED_BY_SDK


async def test_an_instance_with_no_trace_context_reports_empty_ids_not_zeroes() -> None:
    start, end = _window()

    detail = await service.get_error_group(
        _repository(TENANT_A), project_id=PROJECT, group_id=GROUP_A, from_time=start, to_time=end
    )
    from_a_log = detail.recent[1]

    assert from_a_log.source == "log"
    assert from_a_log.trace_id == ""
    assert from_a_log.span_id == ""


# ----------------------------------------------------------------- stacktraces


async def test_the_stacktrace_is_read_out_of_the_span_events_it_stayed_in() -> None:
    start, end = _window()

    detail = await service.get_error_group(
        _repository(TENANT_A), project_id=PROJECT, group_id=GROUP_A, from_time=start, to_time=end
    )

    assert detail.recent[0].stacktrace == _STACKTRACE


async def test_the_stacktrace_of_a_log_record_is_read_out_of_its_attributes() -> None:
    """ClickHouse renders the flat OTLP key as a nested path; both are read."""
    start, end = _window()

    detail = await service.get_error_group(
        _repository(TENANT_A), project_id=PROJECT, group_id=GROUP_A, from_time=start, to_time=end
    )

    assert detail.recent[1].stacktrace == _STACKTRACE


@pytest.mark.parametrize("unreadable", ["", "not json", "{}", "[]", '[{"name":"other"}]'])
def test_a_record_without_a_readable_exception_event_has_no_stacktrace(unreadable: str) -> None:
    assert (
        service._stacktrace(
            source=queries.SOURCE_SPAN,
            detail_json=unreadable,
            exception_type="ValueError",
            exception_message="boom",
        )
        == ""
    )


# --------------------------------------------------------- partition pruning


async def test_an_occurrence_carries_the_build_it_came_from() -> None:
    """A stacktrace with no release cannot be symbolicated, so the screen is told.

    The span in this fixture was deployed with a version; the log record was not,
    which is the browser SDK's default — `serviceVersion` is optional. Reporting
    the second as an empty string rather than omitting it is what lets the screen
    say "no release, so no source map" instead of silently not symbolicating.
    """
    start, end = _window()

    detail = await service.get_error_group(
        _repository(TENANT_A), project_id=PROJECT, group_id=GROUP_A, from_time=start, to_time=end
    )

    releases = [occurrence.release for occurrence in detail.recent]
    assert releases == ["2026.09.06-1", ""]


async def test_the_query_is_bounded_on_our_receive_clock_as_well_as_the_customers() -> None:
    """A bound on `timestamp` alone prunes no partition."""
    start, end = _window()
    store = FakeErrorStore({TENANT_A: GROUP_A})

    await service.list_error_groups(
        TenantScopedRepository(store, tenant_id=TENANT_A),  # type: ignore[arg-type]
        project_id=PROJECT,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert store.last_parameters["from_time"] == start
    assert store.last_parameters["to_time"] == end
    assert store.last_parameters["received_from"] == start - RECEIVE_CLOCK_SLACK
    assert store.last_parameters["received_to"] == end + RECEIVE_CLOCK_SLACK


# ------------------------------------------------- the deploy before a group


FIRST_SEEN = values.nanos_to_datetime(_OCCURRED_NANOS)


def _deploy(version: str, service_name: str, at: datetime) -> DeployEventSummary:
    return DeployEventSummary(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        version=version,
        service_name=service_name,
        deployed_at=at,
        source="webhook",
        metadata={},
    )


async def _one_group(deploys: DeployHistory) -> Any:
    start, end = _window()
    page = await service.list_error_groups(
        _repository(TENANT_A),
        project_id=PROJECT,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
        deploys=deploys,
    )
    return page.groups[0]


async def test_a_group_carries_the_last_deploy_to_ship_before_it_appeared() -> None:
    group = await _one_group(
        DeployHistory(
            [
                _deploy("1.0.0", "checkout", FIRST_SEEN - timedelta(hours=2)),
                _deploy("1.1.0", "checkout", FIRST_SEEN - timedelta(minutes=4)),
            ]
        )
    )

    assert group.preceding_deploy is not None
    assert group.preceding_deploy.version == "1.1.0"
    assert group.preceding_deploy.service_name == "checkout"
    assert group.first_seen - group.preceding_deploy.deployed_at == timedelta(minutes=4)


async def test_a_deploy_that_shipped_after_the_group_appeared_is_not_reported() -> None:
    """It cannot have come first, and saying it did would invert the claim."""
    group = await _one_group(
        DeployHistory([_deploy("1.2.0", "checkout", FIRST_SEEN + timedelta(minutes=10))])
    )

    assert group.preceding_deploy is None


async def test_a_deploy_of_a_service_this_group_never_touched_is_not_reported() -> None:
    group = await _one_group(
        DeployHistory([_deploy("9.9.9", SILENT_SERVICE, FIRST_SEEN - timedelta(minutes=1))])
    )

    assert group.preceding_deploy is None


async def test_a_project_that_records_no_deploys_reports_none_rather_than_guessing() -> None:
    """The default, and the ordinary case: most projects never post a deploy."""
    start, end = _window()
    page = await service.list_error_groups(
        _repository(TENANT_A),
        project_id=PROJECT,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert page.groups[0].preceding_deploy is None
