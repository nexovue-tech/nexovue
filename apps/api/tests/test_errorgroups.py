"""Error grouping: the group key, the counting, and tenant isolation.

The fake store answers from the values the repository *bound*, never from the
statement text, so a query that lost its tenant predicate finds nothing and the
isolation tests below fail rather than quietly passing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexovue_api.data.clickhouse.base import TENANT_BINDING, TenantScopedRepository
from nexovue_api.errorgroups import queries, service
from nexovue_api.errorgroups.schemas import GROUPED_BY_SDK, GROUPED_BY_TEMPLATE
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.window import RECEIVE_CLOCK_SLACK

TENANT_A = "org-a"
TENANT_B = "org-b"
PROJECT = "project-1"

GROUP_A = "1111aaaa1111aaaa"
GROUP_B = "2222bbbb2222bbbb"

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
        self.last_parameters: dict[str, Any] = {}

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.last_parameters = bound
        owned = self._groups.get(bound["tenant_id"])
        if owned is None:
            return FakeQueryResult([], [])

        asked_for = bound.get("group_id")
        if asked_for is not None and asked_for != owned:
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
        _repository(TENANT_A), project_id=PROJECT, from_time=start, to_time=end, limit=50
    )
    for_b = await service.list_error_groups(
        _repository(TENANT_B), project_id=PROJECT, from_time=start, to_time=end, limit=50
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
        empty, project_id=PROJECT, from_time=start, to_time=end, limit=50
    )

    assert page.groups == []


def test_every_error_grouping_query_carries_the_tenant_binding() -> None:
    """The repository would refuse these, but failing here names the culprit."""
    for sql in (
        queries.error_groups_sql(one_group=False),
        queries.error_groups_sql(one_group=True),
        queries.GROUP_OCCURRENCES_SQL,
    ):
        assert TENANT_BINDING in sql


def test_both_signals_are_read_because_an_exception_arrives_either_way() -> None:
    """Span events are being deprecated in favour of log records; both persist."""
    sql = queries.error_groups_sql(one_group=False)

    assert "FROM spans" in sql
    assert "FROM logs" in sql


# ---------------------------------------------------------------------- shapes


async def test_the_group_reports_the_template_the_grouping_was_done_on() -> None:
    start, end = _window()

    page = await service.list_error_groups(
        _repository(TENANT_A), project_id=PROJECT, from_time=start, to_time=end, limit=50
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


async def test_the_query_is_bounded_on_our_receive_clock_as_well_as_the_customers() -> None:
    """A bound on `timestamp` alone prunes no partition (ADR 0067)."""
    start, end = _window()
    store = FakeErrorStore({TENANT_A: GROUP_A})

    await service.list_error_groups(
        TenantScopedRepository(store, tenant_id=TENANT_A),  # type: ignore[arg-type]
        project_id=PROJECT,
        from_time=start,
        to_time=end,
        limit=50,
    )

    assert store.last_parameters["from_time"] == start
    assert store.last_parameters["to_time"] == end
    assert store.last_parameters["received_from"] == start - RECEIVE_CLOCK_SLACK
    assert store.last_parameters["received_to"] == end + RECEIVE_CLOCK_SLACK
