"""Log search: what the window bounds, what the filters narrow, what pages.

The fake store answers from the values the repository *bound*, never from the
statement text, so a query that lost its tenant predicate finds nothing and the
isolation test below fails rather than quietly passing.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexovue_api.data.clickhouse.base import TENANT_BINDING, TenantScopedRepository
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.logs import search
from nexovue_api.window import RECEIVE_CLOCK_SLACK

TENANT_A = "org-a"
TENANT_B = "org-b"
PROJECT = "project-1"

WINDOW_END = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
WINDOW_START = WINDOW_END - timedelta(hours=1)

_SEARCH_COLUMNS = [
    "observed_nanos",
    "row_key",
    "service_name",
    "severity",
    "severity_number",
    "body",
    "trace_ref",
    "span_ref",
    "attributes_json",
]

_FIRST_NANOS = 1_788_076_318_688_120_666


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class FakeLogStore:
    """Serves rows to the one tenant that owns them, and records what was bound."""

    def __init__(self, owner: str = TENANT_A, *, rows: int = 2) -> None:
        self._owner = owner
        self._rows = rows
        self.last_sql = ""
        self.last_parameters: dict[str, Any] = {}

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        self.last_sql = sql
        self.last_parameters = dict(parameters or {})

        if self.last_parameters["tenant_id"] != self._owner:
            return FakeQueryResult([], [])

        if "count()" in sql:
            return FakeQueryResult(
                ["service_name", "severity", "severity_number", "records"],
                [
                    ("demo-payments", "INFO", 9, 1878),
                    ("demo-payments", "ERROR", 17, 12),
                    ("demo-checkout", "INFO", 9, 400),
                ],
            )

        return FakeQueryResult(
            _SEARCH_COLUMNS,
            [
                (
                    _FIRST_NANOS - position,
                    1_000 + position,
                    "demo-payments",
                    "ERROR",
                    17,
                    "payment authorization failed",
                    b"aa" * 16,
                    b"bb11bb11bb11bb11",
                    '{"provider":"stripe-sandbox"}',
                )
                for position in range(self._rows)
            ],
        )


def _repository(tenant: str, store: FakeLogStore | None = None) -> TenantScopedRepository:
    return TenantScopedRepository(store or FakeLogStore(), tenant_id=tenant)  # type: ignore[arg-type]


async def _search(
    tenant: str,
    store: FakeLogStore | None = None,
    *,
    filters: search.LogFilters | None = None,
    limit: int = 10,
    cursor: str | None = None,
) -> search.LogSearchResponse:
    return await search.search_logs(
        _repository(tenant, store),
        project_id=PROJECT,
        from_time=WINDOW_START,
        to_time=WINDOW_END,
        filters=filters or search.LogFilters(),
        limit=limit,
        cursor=cursor,
    )


# ------------------------------------------------------------------- tenancy


async def test_a_search_reads_only_the_tenant_the_repository_was_built_for() -> None:
    store = FakeLogStore(owner=TENANT_A)

    mine = await _search(TENANT_A, store)
    theirs = await _search(TENANT_B, store)

    assert len(mine.logs) == 2
    assert theirs.logs == []


async def test_every_search_statement_binds_the_tenant() -> None:
    """The repository refuses an unbound statement; this proves ours is bound."""
    for filters in (
        search.LogFilters(),
        search.LogFilters(service_name="demo-payments"),
        search.LogFilters(min_severity=17),
        search.LogFilters(search="declined"),
    ):
        for with_cursor in (False, True):
            assert TENANT_BINDING in search.search_sql(filters, with_cursor=with_cursor)
    assert TENANT_BINDING in search.FACETS_SQL


# -------------------------------------------------------------------- bounding


async def test_the_read_is_bounded_on_both_clocks() -> None:
    """`logs` is partitioned on our clock and asked about on the customer's."""
    store = FakeLogStore()
    await _search(TENANT_A, store)

    bound = store.last_parameters
    assert bound["from_time"] == WINDOW_START
    assert bound["to_time"] == WINDOW_END
    assert bound["received_from"] == WINDOW_START - RECEIVE_CLOCK_SLACK
    assert bound["received_to"] == WINDOW_END + RECEIVE_CLOCK_SLACK


# --------------------------------------------------------------------- filters


async def test_a_filter_that_was_not_asked_for_is_neither_spliced_nor_bound() -> None:
    store = FakeLogStore()
    await _search(TENANT_A, store, filters=search.LogFilters(service_name="demo-payments"))

    assert search.SERVICE_CLAUSE in store.last_sql
    assert search.SEVERITY_CLAUSE not in store.last_sql
    assert search.SEARCH_CLAUSE not in store.last_sql
    assert store.last_parameters["service_name"] == "demo-payments"
    assert "min_severity" not in store.last_parameters
    assert "search" not in store.last_parameters


async def test_every_filter_that_was_asked_for_is_spliced_and_bound() -> None:
    store = FakeLogStore()
    await _search(
        TENANT_A,
        store,
        filters=search.LogFilters(service_name="demo-payments", min_severity=17, search="declined"),
    )

    assert search.SERVICE_CLAUSE in store.last_sql
    assert search.SEVERITY_CLAUSE in store.last_sql
    assert search.SEARCH_CLAUSE in store.last_sql
    assert store.last_parameters["min_severity"] == 17
    assert store.last_parameters["search"] == "declined"


# ---------------------------------------------------------------------- paging


async def test_a_short_page_is_the_last_page() -> None:
    page = await _search(TENANT_A, FakeLogStore(rows=2), limit=10)
    assert page.next_cursor is None


async def test_a_full_page_carries_a_cursor_naming_its_last_record() -> None:
    page = await _search(TENANT_A, FakeLogStore(rows=3), limit=3)

    assert page.next_cursor is not None
    decoded = search.decode_cursor(page.next_cursor)
    assert decoded.observed_nanos == _FIRST_NANOS - 2
    assert decoded.row_key == 1_002


async def test_a_cursor_narrows_the_next_page_and_binds_both_halves() -> None:
    store = FakeLogStore(rows=3)
    first = await _search(TENANT_A, store, limit=3)
    assert first.next_cursor is not None

    await _search(TENANT_A, store, limit=3, cursor=first.next_cursor)

    assert search.CURSOR_CLAUSE in store.last_sql
    assert store.last_parameters["cursor_nanos"] == _FIRST_NANOS - 2
    assert store.last_parameters["cursor_key"] == 1_002


def test_a_cursor_this_endpoint_did_not_issue_is_rejected() -> None:
    with pytest.raises(InvalidRequestError):
        search.decode_cursor("not-a-cursor")


def test_a_cursor_survives_a_round_trip() -> None:
    original = search.LogPageCursor(observed_nanos=_FIRST_NANOS, row_key=18_446_744_073_709_551_615)
    assert search.decode_cursor(original.encode()) == original


# ---------------------------------------------------------------------- facets


async def test_facets_roll_services_and_severities_up_separately() -> None:
    facets = await search.log_facets(
        _repository(TENANT_A),
        project_id=PROJECT,
        from_time=WINDOW_START,
        to_time=WINDOW_END,
    )

    # demo-payments appears on two severity rows and must be counted once.
    assert [(facet.service_name, facet.records) for facet in facets.services] == [
        ("demo-payments", 1_890),
        ("demo-checkout", 400),
    ]
    # Loudest first: a severity filter is chosen by rank, not by frequency.
    assert [(facet.severity, facet.records) for facet in facets.severities] == [
        ("ERROR", 12),
        ("INFO", 2_278),
    ]


async def test_facets_read_only_the_tenant_the_repository_was_built_for() -> None:
    facets = await search.log_facets(
        _repository(TENANT_B),
        project_id=PROJECT,
        from_time=WINDOW_START,
        to_time=WINDOW_END,
    )
    assert facets.services == []
    assert facets.severities == []


# ------------------------------------------------------------ exporting the list


class TestTheExportedFile:
    """The records as a file.

    T63 exported traces only, on a recorded finding that logs had no search
    endpoint. They did — this pages through the very function above.
    """

    async def _sheet(self, tenant: str, store: FakeLogStore | None = None) -> tuple[str, bool]:
        return await search.logs_as_csv(
            _repository(tenant, store),
            project_id=PROJECT,
            from_time=WINDOW_START,
            to_time=WINDOW_END,
            filters=search.LogFilters(),
        )

    async def test_the_header_names_the_columns_the_screen_shows(self) -> None:
        sheet, _ = await self._sheet(TENANT_A)

        assert sheet.splitlines()[0].split(",") == list(search.LOG_COLUMNS)

    async def test_the_records_in_the_window_reach_the_file(self) -> None:
        sheet, truncated = await self._sheet(TENANT_A)

        rows = sheet.strip().splitlines()
        assert len(rows) == 3  # header plus the two the fake store holds
        assert "payment authorization failed" in rows[1]
        assert truncated is False

    async def test_attributes_are_one_column_rather_than_a_column_each(self) -> None:
        """A spread would be as wide as the union of every record in the file and
        empty almost everywhere: log attributes are per-record."""
        sheet, _ = await self._sheet(TENANT_A)

        rows = list(csv.DictReader(io.StringIO(sheet)))
        assert rows[0]["attributes"] == '{"provider": "stripe-sandbox"}'

    async def test_the_file_carries_the_same_timestamps_the_api_does(self) -> None:
        """`IsoTimestamp` carries a serializer, but pydantic only applies it when
        serializing — `csv.writer` would take the raw datetime and write
        `2026-08-30 07:51:58.688120+00:00` where the API writes
        `2026-08-30T07:51:58.688Z`. One product, one timestamp."""
        sheet, _ = await self._sheet(TENANT_A)

        rows = list(csv.DictReader(io.StringIO(sheet)))
        assert rows[0]["observed_at"].endswith("Z")
        assert "T" in rows[0]["observed_at"]

    async def test_another_organization_exports_nothing_of_this_one(self) -> None:
        """The same isolation the search is held to. An export is the same read,
        more times, and a cap is not a tenancy boundary."""
        store = FakeLogStore(owner=TENANT_A)

        mine, _ = await self._sheet(TENANT_A, store)
        theirs, _ = await self._sheet(TENANT_B, store)

        assert "payment authorization failed" in mine
        assert theirs.strip() == ",".join(search.LOG_COLUMNS)

    async def test_a_file_that_stops_short_says_so(self) -> None:
        """A file that silently stops is worse than one that says where it
        stopped: the reader would take a partial window for the whole one."""
        full_page = FakeLogStore(owner=TENANT_A, rows=search.MAX_LIMIT)

        sheet, truncated = await self._sheet(TENANT_A, full_page)

        assert truncated is True
        assert len(sheet.strip().splitlines()) == search.MAX_EXPORTED_LOGS + 1
