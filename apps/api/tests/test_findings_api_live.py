"""The findings read routes, through HTTP, against real Postgres and ClickHouse (F6, T2c.5).

Every route is on the read surface (ADR-017), so each is called both as a
signed-in person and as an API token. Postgres rows are rolled back after each
test; ClickHouse readings are written under the test organization's own tenant
id and deleted at the end of the run.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from nexovue_api.data.postgres.models import (
    DeployEvent,
    DetectionState,
    FindingEvent,
    Project,
    Release,
    ReleasePeriod,
    User,
)
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
    # F22 FR20, AC22: a token reads a case's states and times, never its people.
    "assignee_id",
    "assignee_name",
    "case_taken_at",
    "assignee_acted_since_join",
    "case_resolved_by_name",
    "project_has_pagerduty",
    "email_available",
    # F22 T2.7: the page's earlier case, its end and its keeping.
    "earlier_case",
    "case_activity_at",
    "case_ends_on",
    "case_ends_by",
    "deletes_on",
    "joined_kept_count",
    # F22 T4.3: the case's tracker issues (FR20, AC22: a token never reads them).
    "links",
}
# A person's feed row beyond a token's (F22 FR23, AC71): who has the case, the case's
# end in its last days, and in a case filter, the project.
PERSON_FEED_FIELDS: Final = {
    "assignee_id",
    "assignee_name",
    "case_resolved_by_name",
    "case_taken_at",
    "assignee_acted_since_join",
    "case_activity_at",
    "case_ends_on",
    "case_ends_by",
    "project_name",
}
# AC27's test set, shared with the console's `ordering.test.ts`.
FEED_ORDER_FIXTURE: Final = (
    Path(__file__).resolve().parents[2]
    / "web"
    / "src"
    / "features"
    / "findings"
    / "feed-order.fixture.json"
)
SLOWER: Final[dict[str, Any]] = {
    "signal": "slower",
    "key": "slower",
    "normal": 110.0,
    "current": 330.0,
    "worst": 412.0,
    "comparison": "Tuesdays around 15:00, last 3 weeks",
    "endpoint": "POST /pay",
    "band_low": 110.0,
    "band_high": 165.0,
    "endpoint_current": 400.0,
    "endpoint_last_week": 100.0,
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
    # Aware: the ClickHouse client reads a naive time in the machine's own zone.
    now = datetime.now(UTC).replace(second=0, microsecond=0)
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


async def test_the_feed_carries_only_the_case_s_people_and_a_token_s_carries_none(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """F6's rule that the feed quotes nobody, narrowed by F22 FR23 and AC71: a
    person's row names who has the case, read in the feed's one statement; a
    token's row names nobody, and neither carries the page's other fields."""
    await _finding(
        db,
        acme,
        acknowledged_at=datetime.now(UTC),
        acknowledged_by=acme.user.id,
        case_state="investigating",
        case_opened_at=datetime.now(UTC),
        case_activity_at=datetime.now(UTC),
        assignee_id=acme.user.id,
    )
    callers = _callers(acme)

    person = (
        await _get(db, clickhouse, callers.person, "/findings", project_id=acme.project.id)
    ).json()["findings"][0]
    token = (
        await _get(db, clickhouse, callers.token, "/findings", project_id=acme.project.id)
    ).json()["findings"][0]

    assert set(person) - set(token) == PERSON_FEED_FIELDS
    assert MEMBER_ONLY_FIELDS.isdisjoint(token)
    assert MEMBER_ONLY_FIELDS & set(person) == {"assignee_id", "assignee_name"} | (
        PERSON_FEED_FIELDS & MEMBER_ONLY_FIELDS
    )
    assert person["assignee_name"] == (acme.user.name or acme.user.email)


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
    # With no readings, the stored band and the week's comparison still say it (FR31).
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


# ------------------------------------------------------------- the case feed (F22 T2.3)


def _active(**fields: Any) -> dict[str, Any]:
    """An investigating case's columns, as the API's take leaves them."""
    now = datetime.now(UTC)
    return {"case_state": "investigating", "case_opened_at": now, "case_activity_at": now, **fields}


def _resolved(resolved_minutes_ago: int, **fields: Any) -> dict[str, Any]:
    at = datetime.now(UTC) - timedelta(minutes=resolved_minutes_ago)
    return {
        "case_state": "resolved",
        "case_opened_at": at - timedelta(hours=1),
        "case_activity_at": at,
        "case_resolved_at": at,
        "case_resolution": "resolved",
        **fields,
    }


async def test_an_active_case_on_a_closed_finding_stays_in_the_feed_s_200(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """AC26, FR21: a closed finding with an active case ranks above every other
    closed one, so 250 newer closed findings cannot push it past the cap."""
    kept = await _finding(
        db,
        acme,
        service_name="worked",
        started_minutes_ago=600,
        closed_minutes_ago=500,
        **_active(),
    )
    now = datetime.now(UTC)
    db.add_all(
        FindingRow(
            org_id=acme.organization.id,
            project_id=acme.project.id,
            scope="service",
            service_name=f"noise-{index}",
            severity="critical",
            state="closed",
            closed_reason="recovered",
            closed_at=now - timedelta(minutes=index + 1),
            title="noise",
            signals=[SLOWER],
            started_at=now - timedelta(minutes=index + 2),
        )
        for index in range(250)
    )
    await db.flush()

    body = (
        await _get(db, clickhouse, _callers(acme).person, "/findings", project_id=acme.project.id)
    ).json()

    assert body["truncated"] is True
    assert body["findings"][0]["id"] == str(kept.id)


async def test_the_server_s_order_is_the_console_s_on_every_combination(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """AC27, FR21: the shared test set, ordered by the server's one rank. The console's
    `orderFindings` is held to the same `expected_order` by `ordering.test.ts`."""
    fixture = json.loads(FEED_ORDER_FIXTURE.read_text(encoding="utf-8"))
    now = datetime.now(UTC)
    made: dict[str, FindingRow] = {}
    for entry in fixture["findings"]:
        case: dict[str, Any] = {}
        if entry["case_state"] == "investigating":
            case = _active()
        elif entry["case_state"] == "resolved":
            case = _resolved(1)
        closed_ago = entry.get("closed_minutes_ago")
        made[entry["id"]] = await _finding(
            db,
            acme,
            service_name=entry["service"],
            severity=entry["severity"],
            started_minutes_ago=entry["started_minutes_ago"],
            closed_minutes_ago=closed_ago,
            acknowledged_at=now if entry["acknowledged"] else None,
            root_finding_id=made[entry["root"]].id if "root" in entry else None,
            **case,
        )
    names = {str(row.id): name for name, row in made.items()}

    body = (
        await _get(db, clickhouse, _callers(acme).person, "/findings", project_id=acme.project.id)
    ).json()

    assert [names[finding["id"]] for finding in body["findings"]] == fixture["expected_order"]
    by_name = {names[finding["id"]]: finding for finding in body["findings"]}
    # AC59's rank half: a root with an open joined finding is open, at the worse severity.
    assert by_name["root-critical-return"]["case_signal"] == "open"
    assert by_name["root-critical-return"]["case_severity"] == "critical"


async def _project(db: AsyncSession, tenant: ProvisionedTenant, name: str) -> Project:
    project = Project(
        org_id=tenant.organization.id, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}", name=name
    )
    db.add(project)
    await db.flush()
    return project


async def _colleague(db: AsyncSession, tenant: ProvisionedTenant) -> User:
    person = User(
        org_id=tenant.organization.id,
        email=f"asha-{uuid.uuid4()}@example.test",
        name="Asha",
        role="member",
        password_hash="not-a-hash",
    )
    db.add(person)
    await db.flush()
    return person


async def test_the_case_filters_read_across_the_organization_and_name_each_project(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant, globex: ProvisionedTenant
) -> None:
    """AC28's server half, FR22: for a person the three filters read every project
    of the organization, name each row's project, include muted findings and leave
    out joined ones. No project is named, and none is needed."""
    staging = await _project(db, acme, "Staging")
    asha = await _colleague(db, acme)
    mine = await _finding(db, acme, service_name="mine", **_active(assignee_id=acme.user.id))
    theirs = await _finding(
        db,
        acme,
        project_id=staging.id,
        service_name="theirs",
        muted=True,
        **_active(assignee_id=asha.id),
    )
    await _finding(db, acme, service_name="joined", root_finding_id=mine.id)
    taken_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=7)
    db.add(
        FindingEvent(
            org_id=acme.organization.id,
            finding_id=mine.id,
            kind="assigned",
            actor_kind="person",
            actor_id=acme.user.id,
            detail={"assignee_id": str(acme.user.id)},
            at=taken_at,
        )
    )
    await db.flush()
    old = await _finding(
        db,
        acme,
        service_name="old",
        closed_minutes_ago=60,
        started_minutes_ago=120,
        **_resolved(30),
    )
    newer = await _finding(
        db,
        acme,
        project_id=staging.id,
        service_name="newer",
        closed_minutes_ago=60,
        started_minutes_ago=120,
        **_resolved(5, case_resolved_by=asha.id),
    )
    await _finding(db, acme, service_name="no-case")
    await _finding(db, globex, service_name="elsewhere", **_active())
    person = _callers(acme).person

    active = (await _get(db, clickhouse, person, "/findings", case="active")).json()
    assigned = (await _get(db, clickhouse, person, "/findings", case="mine")).json()
    resolved = (await _get(db, clickhouse, person, "/findings", case="resolved")).json()

    assert {row["id"] for row in active["findings"]} == {str(mine.id), str(theirs.id)}
    by_id = {row["id"]: row for row in active["findings"]}
    assert by_id[str(theirs.id)]["project_name"] == "Staging"
    assert by_id[str(theirs.id)]["assignee_name"] == "Asha"
    assert by_id[str(mine.id)]["project_name"] == acme.project.name
    # D76: "You took this at 14:06", from the newest `assigned` event naming them.
    assert _instant(by_id[str(mine.id)]["case_taken_at"]) == taken_at
    assert by_id[str(theirs.id)]["case_taken_at"] is None
    assert by_id[str(theirs.id)]["trend"] == []
    assert [row["id"] for row in assigned["findings"]] == [str(mine.id)]
    assert [row["id"] for row in resolved["findings"]] == [str(newer.id), str(old.id)]
    assert resolved["findings"][0]["case_resolved_by_name"] == "Asha"


async def test_a_token_names_a_readable_project_and_never_asks_for_mine(
    db: AsyncSession,
    clickhouse: AsyncClient,
    acme: ProvisionedTenant,
    staging: Project,
) -> None:
    """AC71, AC110, FR22, BR14: a token must name a project it can read to use a
    case filter. Another project is the same 404 as `get_finding`; no project is
    422; `case=mine` is 422; its rows name nobody."""
    here = await _finding(db, acme, service_name="here", **_active(assignee_id=acme.user.id))
    await _finding(db, acme, project_id=staging.id, service_name="there", **_active())
    token = _callers(acme).token
    every_project = Reader(org_id=acme.organization.id, token_id=uuid.uuid4())

    own = await _get(db, clickhouse, token, "/findings", case="active", project_id=acme.project.id)
    elsewhere = await _get(db, clickhouse, token, "/findings", case="active", project_id=staging.id)
    no_project = await _get(db, clickhouse, every_project, "/findings", case="resolved")
    mine = await _get(db, clickhouse, token, "/findings", case="mine", project_id=acme.project.id)
    unseen = await _get(db, clickhouse, token, f"/findings/{uuid.uuid4()}")

    assert own.status_code == 200, own.text
    assert [row["id"] for row in own.json()["findings"]] == [str(here.id)]
    assert MEMBER_ONLY_FIELDS.isdisjoint(own.json()["findings"][0])
    assert "project_name" not in own.json()["findings"][0]
    assert (elsewhere.status_code, unseen.status_code) == (404, 404)
    assert no_project.status_code == 422
    assert mine.status_code == 422


async def test_without_a_case_filter_a_project_is_still_required(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    response = await _get(db, clickhouse, _callers(acme).person, "/findings")
    assert response.status_code == 422


# ------------------------------------------------ keeping and ending (F22 T2.7)


async def _page(
    db: AsyncSession, clickhouse: AsyncClient, tenant: ProvisionedTenant, finding: FindingRow
) -> dict[str, Any]:
    response = await _get(db, clickhouse, _callers(tenant).person, f"/findings/{finding.id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _instant(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


@pytest.mark.parametrize(
    "resolution",
    ["resolved", "false_alarm", "no_activity", "tracker_closed", "recovered_untouched"],
)
async def test_a_new_finding_after_a_resolved_case_names_that_case(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant, resolution: str
) -> None:
    """AC15, AC95, FR13: the new finding has no case, and its page names the earlier
    one with how it ended, which decides whether the page offers Reopen that case."""
    earlier = await _finding(
        db,
        acme,
        started_minutes_ago=600,
        closed_minutes_ago=500,
        **_resolved(400, case_resolution=resolution),
    )
    later = await _finding(db, acme, started_minutes_ago=10)

    page = await _page(db, clickhouse, acme, later)

    assert page["case_state"] is None
    named = page["earlier_case"]
    assert named["id"] == str(earlier.id)
    assert (named["case_state"], named["case_resolution"]) == ("resolved", resolution)
    assert _instant(named["case_resolved_at"]) == earlier.case_resolved_at


async def test_a_new_finding_beside_a_quiet_case_names_it_and_a_finding_in_a_case_names_none(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """AC68's page half, FR9: past the 7-day window the new finding stands alone and
    its page names the quiet case and when a person last touched it. A finding
    that holds a case, or joined one, names no earlier case."""
    quiet_since = datetime.now(UTC) - timedelta(days=12)
    quiet = await _finding(
        db,
        acme,
        started_minutes_ago=20 * 24 * 60,
        closed_minutes_ago=19 * 24 * 60,
        **_active(case_activity_at=quiet_since, case_opened_at=quiet_since),
    )
    standalone = await _finding(db, acme, started_minutes_ago=5)
    other = await _finding(db, acme, service_name="cart", started_minutes_ago=20, **_active())

    named = (await _page(db, clickhouse, acme, standalone))["earlier_case"]
    assert (named["id"], named["case_state"]) == (str(quiet.id), "investigating")
    assert _instant(named["case_activity_at"]) == quiet_since
    assert (await _page(db, clickhouse, acme, other))["earlier_case"] is None


async def test_an_active_case_says_when_it_ends_by_itself_only_when_it_should(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """FR26, FR49: an untouched recovered case ends 72 hours after its last close,
    said from recovery; an idle one 90 days after its last act, said from 14 days
    before; a worked case far from either says nothing. The case filter's row says
    the same, for the case row's warning."""
    now = datetime.now(UTC)
    untouched = await _finding(
        db,
        acme,
        service_name="untouched",
        started_minutes_ago=300,
        closed_minutes_ago=120,
        **_active(),
    )
    idle_since = now - timedelta(days=80)
    idle = await _finding(
        db,
        acme,
        service_name="idle",
        started_minutes_ago=81 * 24 * 60,
        **_active(
            case_activity_at=idle_since, case_opened_at=idle_since, case_worked_at=idle_since
        ),
    )
    worked = await _finding(
        db,
        acme,
        service_name="worked",
        started_minutes_ago=300,
        closed_minutes_ago=120,
        **_active(case_worked_at=now),
    )

    pages = {
        name: await _page(db, clickhouse, acme, row)
        for name, row in (("untouched", untouched), ("idle", idle), ("worked", worked))
    }
    rows = {
        row["id"]: row
        for row in (
            await _get(db, clickhouse, _callers(acme).person, "/findings", case="active")
        ).json()["findings"]
    }

    closed_at = untouched.closed_at
    assert closed_at is not None
    assert pages["untouched"]["case_ends_by"] == "untouched"
    assert _instant(pages["untouched"]["case_ends_on"]) == closed_at + timedelta(hours=72)
    assert pages["idle"]["case_ends_by"] == "idle"
    assert _instant(pages["idle"]["case_ends_on"]) == idle_since + timedelta(days=90)
    assert _instant(pages["idle"]["case_activity_at"]) == idle_since
    assert (pages["worked"]["case_ends_on"], pages["worked"]["case_ends_by"]) == (None, None)
    assert rows[str(idle.id)]["case_ends_by"] == "idle"
    assert rows[str(untouched.id)]["case_ends_on"] == pages["untouched"]["case_ends_on"]


async def test_a_resolved_case_says_when_it_is_deleted_and_how_many_joined_are_kept(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """AC112, FR24, FR27: the date is the plan's derived retention after the later of
    the close and the resolve, as the sweep deletes it; each joined finding still
    kept is counted. An open finding is never deleted, so it has no date."""
    root = await _finding(
        db, acme, started_minutes_ago=600, closed_minutes_ago=500, **_resolved(60)
    )
    for index in range(3):
        await _finding(
            db,
            acme,
            started_minutes_ago=400 - index,
            closed_minutes_ago=300 - index,
            root_finding_id=root.id,
        )
    still_open = await _finding(db, acme, service_name="open", **_resolved(5))

    page = await _page(db, clickhouse, acme, root)

    resolved_at = root.case_resolved_at
    assert resolved_at is not None
    assert _instant(page["deletes_on"]) == resolved_at + timedelta(days=30)
    assert page["joined_kept_count"] == 3
    assert (await _page(db, clickhouse, acme, still_open))["deletes_on"] is None


# ------------------------------------------------- F28: what came before, AC40


async def _period(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    version: str,
    started_at: datetime,
    *,
    service: str = "checkout",
    seen: bool = True,
    reported: bool = False,
    state: str | None = None,
    already_running: bool = False,
) -> ReleasePeriod:
    release = Release(
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        service_name=service,
        version=version,
        first_seen_at=started_at if seen else None,
        last_seen_at=started_at if seen else None,
        first_reported_at=started_at if reported else None,
        last_reported_at=started_at if reported else None,
        report_count=1 if reported else 0,
        last_state=state or ("finished" if reported else None),
    )
    db.add(release)
    await db.flush()
    if reported:
        db.add(
            DeployEvent(
                org_id=tenant.organization.id,
                project_id=tenant.project.id,
                version=version,
                service_name=service,
                deployed_at=started_at,
                source="ci",
                delivery_id=f"{service}-{version}-{uuid.uuid4()}",
                event_metadata={},
                state=state or "finished",
                release_id=release.id,
            )
        )
    period = ReleasePeriod(
        org_id=tenant.organization.id,
        release_id=release.id,
        project_id=tenant.project.id,
        service_name=service,
        started_at=started_at,
        start_basis="seen" if seen else "reported",
        already_running=already_running,
    )
    db.add(period)
    await db.flush()
    return period


async def _calls(
    clickhouse: AsyncClient, tenant: ProvisionedTenant, client: str, server: str, at: datetime
) -> None:
    delete_after_run("service_edges_1m", str(tenant.organization.id))
    await clickhouse.command(
        "INSERT INTO service_edges_1m (tenant_id, project_id, client_service, server_service, "
        "edge_kind, minute, call_count, error_count, latency_quantiles) "
        "SELECT {tenant:String}, {project:String}, {client:String}, {server:String}, "
        "'CLIENT_SERVER', {minute:DateTime}, 10, 0, quantilesState(0.5, 0.95, 0.99)(1.0)",
        parameters={
            "tenant": str(tenant.organization.id),
            "project": str(tenant.project.id),
            "client": client,
            "server": server,
            "minute": at.replace(second=0, microsecond=0),
        },
    )


async def _context(
    db: AsyncSession, clickhouse: AsyncClient, tenant: ProvisionedTenant, finding: FindingRow
) -> dict[str, Any]:
    answer = await _get(db, clickhouse, _callers(tenant).token, f"/findings/{finding.id}")
    assert answer.status_code == 200, answer.text
    return dict(answer.json()["release_context"])


async def test_a_release_6_minutes_before_reads_with_its_basis_and_coverage(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """AC40, AC73: reported by CI and seen in traffic; coverage as of the finding."""
    finding = await _finding(db, acme, started_minutes_ago=30)
    started = finding.started_at - timedelta(minutes=6)
    period = await _period(db, acme, "v2.1", started, reported=True)

    context = await _context(db, clickhouse, acme, finding)

    before = context["before"]
    assert (before["version"], before["basis"], before["period_id"]) == (
        "v2.1",
        "both",
        str(period.id),
    )
    assert context["coverage"] == "deploys_and_versions"
    assert context["last_reported_at"] is not None
    assert context["affected"] == []


async def test_releases_elsewhere_are_listed_this_service_first_and_its_callees_marked(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """AC71, FR52: payments shipped before a checkout finding, and checkout calls it."""
    finding = await _finding(db, acme, started_minutes_ago=30)
    at = finding.started_at
    await _period(db, acme, "p7", at - timedelta(minutes=20), service="payments")
    await _period(db, acme, "s3", at - timedelta(minutes=10), service="search")
    await _period(db, acme, "v2", at - timedelta(minutes=90))
    await _period(db, acme, "old", at - timedelta(hours=3), service="mailer")
    await _calls(clickhouse, acme, "checkout", "payments", at - timedelta(minutes=15))

    context = await _context(db, clickhouse, acme, finding)

    assert context["called_known"] is True
    assert [(r["service_name"], r["called"]) for r in context["nearby"]] == [
        ("checkout", None),
        ("search", False),
        ("payments", True),
    ]


async def test_coverage_none_and_first_heard_after_read_apart(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """AC40, AC73: no release at all reads none; one first counted after the finding
    started reads "first heard", with no release before."""
    quiet = await _finding(db, acme, service_name="mailer", started_minutes_ago=30)
    late = await _finding(db, acme, started_minutes_ago=300)
    await _period(db, acme, "v1", datetime.now(UTC) - timedelta(minutes=60))

    nothing = await _context(db, clickhouse, acme, quiet)
    heard_late = await _context(db, clickhouse, acme, late)

    assert (nothing["coverage"], nothing["before"], nothing["first_heard_at"]) == (
        "none",
        None,
        None,
    )
    assert (heard_late["coverage"], heard_late["before"]) == ("none", None)
    assert heard_late["first_heard_at"] is not None


async def test_an_old_release_and_one_already_running_are_still_the_release_before(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """FR32, AC40: at any age, 41 days earlier; an already-running version is stated."""
    finding = await _finding(db, acme, started_minutes_ago=30)
    await _period(db, acme, "v1.4", finding.started_at - timedelta(days=60), already_running=True)
    await _period(db, acme, "v1.5", finding.started_at - timedelta(days=41))

    before = (await _context(db, clickhouse, acme, finding))["before"]

    assert before["version"] == "v1.5"
    assert before["started_at"][:10] == (finding.started_at - timedelta(days=41)).date().isoformat()


async def test_a_project_finding_lists_each_affected_service_s_latest_release(
    db: AsyncSession, clickhouse: AsyncClient, acme: ProvisionedTenant
) -> None:
    """FR32: within 24 hours, newest first, one per service, only affected services."""
    finding = await _finding(
        db, acme, service_name=None, started_minutes_ago=30, services=["checkout", "payments"]
    )
    at = finding.started_at
    await _period(db, acme, "v1", at - timedelta(hours=5))
    await _period(db, acme, "v2", at - timedelta(hours=2))
    await _period(db, acme, "p1", at - timedelta(hours=1), service="payments")
    await _period(db, acme, "x1", at - timedelta(minutes=5), service="search")
    await _period(db, acme, "p0", at - timedelta(hours=30), service="payments")

    context = await _context(db, clickhouse, acme, finding)

    assert [(r["service_name"], r["version"]) for r in context["affected"]] == [
        ("payments", "p1"),
        ("checkout", "v2"),
    ]
    assert (context["before"], context["coverage"], context["nearby"]) == (None, None, [])
