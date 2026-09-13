"""The sparkline behind each service's row (P1-40).

The route manifest promised this screen showed a "p95 trend" and the API had no
series in it — one aggregate per window, so the front door showed a number with
no way to tell whether it was normal. These are the tests for the endpoint that
closes that.

The bucketing is the part worth testing. `endpoint_stats_1m` stores a row per
minute: an hour is 60 points and a week is over 10,000, and a sparkline drawn
from 10,000 points is a smear that costs a large response to produce.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest

from nexovue_api import rollup
from nexovue_api.data.clickhouse.base import TenantScopedRepository, binds_tenant
from nexovue_api.services import queries, service

TENANT_A: Final = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
TENANT_B: Final = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e99"
PROJECT: Final = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"

WINDOW_END: Final = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
WINDOW_START: Final = WINDOW_END - timedelta(hours=1)


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class CapturingClient:
    """Answers only for the tenant that was bound.

    Two tenants each own one service, so a statement whose tenant binding went
    missing answers with nothing and the isolation test fails rather than
    passing by luck.
    """

    COLUMNS: Final = ["service_name", "bucket", "request_count", "error_count", "latency_ms"]

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.parameters: list[dict[str, Any]] = []
        self._owned = {TENANT_A: "storefront", TENANT_B: "payments"}

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.statements.append(sql)
        self.parameters.append(bound)

        owned = self._owned.get(bound["tenant_id"])
        if owned is None or bound.get("project_id") != PROJECT:
            return FakeQueryResult([], [])
        if (named := bound.get("service_name")) is not None and named != owned:
            return FakeQueryResult([], [])

        # Two buckets, so a per-bucket rate can be told apart from a whole-window one.
        return FakeQueryResult(
            self.COLUMNS,
            [
                (owned, WINDOW_START, 300, 0, [10.0, 50.0, 90.0]),
                (owned, WINDOW_START + timedelta(minutes=5), 600, 60, [11.0, 120.0, 200.0]),
            ],
        )


@pytest.fixture
def client() -> CapturingClient:
    return CapturingClient()


def _repository(client: CapturingClient, tenant: str) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=tenant)  # type: ignore[arg-type]


# --------------------------------------------------------------------- statement


def test_the_trend_statement_filters_on_the_tenant() -> None:
    """The repository would refuse it, but failing here names the culprit."""
    assert binds_tenant(queries.trend_sql(one_service=False))
    assert binds_tenant(queries.trend_sql(one_service=True))


def test_naming_one_service_adds_a_predicate_and_nothing_else() -> None:
    narrowed = queries.trend_sql(one_service=True)
    broad = queries.trend_sql(one_service=False)

    assert queries.ONE_SERVICE_CLAUSE in narrowed
    assert queries.ONE_SERVICE_CLAUSE not in broad


# ---------------------------------------------------------------------- bucketing


@pytest.mark.parametrize(
    "window,expected_seconds",
    [
        (timedelta(minutes=30), 60),  # already fewer points than the target
        (timedelta(hours=1), 60),  # exactly the target, unbucketed
        (timedelta(hours=6), 360),
        (timedelta(days=1), 1_440),
        (timedelta(days=7), 10_080),
    ],
)
def test_the_bucket_grows_with_the_window(window: timedelta, expected_seconds: int) -> None:
    end = WINDOW_END
    assert rollup.trend_bucket_seconds(end - window, end) == expected_seconds


@pytest.mark.parametrize(
    "window", [timedelta(minutes=30), timedelta(hours=6), timedelta(days=7), timedelta(days=30)]
)
def test_no_window_produces_more_points_than_a_sparkline_can_show(window: timedelta) -> None:
    """The reason bucketing exists. A month of minutes is 43,200 points."""
    bucket = rollup.trend_bucket_seconds(WINDOW_END - window, WINDOW_END)
    points = window.total_seconds() / bucket

    assert points <= rollup.TREND_POINTS + 1


def test_the_bucket_is_never_finer_than_the_table_stores() -> None:
    """A sub-minute bucket would return the same minutes under another name."""
    assert rollup.trend_bucket_seconds(WINDOW_END, WINDOW_END) == rollup.MIN_BUCKET_SECONDS


# ------------------------------------------------------------------------- shaping


async def test_a_rate_is_against_its_own_bucket_not_the_window(
    client: CapturingClient,
) -> None:
    """Otherwise a quiet minute inside a busy hour reads as the hour's average
    and the line is flat — which is the one thing a trend must not be."""
    trend = await service.service_trend(
        _repository(client, TENANT_A),
        project_id=PROJECT,
        service_name=None,
        from_time=WINDOW_START,
        to_time=WINDOW_END,
    )

    points = trend.services[0].points
    # 60s buckets over an hour: 300 requests in one minute is 300/min.
    assert points[0].requests_per_minute == pytest.approx(300.0)
    assert points[1].requests_per_minute == pytest.approx(600.0)


async def test_each_point_carries_the_same_three_numbers_as_the_row(
    client: CapturingClient,
) -> None:
    trend = await service.service_trend(
        _repository(client, TENANT_A),
        project_id=PROJECT,
        service_name=None,
        from_time=WINDOW_START,
        to_time=WINDOW_END,
    )

    second = trend.services[0].points[1]
    assert second.p95_ms == pytest.approx(120.0)
    assert second.error_rate == pytest.approx(0.1)  # 60 of 600


async def test_the_bucket_size_is_returned_so_a_client_can_scale_its_axis(
    client: CapturingClient,
) -> None:
    """A client that assumed a minute would silently mis-scale any window
    wider than an hour."""
    trend = await service.service_trend(
        _repository(client, TENANT_A),
        project_id=PROJECT,
        service_name=None,
        from_time=WINDOW_END - timedelta(days=1),
        to_time=WINDOW_END,
    )

    assert trend.bucket_seconds == 1_440


# ----------------------------------------------------------------------- tenancy


async def test_one_tenants_trend_is_not_another_tenants(client: CapturingClient) -> None:
    a = await service.service_trend(
        _repository(client, TENANT_A),
        project_id=PROJECT,
        service_name=None,
        from_time=WINDOW_START,
        to_time=WINDOW_END,
    )
    b = await service.service_trend(
        _repository(client, TENANT_B),
        project_id=PROJECT,
        service_name=None,
        from_time=WINDOW_START,
        to_time=WINDOW_END,
    )

    assert [s.service_name for s in a.services] == ["storefront"]
    assert [s.service_name for s in b.services] == ["payments"]


async def test_a_service_name_from_the_caller_cannot_reach_another_tenants_rows(
    client: CapturingClient,
) -> None:
    """The name is a value the caller chose; the tenant is not."""
    trend = await service.service_trend(
        _repository(client, TENANT_A),
        project_id=PROJECT,
        service_name="payments",
        from_time=WINDOW_START,
        to_time=WINDOW_END,
    )

    assert trend.services == []
