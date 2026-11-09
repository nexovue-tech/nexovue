"""F28's release facts in the scheduler, against real Postgres (plan T3.8; D56, AC70, AC73).

`release_before` at any age and `releases_near` with the service's own first, each bound
to one organization and project; `coverage` taking the API's four values from the same
rows; and FR44's line in a finding's opening notice, read after the claim on its own
connection: present when a release came first, left out when the read fails or hangs,
with the notice delivered and the claim standing either way.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alert_fixtures import (
    AlertTenant,
    add_finding,
    add_rule,
    anomaly_rule,
    drop_tenant,
    future,
    make_tenant,
    postgres_reachable,
    rows,
)
from live import require_stack
from nexovue_platform.email import Delivery, Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import delivery
from nexovue_scheduler.alerts.delivery import deliver_due
from nexovue_scheduler.alerts.dispatch import Outbound
from nexovue_scheduler.detection import notify
from nexovue_scheduler.releases import facts

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_release_facts_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

SERVICE = "checkout"
MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
AT = datetime(2026, 11, 9, 14, 2, tzinfo=UTC)


@pytest.fixture
async def acme(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await make_tenant(engine, with_check=False)
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


@pytest.fixture
async def globex(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await make_tenant(engine, with_check=False)
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


async def _release(
    engine: AsyncEngine,
    tenant: AlertTenant,
    version: str,
    started_at: datetime,
    *,
    service: str = SERVICE,
    seen: bool = True,
    reported_at: datetime | None = None,
    state: str = "finished",
    already_running: bool = False,
    period: bool = True,
) -> uuid.UUID:
    """A release, its one period unless `period` is False, and its one report if any."""
    keys: dict[str, Any] = {
        "org_id": tenant.org_id,
        "project_id": tenant.project_id,
        "service": service,
        "version": version,
        "seen": started_at if seen else None,
        "reported": reported_at,
        "count": 1 if reported_at else 0,
        "state": state if reported_at else None,
        "started": started_at,
        "basis": "seen" if seen else "reported",
        "running": already_running,
    }
    async with engine.begin() as connection:
        release_id: uuid.UUID = await connection.scalar(
            text(
                """
                INSERT INTO releases (org_id, project_id, service_name, version, first_seen_at,
                                      last_seen_at, first_reported_at, last_reported_at,
                                      report_count, last_state, last_activity_at)
                VALUES (:org_id, :project_id, :service, :version, :seen, :seen, :reported,
                        :reported, :count, :state, now())
                RETURNING id
                """
            ),
            keys,
        )
        if period:
            await connection.execute(
                text(
                    """
                    INSERT INTO release_periods (org_id, release_id, project_id, service_name,
                                                 started_at, start_basis, already_running,
                                                 is_return)
                    VALUES (:org_id, :release_id, :project_id, :service, :started, :basis,
                            :running, false)
                    """
                ),
                {**keys, "release_id": release_id},
            )
        if reported_at is not None:
            await connection.execute(
                text(
                    """
                    INSERT INTO deploy_events (org_id, project_id, version, service_name,
                                               deployed_at, source, delivery_id, metadata,
                                               state, release_id)
                    VALUES (:org_id, :project_id, :version, :service, :reported, 'ci',
                            :delivery, '{}', :state, :release_id)
                    """
                ),
                {**keys, "release_id": release_id, "delivery": str(uuid.uuid4())},
            )
    return release_id


async def _before(engine: AsyncEngine, tenant: AlertTenant, at: datetime) -> Any:
    async with engine.connect() as connection:
        return await facts.release_before(
            connection, org_id=tenant.org_id, project_id=tenant.project_id, service=SERVICE, at=at
        )


# --------------------------------------------------------------- release_before


async def test_the_release_before_is_the_last_one_starting_by_then_at_any_age(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """FR32, FR44, D56: 41 days earlier is still the release before; a later one is not."""
    await _release(engine, acme, "v1", AT - 60 * DAY)
    await _release(engine, acme, "v2", AT - 41 * DAY)
    await _release(engine, acme, "v3", AT + MINUTE)

    before = await _before(engine, acme, AT)

    assert before is not None
    assert (before.version, before.started_at, before.basis) == ("v2", AT - 41 * DAY, "seen")


async def test_a_release_in_the_finding_s_own_minute_came_before_it(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC41's rule, read the same way from the other side."""
    await _release(engine, acme, "v2", AT)
    before = await _before(engine, acme, AT)
    assert before is not None and before.version == "v2"


async def test_facts_are_bound_to_one_organization_and_project(
    engine: AsyncEngine, acme: AlertTenant, globex: AlertTenant
) -> None:
    """D56: another organization's release of the same service and version is not ours."""
    await _release(engine, globex, "v2", AT - MINUTE)
    async with engine.begin() as connection:
        other_project = uuid.uuid4()
        await connection.execute(
            text(
                "INSERT INTO projects (id, org_id, slug, name) "
                "VALUES (:id, :org_id, 'staging', 'Staging')"
            ),
            {"id": other_project, "org_id": acme.org_id},
        )
    staging = AlertTenant(
        acme.org_id, other_project, acme.owner_email, None, acme.owners_channel_id, uuid.uuid4()
    )
    await _release(engine, staging, "v3", AT - MINUTE)

    assert await _before(engine, acme, AT) is None
    async with engine.connect() as connection:
        near = await facts.releases_near(
            connection,
            org_id=acme.org_id,
            project_id=acme.project_id,
            service=SERVICE,
            at=AT,
            lookback=2 * HOUR,
        )
    assert near == []


# ---------------------------------------------------------------- releases_near


async def test_releases_near_put_the_service_first_and_keep_to_the_lookback(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """FR52, D56: newest first after the service's own; none older than the lookback, none
    already running."""
    await _release(engine, acme, "p7", AT - 20 * MINUTE, service="payments")
    await _release(engine, acme, "s3", AT - 10 * MINUTE, service="search")
    await _release(engine, acme, "v2", AT - 90 * MINUTE)
    await _release(engine, acme, "old", AT - 3 * HOUR, service="mailer")
    await _release(engine, acme, "r1", AT - 5 * MINUTE, service="ranker", already_running=True)

    async with engine.connect() as connection:
        near = await facts.releases_near(
            connection,
            org_id=acme.org_id,
            project_id=acme.project_id,
            service=SERVICE,
            at=AT,
            lookback=2 * HOUR,
        )

    assert [(fact.service_name, fact.version) for fact in near] == [
        ("checkout", "v2"),
        ("search", "s3"),
        ("payments", "p7"),
    ]


# --------------------------------------------------------------------- coverage


async def _coverage(engine: AsyncEngine, tenant: AlertTenant, service: str, at: datetime) -> Any:
    async with engine.connect() as connection:
        return await facts.coverage(
            connection, org_id=tenant.org_id, project_id=tenant.project_id, service=service, at=at
        )


async def test_coverage_takes_the_api_s_four_values_from_the_same_rows(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC73: the rows `test_releases_live.py` gives the API read the same here; a failed
    report 31 days old is still deploy information, with its date."""
    month_ago = AT - 31 * DAY
    await _release(
        engine,
        acme,
        "b1",
        month_ago,
        service="billing",
        seen=False,
        reported_at=month_ago,
        state="failed",
        period=False,
    )
    await _release(engine, acme, "v2", AT - 2 * HOUR, reported_at=AT - HOUR)
    await _release(engine, acme, "s1", AT - 3 * HOUR, service="search")

    billing = await _coverage(engine, acme, "billing", AT)
    assert (billing.value, billing.last_reported_at) == ("deploys_only", month_ago)
    assert (await _coverage(engine, acme, SERVICE, AT)).value == "deploys_and_versions"
    assert (await _coverage(engine, acme, "search", AT)).value == "versions_only"
    assert (await _coverage(engine, acme, "mailer", AT)).value == "none"
    early = await _coverage(engine, acme, SERVICE, AT - 5 * HOUR)
    assert (early.value, early.first_heard_at, early.last_reported_at) == (
        "none",
        AT - 2 * HOUR,
        None,
    )


# ------------------------------------------------- FR44 in the opening notice, AC70


class _Mailbox:
    def __init__(self) -> None:
        self.bodies: list[str] = []

    async def send(
        self,
        *,
        to: str,
        message: Message,
        data: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> Delivery:
        self.bodies.append(str(data["body"]))
        return Delivery(provider_message_id="recorded")


async def _finding_opened(
    engine: AsyncEngine, tenant: AlertTenant, at: datetime, *, service: str | None = SERVICE
) -> uuid.UUID:
    async with engine.begin() as connection:
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Findings",
            condition=anomaly_rule(),
            channel_ids=[tenant.owners_channel_id],
        )
        finding = await add_finding(connection, tenant, service=service, started_at=at - 5 * MINUTE)
        await notify.finding_opened(
            connection, finding, org_id=tenant.org_id, severity="warning", at=at
        )
    return finding


async def _deliver(engine: AsyncEngine, tenant: AlertTenant, at: datetime) -> _Mailbox:
    mailbox = _Mailbox()
    await deliver_due(
        engine,
        Outbound(email_sender=mailbox, secret_box=None, app_base_url="https://app.test"),
        now=lambda: at,
        org_id=tenant.org_id,
    )
    return mailbox


async def _states(engine: AsyncEngine, tenant: AlertTenant) -> list[str]:
    found = await rows(
        engine, "SELECT state FROM notifications WHERE org_id = :org_id", org_id=tenant.org_id
    )
    return [row.state for row in found]


async def test_an_opening_notice_carries_the_release_before_it(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """FR44, AC70: with no AI anywhere, the line from stored facts, as order."""
    at = future()
    await _release(engine, acme, "v2.1", at - 11 * MINUTE, reported_at=at - 11 * MINUTE)
    await _finding_opened(engine, acme, at)

    mailbox = await _deliver(engine, acme, at)

    (body,) = mailbox.bodies
    assert (
        "Most recent release on this service: v2.1, 6 min before this. Order only, not cause."
        in body
    )
    assert await _states(engine, acme) == ["delivered"]


async def test_a_service_we_hear_nothing_about_reads_coverage_none(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    at = future()
    await _finding_opened(engine, acme, at)
    (body,) = (await _deliver(engine, acme, at)).bodies
    assert "We receive no deploy information for this service." in body


async def test_a_failing_statement_leaves_the_line_out_and_the_claim_stands(
    engine: AsyncEngine, acme: AlertTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC70, D51: a real database error on the read's own connection cannot abort the
    claim's transaction; the notice goes without the line."""
    monkeypatch.setattr(facts, "_COVERAGE", text("SELECT 1 / 0 AS first_reported_at"))
    at = future()
    await _release(engine, acme, "v2.1", at - 11 * MINUTE)
    await _finding_opened(engine, acme, at)

    (body,) = (await _deliver(engine, acme, at)).bodies

    assert "release" not in body
    assert await _states(engine, acme) == ["delivered"]


async def test_a_read_that_hangs_is_given_up_within_its_limit(
    engine: AsyncEngine, acme: AlertTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC70: half a second, including the wait for a connection; then the alert goes."""

    async def hangs(*_: Any, **__: Any) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(facts, "release_before", hangs)
    at = future()
    await _finding_opened(engine, acme, at)

    started = time.monotonic()
    (body,) = (await _deliver(engine, acme, at)).bodies
    waited = time.monotonic() - started

    assert "release" not in body and "deploy information" not in body
    assert waited < delivery.RELEASE_READ_SECONDS + 5
    assert await _states(engine, acme) == ["delivered"]


async def test_a_project_finding_makes_no_read(
    engine: AsyncEngine, acme: AlertTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D51: a project finding names no service, so it carries no line and reads nothing."""
    calls: list[object] = []

    async def counted(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(facts, "release_before", counted)
    at = future()
    await _finding_opened(engine, acme, at, service=None)

    await _deliver(engine, acme, at)

    assert calls == []
    assert await _states(engine, acme) == ["delivered"]
