"""The web vitals read against real ClickHouse.

The offline suites prove the service reads a p75 out of the array position the
rollup declared. They cannot prove that `quantilesMerge` accepts the levels the
query names, that the tenant filter reaches the statement, or that a state the
Go writer folded is one this query can merge — and those are the three ways the
number on the screen becomes silently wrong rather than absent.

Rows are written under tenant ids this suite owns and deleted on the way out;
ClickHouse has no transaction to roll back.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.rum import service
from nexovue_api.rum.schemas import BreakdownDimension, WebVitalBreakdownResponse

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

ROLLUP_TABLE: Final = "web_vitals_1m"

# The levels migration 000031 declared. Named here so a test that stopped
# matching the migration fails as a mismatch rather than as a wrong number.
QUANTILE_LEVELS: Final = "0.5, 0.75, 0.95"


def _parts() -> tuple[str, int, str, str, str]:
    url = urlsplit(CLICKHOUSE_DSN)
    return (
        url.hostname or "localhost",
        url.port or 8123,
        url.username or "",
        url.password or "",
        (url.path or "/").lstrip("/"),
    )


def _reachable() -> bool:
    host, port, *_ = _parts()
    try:
        with socket.create_connection((host, port), REACHABILITY_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_rum_live.py",
    remedy=f"ClickHouse is not answering on {CLICKHOUSE_DSN} — start it with `just dev`",
)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    host, port, username, password, database = _parts()
    connection = await clickhouse_connect.get_async_client(
        host=host, port=port, username=username, password=password, database=database
    )
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def tenant(client: AsyncClient) -> AsyncIterator[str]:
    """A tenant id nothing else writes to, emptied on the way out."""
    tenant_id = f"test-rum-{uuid.uuid4()}"
    try:
        yield tenant_id
    finally:
        await client.command(
            f"ALTER TABLE {ROLLUP_TABLE} DELETE WHERE tenant_id = %(tenant)s",
            parameters={"tenant": tenant_id},
            settings={"mutations_sync": "2"},
        )


async def _write_readings(
    client: AsyncClient,
    *,
    tenant_id: str,
    project_id: str,
    vital: str,
    values: list[float],
    minute: datetime,
    url_path: str = "/checkout",
    route: str = "",
    device_class: str = "mobile",
) -> None:
    """One rollup row, folded into a quantile state by the server.

    The same `arrayReduce` the Go writer uses, so what this suite reads back is a
    state built the way production builds it rather than one shaped for a test.

    `route` defaults to empty, which is the customer who configured none.
    """
    await client.command(
        f"""
        INSERT INTO {ROLLUP_TABLE}
            (tenant_id, derived_retention_days, project_id, vital_name, route,
             url_path, device_class, country, minute, reading_count, value_quantiles)
        SELECT %(tenant)s, 395, %(project)s, %(vital)s, %(route)s, %(path)s,
               %(device)s, '', %(minute)s, %(count)s,
               arrayReduce('quantilesState({QUANTILE_LEVELS})', %(values)s)
        """,
        parameters={
            "tenant": tenant_id,
            "project": project_id,
            "vital": vital,
            "route": route,
            "path": url_path,
            "device": device_class,
            "minute": minute,
            "count": len(values),
            "values": values,
        },
    )


async def _write_one_reading_per_page(
    client: AsyncClient,
    *,
    tenant_id: str,
    project_id: str,
    minute: datetime,
    pages: int,
    route: str = "",
) -> None:
    """`pages` distinct paths, one reading each, in a single statement.

    This is the shape that makes the group limit matter: with no `route` a path
    carrying an order id is a new group per order. Pass one and the same paths
    collapse to a single group, which is what the option is for.
    """
    await client.command(
        f"""
        INSERT INTO {ROLLUP_TABLE}
            (tenant_id, derived_retention_days, project_id, vital_name, route,
             url_path, device_class, country, minute, reading_count, value_quantiles)
        SELECT %(tenant)s, 395, %(project)s, 'lcp', %(route)s,
               concat('/order/', toString(number)),
               'desktop', '', %(minute)s, 1,
               arrayReduce('quantilesState({QUANTILE_LEVELS})', [100.0])
        FROM numbers(%(pages)s)
        """,
        parameters={
            "tenant": tenant_id,
            "project": project_id,
            "route": route,
            "minute": minute,
            "pages": pages,
        },
    )


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    return now - timedelta(hours=1), now + timedelta(minutes=1)


async def test_the_p75_read_back_is_the_percentile_of_what_was_written(
    client: AsyncClient, tenant: str
) -> None:
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    # 100, 200, 300, 400 → p75 is 325 by ClickHouse's interpolating quantiles.
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[100.0, 200.0, 300.0, 400.0],
        minute=minute,
    )

    start, end = _window()
    answer = await service.summarize_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )

    assert len(answer.vitals) == 1
    summary = answer.vitals[0]
    assert summary.name == "lcp"
    assert summary.readings == 4
    assert summary.p75 == pytest.approx(325.0)


async def test_minutes_merge_rather_than_averaging_their_percentiles(
    client: AsyncClient, tenant: str
) -> None:
    """Two minutes of one page, read as one distribution.

    This is what the rollup exists for. Averaging the two minutes' p75s would give
    a different, wrong number, so the merge has to happen in the store.
    """
    project_id = str(uuid.uuid4())
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[100.0, 100.0, 100.0, 100.0],
        minute=now - timedelta(minutes=2),
    )
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[900.0, 900.0, 900.0, 900.0],
        minute=now,
    )

    start, end = _window()
    answer = await service.summarize_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )

    summary = answer.vitals[0]
    assert summary.readings == 8
    # The p75 of eight readings, four at 100 and four at 900, is 900 — not the
    # 500 an average of the two minutes' percentiles would produce.
    assert summary.p75 == pytest.approx(900.0)


async def test_every_vital_comes_back_separately_and_ordered(
    client: AsyncClient, tenant: str
) -> None:
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    for vital, value in (("lcp", 2400.0), ("inp", 180.0), ("cls", 0.08)):
        await _write_readings(
            client,
            tenant_id=tenant,
            project_id=project_id,
            vital=vital,
            values=[value] * 4,
            minute=minute,
        )

    start, end = _window()
    answer = await service.summarize_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )

    assert [summary.name for summary in answer.vitals] == ["cls", "inp", "lcp"]
    # CLS is a ratio, so a column that rounded it to an integer would read zero.
    cls = next(summary for summary in answer.vitals if summary.name == "cls")
    assert cls.p75 == pytest.approx(0.08)


async def test_another_tenants_readings_are_not_visible(client: AsyncClient, tenant: str) -> None:
    """Tenant isolation at the repository layer, which is a Phase 1 invariant."""
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[100.0, 200.0, 300.0, 400.0],
        minute=minute,
    )

    start, end = _window()
    # The same project id, read as somebody else. The project is not the boundary.
    answer = await service.summarize_web_vitals(
        TenantScopedRepository(client, tenant_id=f"test-rum-other-{uuid.uuid4()}"),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )
    assert answer.vitals == []


async def test_a_window_with_no_readings_is_an_empty_list_and_not_an_error(
    client: AsyncClient, tenant: str
) -> None:
    answer = await service.summarize_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        project_id=str(uuid.uuid4()),
        from_time=datetime.now(UTC) - timedelta(hours=2),
        to_time=datetime.now(UTC) - timedelta(hours=1),
    )
    assert answer.vitals == []


# ------------------------------------------------------- the breakdowns, P1-47
#
# `route`, `url_path` and `device_class` are promoted columns on `web_vitals` and
# are in `web_vitals_1m`'s sort key, so these are a `GROUP BY` over the same
# rollup the headline reads — not a raw-row scan and not a migration.
#
# A page is `route` where the customer configured one and `url_path` where they
# did not, so the tests below cover both and the mixture.


async def _break_down(
    client: AsyncClient, tenant: str, *, dimension: BreakdownDimension, project_id: str
) -> WebVitalBreakdownResponse:
    start, end = _window()
    return await service.break_down_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        dimension=dimension,
        project_id=project_id,
        from_time=start,
        to_time=end,
    )


async def test_each_page_gets_its_own_percentile_rather_than_the_sites(
    client: AsyncClient, tenant: str
) -> None:
    """The point of the breakdown: a slow page cannot hide inside a fast site."""
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[100.0] * 4,
        minute=minute,
        url_path="/",
    )
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[9000.0] * 4,
        minute=minute,
        url_path="/checkout",
    )

    answer = await _break_down(
        client, tenant, dimension=BreakdownDimension.PAGE, project_id=project_id
    )

    by_page = {group.key: group for group in answer.groups}
    assert set(by_page) == {"/", "/checkout"}
    assert by_page["/"].vitals[0].p75 == pytest.approx(100.0)
    assert by_page["/checkout"].vitals[0].p75 == pytest.approx(9000.0)
    # The unsplit read over the same rows merges all eight into one distribution.
    assert by_page["/"].vitals[0].readings == 4


async def test_the_same_page_splits_by_device_class(client: AsyncClient, tenant: str) -> None:
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    for device, value in (("desktop", 400.0), ("mobile", 3200.0)):
        await _write_readings(
            client,
            tenant_id=tenant,
            project_id=project_id,
            vital="lcp",
            values=[value] * 4,
            minute=minute,
            url_path="/",
            device_class=device,
        )

    answer = await _break_down(
        client, tenant, dimension=BreakdownDimension.DEVICE, project_id=project_id
    )

    by_device = {group.key: group.vitals[0].p75 for group in answer.groups}
    assert by_device["desktop"] == pytest.approx(400.0)
    assert by_device["mobile"] == pytest.approx(3200.0)


async def test_a_group_carries_every_vital_it_measured_ordered_by_name(
    client: AsyncClient, tenant: str
) -> None:
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    for vital, value in (("lcp", 2400.0), ("cls", 0.08), ("inp", 180.0)):
        await _write_readings(
            client,
            tenant_id=tenant,
            project_id=project_id,
            vital=vital,
            values=[value] * 4,
            minute=minute,
            url_path="/",
        )

    answer = await _break_down(
        client, tenant, dimension=BreakdownDimension.PAGE, project_id=project_id
    )

    assert len(answer.groups) == 1
    assert [vital.name for vital in answer.groups[0].vitals] == ["cls", "inp", "lcp"]
    # CLS is a ratio; a group that rounded it would report a perfect page.
    assert answer.groups[0].vitals[0].p75 == pytest.approx(0.08)


async def test_the_busiest_pages_come_first_so_the_limit_cuts_the_tail(
    client: AsyncClient, tenant: str
) -> None:
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[100.0],
        minute=minute,
        url_path="/rare",
    )
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[100.0] * 20,
        minute=minute,
        url_path="/busy",
    )

    answer = await _break_down(
        client, tenant, dimension=BreakdownDimension.PAGE, project_id=project_id
    )

    assert [group.key for group in answer.groups] == ["/busy", "/rare"]


async def test_a_page_with_too_few_readings_is_returned_with_its_count(
    client: AsyncClient, tenant: str
) -> None:
    """The threshold is the screen's, not the query's.

    A page visited three times is a fact; whether its p75 may be shown as a
    number is a presentation rule, and it is written down once — in
    `apps/web/src/features/rum/vitals.ts`. Filtering here would be a second copy
    of it and would also hide that the page was visited at all.
    """
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[9000.0, 9000.0, 9000.0],
        minute=minute,
        url_path="/barely-visited",
    )

    answer = await _break_down(
        client, tenant, dimension=BreakdownDimension.PAGE, project_id=project_id
    )

    assert [group.key for group in answer.groups] == ["/barely-visited"]
    assert answer.groups[0].vitals[0].readings == 3


async def test_more_pages_than_the_limit_are_cut_and_said_to_be_cut(
    client: AsyncClient, tenant: str
) -> None:
    """`url_path` is stored as sent, so an id in the path is a group per order."""
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_one_reading_per_page(
        client,
        tenant_id=tenant,
        project_id=project_id,
        minute=minute,
        pages=service.MAX_GROUPS + 5,
    )

    answer = await _break_down(
        client, tenant, dimension=BreakdownDimension.PAGE, project_id=project_id
    )

    assert len(answer.groups) == service.MAX_GROUPS
    assert answer.truncated


async def test_exactly_the_limit_is_not_reported_as_cut(client: AsyncClient, tenant: str) -> None:
    """The off-by-one that would tell every full page it was truncated."""
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_one_reading_per_page(
        client,
        tenant_id=tenant,
        project_id=project_id,
        minute=minute,
        pages=service.MAX_GROUPS,
    )

    answer = await _break_down(
        client, tenant, dimension=BreakdownDimension.PAGE, project_id=project_id
    )

    assert len(answer.groups) == service.MAX_GROUPS
    assert not answer.truncated


async def test_another_tenants_pages_are_not_in_the_breakdown(
    client: AsyncClient, tenant: str
) -> None:
    """The breakdown goes through the same chokepoint as everything else."""
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[100.0] * 4,
        minute=minute,
        url_path="/private",
    )

    answer = await _break_down(
        client,
        f"test-rum-other-{uuid.uuid4()}",
        dimension=BreakdownDimension.PAGE,
        project_id=project_id,
    )
    assert answer.groups == []


async def test_a_window_with_no_readings_breaks_down_to_nothing(
    client: AsyncClient, tenant: str
) -> None:
    start = datetime.now(UTC) - timedelta(hours=2)
    answer = await service.break_down_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        dimension=BreakdownDimension.DEVICE,
        project_id=str(uuid.uuid4()),
        from_time=start,
        to_time=start + timedelta(hours=1),
    )
    assert answer.groups == []
    assert not answer.truncated


# ------------------------------------------------------ grouping pages by route
#
# The defect: our own dev store held `/cart/checkout/978ac453-aa9f-11f1-91c3-…`,
# so a real shop produced one breakdown group per order. The customer already
# tells us the answer through the SDK's `route` option; nothing carried it.


async def test_one_route_template_collapses_every_url_beneath_it(
    client: AsyncClient, tenant: str
) -> None:
    """Three orders, one page — and one p75 over all three, not three p75s.

    The merge has to happen in the store: a page's percentile cannot be recovered
    from three per-URL percentiles.
    """
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    template = "/cart/checkout/{orderId}"
    for order, value in (("978ac453", 100.0), ("aa9f11f1", 200.0), ("91c31e31", 300.0)):
        await _write_readings(
            client,
            tenant_id=tenant,
            project_id=project_id,
            vital="lcp",
            values=[value],
            minute=minute,
            url_path=f"/cart/checkout/{order}",
            route=template,
        )

    answer = await _break_down(
        client, tenant, dimension=BreakdownDimension.PAGE, project_id=project_id
    )

    assert [group.key for group in answer.groups] == [template]
    group = answer.groups[0]
    assert group.is_route_template
    assert group.vitals[0].readings == 3
    # The p75 of 100, 200 and 300 together. Any per-URL percentile would be one
    # of those three values.
    assert group.vitals[0].p75 == pytest.approx(250.0)


async def test_a_page_with_no_route_is_still_grouped_by_its_path(
    client: AsyncClient, tenant: str
) -> None:
    """The fallback. A customer who configured nothing still gets a breakdown."""
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[900.0] * 4,
        minute=minute,
        url_path="/about",
    )

    answer = await _break_down(
        client, tenant, dimension=BreakdownDimension.PAGE, project_id=project_id
    )

    assert [group.key for group in answer.groups] == ["/about"]
    assert not answer.groups[0].is_route_template
    assert answer.groups[0].vitals[0].p75 == pytest.approx(900.0)


async def test_routed_and_unrouted_pages_do_not_collide(client: AsyncClient, tenant: str) -> None:
    """The mixture, which is what a real customer part-way through looks like.

    Both appear, each with its own distribution, and each says which kind of key
    it is — a template means "this page", a path means "this one URL".
    """
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    template = "/cart/checkout/{orderId}"
    for order, value in (("978ac453", 100.0), ("aa9f11f1", 300.0)):
        await _write_readings(
            client,
            tenant_id=tenant,
            project_id=project_id,
            vital="lcp",
            values=[value],
            minute=minute,
            url_path=f"/cart/checkout/{order}",
            route=template,
        )
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[900.0] * 4,
        minute=minute,
        url_path="/about",
    )

    answer = await _break_down(
        client, tenant, dimension=BreakdownDimension.PAGE, project_id=project_id
    )

    by_page = {group.key: group for group in answer.groups}
    assert set(by_page) == {template, "/about"}
    assert by_page[template].is_route_template
    assert not by_page["/about"].is_route_template
    assert by_page[template].vitals[0].readings == 2
    assert by_page[template].vitals[0].p75 == pytest.approx(250.0)
    assert by_page["/about"].vitals[0].readings == 4
    assert by_page["/about"].vitals[0].p75 == pytest.approx(900.0)


async def test_a_route_template_keeps_the_group_count_flat_as_orders_arrive(
    client: AsyncClient, tenant: str
) -> None:
    """The whole point, stated as the number a customer would notice.

    Sixty order URLs with no template overflow the group limit and the breakdown
    says it was cut. The same sixty under one template are one row.
    """
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_one_reading_per_page(
        client,
        tenant_id=tenant,
        project_id=project_id,
        minute=minute,
        pages=service.MAX_GROUPS + 10,
    )
    unrouted = await _break_down(
        client, tenant, dimension=BreakdownDimension.PAGE, project_id=project_id
    )
    assert len(unrouted.groups) == service.MAX_GROUPS
    assert unrouted.truncated

    routed_project = str(uuid.uuid4())
    template = "/order/{id}"
    await _write_one_reading_per_page(
        client,
        tenant_id=tenant,
        project_id=routed_project,
        minute=minute,
        pages=service.MAX_GROUPS + 10,
        route=template,
    )
    routed = await _break_down(
        client, tenant, dimension=BreakdownDimension.PAGE, project_id=routed_project
    )
    assert [group.key for group in routed.groups] == [template]
    assert not routed.truncated
    assert routed.groups[0].vitals[0].readings == service.MAX_GROUPS + 10


async def test_a_device_breakdown_never_claims_a_route_template(
    client: AsyncClient, tenant: str
) -> None:
    """A device class is a device class. The flag exists so one reader serves both."""
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[400.0] * 4,
        minute=minute,
        url_path="/cart/checkout/1",
        route="/cart/checkout/{orderId}",
        device_class="desktop",
    )

    answer = await _break_down(
        client, tenant, dimension=BreakdownDimension.DEVICE, project_id=project_id
    )

    assert [group.key for group in answer.groups] == ["desktop"]
    assert not answer.groups[0].is_route_template


# ---------------------------------------------------------------------- filters
#
# `device_class` and `route` are columns of the rollup, so they narrow what is
# merged. The p75 of "mobile only" is therefore the p75 of the mobile readings
# and not the overall p75 with the desktop rows hidden beside it — which is the
# whole difference between a filter and a view.
#
# There is deliberately no rating filter. The rollup carries no rating: a rating
# is a property of a value and the p75 is a new value, so it is computed on the
# screen from the merged p75. Filtering on the raw table's per-reading rating
# would exclude pages this screen still labels poor.


async def test_filtering_by_device_class_changes_the_headline_percentile(
    client: AsyncClient, tenant: str
) -> None:
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    # Desktop outnumbers mobile nine to one, so the site's own p75 is a desktop
    # number and the mobile readings are exactly what it hides. Even counts would
    # have put the p75 on the slow side and made this pass either way.
    for device, value, count in (("desktop", 400.0, 9), ("mobile", 3200.0, 1)):
        await _write_readings(
            client,
            tenant_id=tenant,
            project_id=project_id,
            vital="lcp",
            values=[value] * count,
            minute=minute,
            url_path="/",
            device_class=device,
        )

    start, end = _window()
    repository = TenantScopedRepository(client, tenant_id=tenant)
    everyone = await service.summarize_web_vitals(
        repository, project_id=project_id, from_time=start, to_time=end
    )
    mobile = await service.summarize_web_vitals(
        repository, project_id=project_id, from_time=start, to_time=end, device_class="mobile"
    )

    assert everyone.vitals[0].readings == 10
    assert everyone.vitals[0].p75 == pytest.approx(400.0)
    # The filter merges a different set, rather than hiding rows from this one.
    assert mobile.vitals[0].readings == 1
    assert mobile.vitals[0].p75 == pytest.approx(3200.0)


async def test_filtering_by_page_matches_the_path_when_no_route_was_configured(
    client: AsyncClient, tenant: str
) -> None:
    """A page is `route` where one exists and `url_path` where it does not.

    The filter has to match whichever of the two the reader was shown, or a
    customer who configured no route templates can see a page and not filter
    to it.
    """
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    for path, value in (("/checkout", 2500.0), ("/", 300.0)):
        await _write_readings(
            client,
            tenant_id=tenant,
            project_id=project_id,
            vital="lcp",
            values=[value] * 4,
            minute=minute,
            url_path=path,
        )

    start, end = _window()
    checkout = await service.summarize_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        project_id=project_id,
        from_time=start,
        to_time=end,
        route="/checkout",
    )

    assert checkout.vitals[0].p75 == pytest.approx(2500.0)


async def test_filtering_by_page_matches_the_template_when_one_was_configured(
    client: AsyncClient, tenant: str
) -> None:
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[2500.0] * 4,
        minute=minute,
        url_path="/orders/8f21",
        route="/orders/{id}",
    )

    start, end = _window()
    repository = TenantScopedRepository(client, tenant_id=tenant)
    by_template = await service.summarize_web_vitals(
        repository, project_id=project_id, from_time=start, to_time=end, route="/orders/{id}"
    )
    by_raw_path = await service.summarize_web_vitals(
        repository, project_id=project_id, from_time=start, to_time=end, route="/orders/8f21"
    )

    assert by_template.vitals[0].p75 == pytest.approx(2500.0)
    # The template is what the reader was shown, so the raw path is not the page.
    assert by_raw_path.vitals == []


async def test_a_filter_narrows_the_breakdown_as_well_as_the_headline(
    client: AsyncClient, tenant: str
) -> None:
    """The breakdown is capped at fifty groups, so its filter must be a predicate."""
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    for device in ("desktop", "mobile"):
        for path in ("/", "/checkout"):
            await _write_readings(
                client,
                tenant_id=tenant,
                project_id=project_id,
                vital="lcp",
                values=[1000.0] * 4,
                minute=minute,
                url_path=path,
                device_class=device,
            )

    start, end = _window()
    answer = await service.break_down_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        dimension=BreakdownDimension.PAGE,
        project_id=project_id,
        from_time=start,
        to_time=end,
        device_class="mobile",
    )

    assert sorted(group.key for group in answer.groups) == ["/", "/checkout"]
