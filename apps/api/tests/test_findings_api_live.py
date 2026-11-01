"""The findings read routes, through HTTP, against real Postgres and ClickHouse (F6, T2c.5).

Every route is on the read surface (ADR-017), so each is called both as a
signed-in person and as an API token. Postgres rows are rolled back after each
test; ClickHouse readings are written under the test organization's own tenant
id and deleted at the end of the run.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import httpx
import pytest
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.asyncclient import AsyncClient
from environment import TEST_ENVIRONMENT
from live import require_stack
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.dependencies import (
    Identity,
    Reader,
    get_current_reader,
    get_tenant_repository,
)
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import DetectionState, Project
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.settings import API_PREFIX, Settings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
# What names a person, quotes them, or prepares an action only a person can take.
MEMBER_ONLY_FIELDS: Final = {
    "acknowledged_by",
    "acknowledged_by_name",
    "feedback_reason",
    "new_normal",
}
SLOWER: Final[dict[str, Any]] = {
    "signal": "slower",
    "normal": 110.0,
    "current": 330.0,
    "worst": 412.0,
    "comparison": "Tuesdays around 15:00, last 3 weeks",
    "endpoint": None,
}


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port), 2
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN, 5432) and _reachable(CLICKHOUSE_DSN, 8123),
    suite="apps/api/tests/test_findings_api_live.py",
    remedy="Postgres or ClickHouse is not reachable — run `just dev`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def clickhouse() -> AsyncIterator[AsyncClient]:
    url = urlsplit(CLICKHOUSE_DSN)
    client = await clickhouse_connect.get_async_client(
        host=url.hostname or "localhost",
        port=url.port or 8123,
        username=url.username or "",
        password=url.password or "",
        database=(url.path or "/").lstrip("/"),
    )
    try:
        yield client
    finally:
        await client.close()


async def _tenant(db: AsyncSession, name: str) -> ProvisionedTenant:
    tenant = await provision_tenant(
        db,
        organization_name=name,
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )
    delete_after_run("detection_readings", str(tenant.organization.id))
    return tenant


@pytest.fixture
async def acme(db: AsyncSession) -> ProvisionedTenant:
    return await _tenant(db, "Acme")


@pytest.fixture
async def globex(db: AsyncSession) -> ProvisionedTenant:
    return await _tenant(db, "Globex")


@pytest.fixture
async def staging(db: AsyncSession, acme: ProvisionedTenant) -> Project:
    """A second project in Acme, outside a token scoped to Acme's first."""
    project = Project(
        org_id=acme.organization.id, slug=f"staging-{uuid.uuid4().hex[:8]}", name="Staging"
    )
    db.add(project)
    await db.flush()
    return project


@dataclass(frozen=True)
class Callers:
    """The two kinds of reader the read surface serves."""

    person: Reader
    token: Reader


def _callers(tenant: ProvisionedTenant) -> Callers:
    return Callers(
        person=Reader(
            org_id=tenant.organization.id,
            identity=Identity(user=tenant.user, organization=tenant.organization),
        ),
        token=Reader(
            org_id=tenant.organization.id,
            project_scope=tenant.project.id,
            token_id=uuid.uuid4(),
        ),
    )


def _client(db: AsyncSession, clickhouse: AsyncClient, reader: Reader) -> httpx.AsyncClient:
    from nexovue_api.main import create_app

    settings = Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]
    app = create_app(settings)

    async def db_override() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_current_reader] = lambda: reader
    app.dependency_overrides[get_tenant_repository] = lambda: TenantScopedRepository(
        clickhouse, tenant_id=str(reader.org_id)
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _get(
    db: AsyncSession, clickhouse: AsyncClient, reader: Reader, path: str, **params: Any
) -> httpx.Response:
    async with _client(db, clickhouse, reader) as client:
        return await client.get(f"{API_PREFIX}{path}", params=params)


async def _finding(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    *,
    project_id: uuid.UUID | None = None,
    service_name: str | None = "checkout",
    severity: str = "warning",
    started_minutes_ago: int = 30,
    closed_minutes_ago: int | None = None,
    **fields: Any,
) -> FindingRow:
    now = datetime.now(UTC)
    closed_at = None if closed_minutes_ago is None else now - timedelta(minutes=closed_minutes_ago)
    closed = closed_at is not None
    row = FindingRow(
        org_id=tenant.organization.id,
        project_id=project_id or tenant.project.id,
        scope="service" if service_name is not None else "project",
        service_name=service_name,
        severity=severity,
        state="closed" if closed else "open",
        closed_reason="recovered" if closed else None,
        closed_at=closed_at,
        title=f"{service_name or 'The project'} is slower than normal",
        signals=[SLOWER],
        started_at=now - timedelta(minutes=started_minutes_ago),
        **fields,
    )
    db.add(row)
    await db.flush()
    return row


async def _write_readings(
    clickhouse: AsyncClient,
    tenant: ProvisionedTenant,
    *,
    project_id: uuid.UUID,
    service_name: str,
    p95_by_minutes_ago: dict[int, float],
    version: int = 1,
) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0, tzinfo=None)
    rows = [
        [
            str(tenant.organization.id),
            str(project_id),
            service_name,
            now - timedelta(minutes=ago),
            version,
            5,
            600,
            6,
            p95,
        ]
        for ago, p95 in p95_by_minutes_ago.items()
    ]
    await clickhouse.insert(
        "detection_readings",
        rows,
        column_names=[
            "tenant_id",
            "project_id",
            "service_name",
            "minute",
            "version",
            "window_minutes",
            "window_requests",
            "window_errors",
            "window_p95_ms",
        ],
    )


# ------------------------------------------------------------------- feed


async def test_the_feed_reads_open_then_acknowledged_then_closed_then_severity_then_recency(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    now = datetime.now(UTC)
    closed_old_critical = await _finding(
        db,
        acme,
        service_name="a",
        severity="critical",
        started_minutes_ago=300,
        closed_minutes_ago=200,
    )
    closed_recent_warning = await _finding(
        db, acme, service_name="b", started_minutes_ago=100, closed_minutes_ago=5
    )
    closed_recent_critical = await _finding(
        db,
        acme,
        service_name="c",
        severity="critical",
        started_minutes_ago=100,
        closed_minutes_ago=10,
    )
    acknowledged = await _finding(
        db, acme, service_name="d", severity="critical", started_minutes_ago=1, acknowledged_at=now
    )
    open_warning_new = await _finding(db, acme, service_name="e", started_minutes_ago=2)
    open_critical_old = await _finding(
        db, acme, service_name="f", severity="critical", started_minutes_ago=90
    )
    open_project = await _finding(db, acme, service_name=None, started_minutes_ago=3)
    muted = await _finding(db, acme, service_name="g", severity="critical", muted=True)

    response = await _get(
        db, clickhouse, _callers(acme).person, "/findings", project_id=acme.project.id
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [finding["id"] for finding in body["findings"]] == [
        str(row.id)
        for row in (
            open_critical_old,
            open_warning_new,
            open_project,
            acknowledged,
            closed_recent_critical,
            closed_old_critical,
            closed_recent_warning,
        )
    ]
    assert [finding["state"] for finding in body["findings"]][3:5] == ["acknowledged", "closed"]
    assert body["muted_count"] == 1
    assert body["truncated"] is False
    assert str(muted.id) not in {finding["id"] for finding in body["findings"]}


async def test_the_muted_findings_are_read_apart(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    await _finding(db, acme, service_name="loud")
    muted = await _finding(db, acme, service_name="quiet", muted=True)

    response = await _get(
        db, clickhouse, _callers(acme).person, "/findings", project_id=acme.project.id, muted="true"
    )

    body = response.json()
    assert [finding["id"] for finding in body["findings"]] == [str(muted.id)]
    assert body["findings"][0]["muted"] is True
    assert body["muted_count"] == 1


async def test_open_includes_acknowledged_and_closed_is_only_closed(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    opened = await _finding(db, acme, service_name="a")
    acknowledged = await _finding(db, acme, service_name="b", acknowledged_at=datetime.now(UTC))
    closed = await _finding(db, acme, service_name="c", closed_minutes_ago=1)
    person = _callers(acme).person

    open_feed = await _get(
        db, clickhouse, person, "/findings", project_id=acme.project.id, state="open"
    )
    closed_feed = await _get(
        db, clickhouse, person, "/findings", project_id=acme.project.id, state="closed"
    )

    assert {finding["id"] for finding in open_feed.json()["findings"]} == {
        str(opened.id),
        str(acknowledged.id),
    }
    assert [finding["id"] for finding in closed_feed.json()["findings"]] == [str(closed.id)]


async def test_a_state_the_feed_does_not_know_is_refused(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    response = await _get(
        db,
        clickhouse,
        _callers(acme).person,
        "/findings",
        project_id=acme.project.id,
        state="acknowledged",
    )
    assert response.status_code == 422


# ------------------------------------------------------------------ scope


@pytest.mark.parametrize("path", ["/findings", "/findings/stats"])
async def test_a_token_asking_about_a_project_outside_its_scope_is_refused(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant, staging: Project, path: str
) -> None:
    await _finding(db, acme, project_id=staging.id)

    response = await _get(db, clickhouse, _callers(acme).token, path, project_id=staging.id)

    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/findings", "/findings/stats"])
async def test_another_organizations_project_is_refused(
    db: AsyncSession,
    clickhouse: AsyncClient,
    acme: ProvisionedTenant,
    globex: ProvisionedTenant,
    path: str,
) -> None:
    await _finding(db, globex)

    response = await _get(db, clickhouse, _callers(acme).person, path, project_id=globex.project.id)

    assert response.status_code == 404


async def test_a_finding_in_another_project_is_not_found_by_a_scoped_token(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant, staging: Project
) -> None:
    elsewhere = await _finding(db, acme, project_id=staging.id)
    callers = _callers(acme)

    as_token = await _get(db, clickhouse, callers.token, f"/findings/{elsewhere.id}")
    as_person = await _get(db, clickhouse, callers.person, f"/findings/{elsewhere.id}")

    assert as_token.status_code == 404
    assert as_person.status_code == 200


async def test_another_organizations_finding_is_not_found(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant, globex: ProvisionedTenant
) -> None:
    theirs = await _finding(db, globex)

    response = await _get(db, clickhouse, _callers(acme).person, f"/findings/{theirs.id}")

    assert response.status_code == 404


# ------------------------------------------------------------ one finding


async def test_a_person_sees_who_acknowledged_and_why_and_a_token_does_not(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    finding = await _finding(
        db,
        acme,
        acknowledged_at=datetime.now(UTC),
        acknowledged_by=acme.user.id,
        feedback_reason="the nightly batch job",
        false_alarm_at=datetime.now(UTC),
    )
    callers = _callers(acme)

    as_person = (await _get(db, clickhouse, callers.person, f"/findings/{finding.id}")).json()
    as_token = (await _get(db, clickhouse, callers.token, f"/findings/{finding.id}")).json()

    assert as_person["acknowledged_by"] == str(acme.user.id)
    assert as_person["feedback_reason"] == "the nightly batch job"
    assert MEMBER_ONLY_FIELDS.isdisjoint(as_token)
    assert set(as_person) - set(as_token) == MEMBER_ONLY_FIELDS
    assert as_token["state"] == "acknowledged"


async def test_the_feed_never_carries_the_member_only_fields(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    await _finding(db, acme, acknowledged_at=datetime.now(UTC), acknowledged_by=acme.user.id)

    body = (
        await _get(db, clickhouse, _callers(acme).person, "/findings", project_id=acme.project.id)
    ).json()

    assert MEMBER_ONLY_FIELDS.isdisjoint(body["findings"][0])


async def test_the_trend_is_the_leading_signal_s_last_readings_of_this_project_only(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant, staging: Project
) -> None:
    finding = await _finding(db, acme, service_name="checkout")
    await _write_readings(
        clickhouse,
        acme,
        project_id=acme.project.id,
        service_name="checkout",
        p95_by_minutes_ago={6: 100.0, 5: 200.0, 4: 300.0},
    )
    # The minute re-judged after a crash: the higher version wins.
    await _write_readings(
        clickhouse,
        acme,
        project_id=acme.project.id,
        service_name="checkout",
        p95_by_minutes_ago={4: 350.0},
        version=2,
    )
    # The same service name in another project must not be drawn.
    await _write_readings(
        clickhouse,
        acme,
        project_id=staging.id,
        service_name="checkout",
        p95_by_minutes_ago={3: 9999.0},
    )
    person = _callers(acme).person

    detail = (await _get(db, clickhouse, person, f"/findings/{finding.id}")).json()
    feed = (await _get(db, clickhouse, person, "/findings", project_id=acme.project.id)).json()

    assert detail["trend"] == [100.0, 200.0, 350.0]
    assert feed["findings"][0]["trend"] == [100.0, 200.0, 350.0]


async def test_a_finding_with_no_readings_has_an_empty_trend(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    finding = await _finding(db, acme, service_name="never-judged")

    detail = (await _get(db, clickhouse, _callers(acme).person, f"/findings/{finding.id}")).json()

    assert detail["trend"] == []
    assert detail["signals"] == [SLOWER | {"unit": "ms"}]


# ------------------------------------------------------------------ stats


async def test_stats_count_this_week_s_findings_and_false_alarms_per_judged_service(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant, staging: Project
) -> None:
    now = datetime.now(UTC)
    week = 7 * 24 * 60
    await _finding(db, acme, service_name="a", closed_minutes_ago=10, started_minutes_ago=60)
    await _finding(
        db,
        acme,
        service_name="b",
        closed_minutes_ago=10,
        started_minutes_ago=120,
        false_alarm_at=now,
    )
    await _finding(
        db,
        acme,
        service_name="c",
        closed_minutes_ago=10,
        started_minutes_ago=180,
        false_alarm_at=now,
    )
    # Opened before the window, marked inside it: not this week's finding.
    await _finding(
        db,
        acme,
        service_name="d",
        closed_minutes_ago=10,
        started_minutes_ago=week + 60,
        false_alarm_at=now,
    )
    # Another project's.
    await _finding(db, acme, project_id=staging.id, service_name="e", false_alarm_at=now)
    for service_name, judged in (("a", now), ("b", now), ("learning", None)):
        db.add(
            DetectionState(
                org_id=acme.organization.id,
                project_id=acme.project.id,
                service_name=service_name,
                last_judged_minute=judged,
            )
        )
    await db.flush()

    for reader in (_callers(acme).person, _callers(acme).token):
        response = await _get(db, clickhouse, reader, "/findings/stats", project_id=acme.project.id)
        assert response.status_code == 200, response.text
        assert response.json() == {
            "days": 7,
            "opened": 3,
            "false_alarms": 2,
            "judged_services": 2,
            # 2 false alarms over 2 services for one week.
            "false_finding_rate_per_service_week": 1.0,
            # FR26: each service with a finding in the window, most false alarms first.
            "services": [
                {"service_name": "b", "opened": 1, "false_alarms": 1, "false_alarms_per_week": 1.0},
                {"service_name": "c", "opened": 1, "false_alarms": 1, "false_alarms_per_week": 1.0},
                {"service_name": "a", "opened": 1, "false_alarms": 0, "false_alarms_per_week": 0.0},
            ],
        }


async def test_stats_over_a_longer_window_divide_by_its_weeks(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    now = datetime.now(UTC)
    await _finding(db, acme, closed_minutes_ago=10, false_alarm_at=now)
    db.add(
        DetectionState(
            org_id=acme.organization.id,
            project_id=acme.project.id,
            service_name="checkout",
            last_judged_minute=now,
        )
    )
    await db.flush()

    body = (
        await _get(
            db,
            clickhouse,
            _callers(acme).person,
            "/findings/stats",
            project_id=acme.project.id,
            days=14,
        )
    ).json()

    assert body["false_finding_rate_per_service_week"] == pytest.approx(0.5)
    [checkout] = body["services"]
    assert checkout["false_alarms_per_week"] == pytest.approx(0.5)


async def test_stats_count_each_service_apart_and_a_project_finding_for_none(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """FR26: per service. A project finding spans several, so it counts for the project
    alone."""
    now = datetime.now(UTC)
    await _finding(db, acme, service_name="checkout", closed_minutes_ago=10, false_alarm_at=now)
    await _finding(db, acme, service_name="checkout", started_minutes_ago=90)
    await _finding(db, acme, service_name="search", closed_minutes_ago=10, false_alarm_at=now)
    await _finding(db, acme, service_name="search", closed_minutes_ago=20, false_alarm_at=now)
    await _finding(db, acme, service_name=None)

    body = (
        await _get(
            db, clickhouse, _callers(acme).person, "/findings/stats", project_id=acme.project.id
        )
    ).json()

    assert body["opened"] == 5
    assert [
        (each["service_name"], each["opened"], each["false_alarms"]) for each in body["services"]
    ] == [
        ("search", 2, 2),
        ("checkout", 2, 1),
    ]


async def test_stats_with_nothing_judged_have_no_rate(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    body = (
        await _get(
            db, clickhouse, _callers(acme).person, "/findings/stats", project_id=acme.project.id
        )
    ).json()

    assert (body["opened"], body["false_finding_rate_per_service_week"]) == (0, None)
    assert body["services"] == []
