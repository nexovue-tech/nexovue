"""The user's controls over detection, through HTTP, against real Postgres and
ClickHouse (F6, plan T5.1; spec AC30 to AC35, FR18 to FR24).

Each route is called as the role that may use it and as one that may not. Every
change must leave an audit row with its reason, and a finding that changed under
the caller must answer 409 and change nothing. Postgres rows are rolled back after
each test; readings are written under the test organization's tenant id and
deleted at the end of the run.

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
from nexovue_platform.detection import COMPUTE_BASELINES_TASK, SHIFT_READINGS_NEEDED
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.dependencies import (
    Identity,
    Reader,
    get_current_identity,
    get_current_reader,
    get_tenant_repository,
)
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import (
    AuditLogEntry,
    BaselineShift,
    DetectionState,
    Job,
    Project,
    ServiceDetectionSettings,
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
SERVICE: Final = "checkout"
# A slower finding's stored entry: 110 ms normally, 330 ms now.
SLOWER: Final[dict[str, Any]] = {
    "signal": "slower",
    "normal": 110.0,
    "current": 330.0,
    "worst": 412.0,
    "comparison": "Tuesdays around 15:00, last 3 weeks",
    "endpoint": None,
}
NORMAL_P95_MS: Final = 110.0
NEW_P95_MS: Final = 330.0


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
    suite="apps/api/tests/test_findings_actions_live.py",
    remedy="Postgres or ClickHouse is not reachable — run `just dev`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
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


@dataclass(frozen=True)
class Team:
    """One organization's people, one of each role."""

    tenant: ProvisionedTenant
    owner: User
    admin: User
    member: User
    viewer: User

    @property
    def org_id(self) -> uuid.UUID:
        return self.tenant.organization.id

    @property
    def project_id(self) -> uuid.UUID:
        return self.tenant.project.id


async def _team(db: AsyncSession, name: str) -> Team:
    tenant = await provision_tenant(
        db,
        organization_name=name,
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )
    delete_after_run("detection_readings", str(tenant.organization.id))
    people = {}
    for role in ("admin", "member", "viewer"):
        person = User(
            org_id=tenant.organization.id,
            email=f"{role}-{uuid.uuid4()}@example.test",
            name=role.capitalize(),
            role=role,
        )
        db.add(person)
        people[role] = person
    db.add(
        DetectionState(
            org_id=tenant.organization.id, project_id=tenant.project.id, service_name=SERVICE
        )
    )
    await db.flush()
    return Team(tenant=tenant, owner=tenant.user, **people)


@pytest.fixture
async def acme(db: AsyncSession) -> Team:
    return await _team(db, "Acme")


@pytest.fixture
async def globex(db: AsyncSession) -> Team:
    return await _team(db, "Globex")


def _client(
    db: AsyncSession, clickhouse: AsyncClient, team: Team, person: User | None
) -> httpx.AsyncClient:
    """As `person`, or as a token scoped to the team's project when None."""
    from nexovue_api.main import create_app

    settings = Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]
    app = create_app(settings)
    organization = team.tenant.organization
    reader = (
        Reader(org_id=organization.id, project_scope=team.project_id, token_id=uuid.uuid4())
        if person is None
        else Reader(
            org_id=organization.id, identity=Identity(user=person, organization=organization)
        )
    )

    async def db_override() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_current_reader] = lambda: reader
    if person is not None:
        app.dependency_overrides[get_current_identity] = lambda: Identity(
            user=person, organization=organization
        )
    app.dependency_overrides[get_tenant_repository] = lambda: TenantScopedRepository(
        clickhouse, tenant_id=str(organization.id)
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _call(
    db: AsyncSession,
    clickhouse: AsyncClient,
    team: Team,
    person: User | None,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    async with _client(db, clickhouse, team, person) as client:
        return await client.request(method, f"{API_PREFIX}{path}", json=body)


async def _finding(
    db: AsyncSession,
    team: Team,
    *,
    service_name: str | None = SERVICE,
    severity: str = "warning",
    started_minutes_ago: int = 60,
    closed_reason: str | None = None,
    **fields: Any,
) -> FindingRow:
    now = datetime.now(UTC)
    closed = closed_reason is not None
    row = FindingRow(
        org_id=team.org_id,
        project_id=team.project_id,
        scope="service" if service_name is not None else "project",
        service_name=service_name,
        severity=severity,
        state="closed" if closed else "open",
        closed_reason=closed_reason,
        closed_at=now - timedelta(minutes=5) if closed else None,
        title=f"{service_name or 'The project'} is 3 times slower than normal since 14:02 UTC",
        signals=[SLOWER],
        started_at=now - timedelta(minutes=started_minutes_ago),
        **fields,
    )
    db.add(row)
    await db.flush()
    return row


async def _told(db: AsyncSession, team: Team, finding: FindingRow) -> uuid.UUID:
    """The finding's opening reached the owners channel, as the project job writes it."""
    owners: uuid.UUID = (
        await db.execute(
            text("SELECT id FROM notification_channels WHERE org_id = :org_id AND kind = 'owners'"),
            {"org_id": team.org_id},
        )
    ).scalar_one()
    await db.execute(
        text(
            """
            INSERT INTO notifications
                (org_id, project_id, finding_id, channel_id, kind, sequence, severity,
                 deliver_after, created_at)
            VALUES (:org_id, :project_id, :finding_id, :channel_id, 'opened', 0, :severity,
                    now(), now())
            """
        ),
        {
            "org_id": team.org_id,
            "project_id": team.project_id,
            "finding_id": finding.id,
            "channel_id": owners,
            "severity": finding.severity,
        },
    )
    return owners


async def _notifications(db: AsyncSession, finding: FindingRow) -> list[Any]:
    return list(
        (
            await db.execute(
                text(
                    "SELECT kind, channel_id FROM notifications "
                    "WHERE org_id = :org_id AND finding_id = :finding_id ORDER BY kind"
                ),
                {"org_id": finding.org_id, "finding_id": finding.id},
            )
        ).all()
    )


async def _audit(db: AsyncSession, team: Team, action: str) -> list[AuditLogEntry]:
    await db.flush()
    return list(
        await db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.org_id == team.org_id, AuditLogEntry.action == action
            )
        )
    )


async def _queued_baselines(db: AsyncSession, team: Team) -> list[Job]:
    await db.flush()
    return list(
        await db.scalars(
            select(Job).where(Job.org_id == team.org_id, Job.task == COMPUTE_BASELINES_TASK)
        )
    )


async def _reload(db: AsyncSession, finding: FindingRow) -> FindingRow:
    await db.refresh(finding)
    return finding


async def _write_readings(
    clickhouse: AsyncClient,
    team: Team,
    *,
    count: int,
    p95: float = NEW_P95_MS,
    normal: float = NORMAL_P95_MS,
    service_name: str = SERVICE,
) -> None:
    """`count` judged minutes ending now, each 5-minute window at `p95` against `normal`."""
    now = datetime.now(UTC).replace(second=0, microsecond=0, tzinfo=None)
    rows = [
        [
            str(team.org_id),
            str(team.project_id),
            service_name,
            now - timedelta(minutes=ago),
            1,
            5,
            600,
            6,
            p95,
            normal,
            0.01,
            0.002,
            (14.0 + 0.0),
        ]
        for ago in range(count)
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
            "slower_median",
            "failing_median",
            "failing_spread",
            "traffic_median",
        ],
    )


def _path(finding: FindingRow, action: str) -> str:
    return f"/findings/{finding.id}/{action}"


# ---------------------------------------------------------------- acknowledge


async def test_acknowledging_records_who_and_when_and_leaves_it_open(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """AC33, FR19."""
    finding = await _finding(db, acme)

    response = await _call(db, clickhouse, acme, acme.member, "POST", _path(finding, "acknowledge"))

    assert response.status_code == 200, response.text
    body = response.json()["finding"]
    assert body["state"] == "acknowledged"
    assert body["acknowledged_by"] == str(acme.member.id)
    assert body["acknowledged_by_name"] == "Member"
    stored = await _reload(db, finding)
    assert stored.state == "open"
    assert stored.acknowledged_by == acme.member.id
    assert stored.acknowledged_at is not None
    [entry] = await _audit(db, acme, "finding.acknowledged")
    assert entry.actor_user_id == acme.member.id
    assert entry.target == f"finding:{finding.id}"


async def test_acknowledging_one_already_acknowledged_or_closed_is_stale(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    acknowledged = await _finding(
        db, acme, acknowledged_by=acme.admin.id, acknowledged_at=datetime.now(UTC)
    )
    closed = await _finding(db, acme, service_name="cart", closed_reason="recovered")

    for finding in (acknowledged, closed):
        response = await _call(
            db, clickhouse, acme, acme.member, "POST", _path(finding, "acknowledge")
        )
        assert response.status_code == 409, response.text
    assert (await _reload(db, acknowledged)).acknowledged_by == acme.admin.id
    assert await _audit(db, acme, "finding.acknowledged") == []


# ---------------------------------------------------------------- false alarm


async def test_an_admin_s_false_alarm_closes_it_with_one_resolved_and_returns_its_minutes(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """AC32, FR20, D28: closed, one *resolved*, counted, the minutes back in the normal,
    and the sensitivity untouched (BR8)."""
    finding = await _finding(db, acme)
    owners = await _told(db, acme, finding)

    response = await _call(
        db,
        clickhouse,
        acme,
        acme.admin,
        "POST",
        _path(finding, "false-alarm"),
        {"reason": "A load test we ran"},
    )

    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["finding"]["state"] == "closed"
    assert changed["finding"]["closed_reason"] == "false_alarm"
    assert changed["finding"]["feedback_reason"] == "A load test we ran"
    assert changed["resolved_to"] == ["Organization owners"]
    stored = await _reload(db, finding)
    assert stored.false_alarm_by == acme.admin.id
    assert stored.false_alarm_at is not None
    assert stored.false_alarm_returns_minutes is True
    assert [(row.kind, row.channel_id) for row in await _notifications(db, finding)] == [
        ("opened", owners),
        ("resolved", owners),
    ]
    project = await db.get(Project, acme.project_id)
    assert project is not None and project.detection_sensitivity == "normal"
    [entry] = await _audit(db, acme, "finding.false_alarm")
    assert entry.actor_user_id == acme.admin.id
    assert entry.detail == {"reason": "A load test we ran", "returns_minutes": True}
    # Its minutes are back in the normal: the service's baselines are rebuilt now.
    assert len(await _queued_baselines(db, acme)) == 1
    stats = await _call(
        db, clickhouse, acme, acme.admin, "GET", f"/findings/stats?project_id={acme.project_id}"
    )
    assert stats.json()["false_alarms"] == 1


async def test_a_member_s_false_alarm_closes_and_counts_but_keeps_its_minutes_out(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """D28, AC34: a member cannot widen the normal."""
    finding = await _finding(db, acme)

    response = await _call(
        db, clickhouse, acme, acme.member, "POST", _path(finding, "false-alarm"), {}
    )

    assert response.status_code == 200, response.text
    stored = await _reload(db, finding)
    assert (stored.state, stored.closed_reason) == ("closed", "false_alarm")
    assert stored.false_alarm_returns_minutes is False
    assert stored.feedback_reason is None
    [entry] = await _audit(db, acme, "finding.false_alarm")
    assert entry.detail == {"reason": None, "returns_minutes": False}
    # Nothing in the normal changed, so nothing is rebuilt.
    assert await _queued_baselines(db, acme) == []


async def test_a_recovered_finding_can_still_be_called_a_false_alarm_once(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """Design, *The finding page*: on a closed finding only *False alarm* remains, and
    only when it closed as recovered. It was already resolved, so nothing is sent."""
    finding = await _finding(db, acme, closed_reason="recovered")
    await _told(db, acme, finding)
    closed_at = finding.closed_at

    first = await _call(db, clickhouse, acme, acme.member, "POST", _path(finding, "false-alarm"))
    again = await _call(db, clickhouse, acme, acme.member, "POST", _path(finding, "false-alarm"))

    assert first.status_code == 200, first.text
    assert first.json()["resolved_to"] == []
    assert again.status_code == 409, again.text
    stored = await _reload(db, finding)
    assert (stored.closed_reason, stored.closed_at) == ("false_alarm", closed_at)
    assert [row.kind for row in await _notifications(db, finding)] == ["opened"]


@pytest.mark.parametrize("reason", ["service_gone", "adopted", "new_normal"])
async def test_a_finding_closed_any_other_way_is_not_a_false_alarm(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team, reason: str
) -> None:
    finding = await _finding(db, acme, closed_reason=reason)

    response = await _call(db, clickhouse, acme, acme.admin, "POST", _path(finding, "false-alarm"))

    assert response.status_code == 409, response.text
    assert (await _reload(db, finding)).false_alarm_at is None


async def test_a_muted_service_s_false_alarm_resolves_only_what_a_machine_holds(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """BR9: people hear nothing about a muted service, not even its close."""
    finding = await _finding(db, acme)
    await _told(db, acme, finding)
    db.add(
        ServiceDetectionSettings(
            org_id=acme.org_id,
            project_id=acme.project_id,
            service_name=SERVICE,
            muted_until=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db.flush()

    response = await _call(db, clickhouse, acme, acme.admin, "POST", _path(finding, "false-alarm"))

    assert response.status_code == 200, response.text
    assert response.json()["resolved_to"] == []
    assert [row.kind for row in await _notifications(db, finding)] == ["opened"]


# ----------------------------------------------------------------- new normal


async def test_the_new_normal_moves_the_normal_by_the_readings_and_closes_the_finding(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """FR21, AC15: the ratio is the median of the readings since the finding opened,
    from the minute it started; the finding closes, and its baselines are rebuilt now."""
    finding = await _finding(db, acme, started_minutes_ago=30)
    owners = await _told(db, acme, finding)
    await _write_readings(clickhouse, acme, count=SHIFT_READINGS_NEEDED + 5)

    preview = await _call(db, clickhouse, acme, acme.admin, "GET", f"/findings/{finding.id}")
    assert preview.status_code == 200, preview.text
    [proposed] = preview.json()["new_normal"]["signals"]
    assert proposed["signal"] == "slower"
    assert proposed["ratio"] == pytest.approx(3.0)
    assert proposed["normal_after"] == pytest.approx(NEW_P95_MS)

    response = await _call(db, clickhouse, acme, acme.admin, "POST", _path(finding, "new-normal"))

    assert response.status_code == 200, response.text
    stored = await _reload(db, finding)
    assert (stored.state, stored.closed_reason) == ("closed", "new_normal")
    [shift] = list(
        await db.scalars(select(BaselineShift).where(BaselineShift.org_id == acme.org_id))
    )
    assert (shift.service_name, shift.signal, shift.reason) == (SERVICE, "slower", "new_normal")
    assert shift.ratio == pytest.approx(3.0)
    assert shift.from_minute == stored.started_at.replace(second=0, microsecond=0)
    assert [(row.kind, row.channel_id) for row in await _notifications(db, finding)] == [
        ("opened", owners),
        ("resolved", owners),
    ]
    [entry] = await _audit(db, acme, "finding.new_normal")
    assert entry.actor_user_id == acme.admin.id
    assert entry.detail == {"shifts": [{"signal": "slower", "ratio": pytest.approx(3.0)}]}
    assert len(await _queued_baselines(db, acme)) == 1


async def test_the_new_normal_is_refused_until_enough_readings_say_what_it_is(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    finding = await _finding(db, acme, started_minutes_ago=30)
    await _write_readings(clickhouse, acme, count=SHIFT_READINGS_NEEDED - 1)

    preview = await _call(db, clickhouse, acme, acme.admin, "GET", f"/findings/{finding.id}")
    assert preview.json()["new_normal"] == {
        "available": False,
        "readings": SHIFT_READINGS_NEEDED - 1,
        "needed": SHIFT_READINGS_NEEDED,
        "signals": [],
    }
    response = await _call(db, clickhouse, acme, acme.admin, "POST", _path(finding, "new-normal"))

    assert response.status_code == 409, response.text
    assert (await _reload(db, finding)).state == "open"
    assert (
        list(await db.scalars(select(BaselineShift).where(BaselineShift.org_id == acme.org_id)))
        == []
    )


async def test_a_closed_finding_has_no_new_normal(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    finding = await _finding(db, acme, closed_reason="recovered")
    await _write_readings(clickhouse, acme, count=SHIFT_READINGS_NEEDED + 5)

    response = await _call(db, clickhouse, acme, acme.admin, "POST", _path(finding, "new-normal"))

    assert response.status_code == 409, response.text
    assert (await _reload(db, finding)).closed_reason == "recovered"


async def test_a_project_finding_has_no_normal_of_its_own_to_move(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    finding = await _finding(db, acme, service_name=None)

    response = await _call(db, clickhouse, acme, acme.admin, "POST", _path(finding, "new-normal"))

    assert response.status_code == 422, response.text
    assert (await _reload(db, finding)).state == "open"


# ------------------------------------------------------------- who may do what


@pytest.mark.parametrize("action", ["acknowledge", "false-alarm", "new-normal"])
async def test_a_viewer_may_do_nothing_to_a_finding(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team, action: str
) -> None:
    """AC34: a viewer is refused all of these."""
    finding = await _finding(db, acme)

    response = await _call(db, clickhouse, acme, acme.viewer, "POST", _path(finding, action))

    assert response.status_code == 403, response.text
    assert (await _reload(db, finding)).state == "open"


async def test_a_member_may_not_set_a_new_normal(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """AC34, FR23."""
    finding = await _finding(db, acme)
    await _write_readings(clickhouse, acme, count=SHIFT_READINGS_NEEDED + 5)

    response = await _call(db, clickhouse, acme, acme.member, "POST", _path(finding, "new-normal"))

    assert response.status_code == 403, response.text
    assert (await _reload(db, finding)).state == "open"


async def test_another_organization_s_finding_is_not_found(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team, globex: Team
) -> None:
    theirs = await _finding(db, globex)

    for action in ("acknowledge", "false-alarm", "new-normal"):
        response = await _call(db, clickhouse, acme, acme.owner, "POST", _path(theirs, action))
        assert response.status_code == 404, response.text
    assert (await _reload(db, theirs)).acknowledged_at is None


# ----------------------------------------------------------- snooze and mute


def _service(action: str, name: str = SERVICE) -> str:
    return f"/services/{name}/{action}"


def _ahead(**delta: float) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


async def _settings(db: AsyncSession, team: Team) -> ServiceDetectionSettings | None:
    await db.flush()
    found: ServiceDetectionSettings | None = await db.scalar(
        select(ServiceDetectionSettings).where(
            ServiceDetectionSettings.org_id == team.org_id,
            ServiceDetectionSettings.service_name == SERVICE,
        )
    )
    if found is not None:
        await db.refresh(found)
    return found


async def test_a_member_snoozes_a_service_for_up_to_four_hours_and_ends_it(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """D25, FR22."""
    until = _ahead(hours=2)

    snoozed = await _call(
        db,
        clickhouse,
        acme,
        acme.member,
        "POST",
        _service("snooze"),
        {"project_id": str(acme.project_id), "until": until},
    )

    assert snoozed.status_code == 200, snoozed.text
    assert snoozed.json()["snoozed_until"] is not None
    stored = await _settings(db, acme)
    assert stored is not None and stored.snoozed_by == acme.member.id
    [entry] = await _audit(db, acme, "service.snoozed")
    assert entry.actor_user_id == acme.member.id
    assert entry.target == f"project:{acme.project_id}"
    assert entry.detail is not None and entry.detail["service"] == SERVICE

    ended = await _call(
        db,
        clickhouse,
        acme,
        acme.member,
        "POST",
        _service("snooze"),
        {"project_id": str(acme.project_id), "until": None},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["snoozed_until"] is None
    stored = await _settings(db, acme)
    assert stored is not None and stored.snoozed_until is None


@pytest.mark.parametrize("until", [{"hours": 4, "minutes": 5}, {"minutes": -1}])
async def test_a_snooze_past_four_hours_or_already_over_is_refused(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team, until: dict[str, float]
) -> None:
    response = await _call(
        db,
        clickhouse,
        acme,
        acme.member,
        "POST",
        _service("snooze"),
        {"project_id": str(acme.project_id), "until": _ahead(**until)},
    )

    assert response.status_code == 422, response.text
    assert await _settings(db, acme) is None


async def test_an_admin_mutes_a_service_for_up_to_thirty_days(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """BR9, FR22."""
    response = await _call(
        db,
        clickhouse,
        acme,
        acme.admin,
        "PUT",
        _service("mute"),
        {"project_id": str(acme.project_id), "until": _ahead(days=29)},
    )
    too_long = await _call(
        db,
        clickhouse,
        acme,
        acme.admin,
        "PUT",
        _service("mute"),
        {"project_id": str(acme.project_id), "until": _ahead(days=31)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["muted_until"] is not None
    assert too_long.status_code == 422, too_long.text
    stored = await _settings(db, acme)
    assert stored is not None and stored.muted_by == acme.admin.id
    [entry] = await _audit(db, acme, "service.muted")
    assert entry.actor_user_id == acme.admin.id


async def test_a_member_may_not_mute_and_a_viewer_may_not_snooze(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """AC34."""
    body = {"project_id": str(acme.project_id), "until": _ahead(hours=1)}
    muted = await _call(db, clickhouse, acme, acme.member, "PUT", _service("mute"), body)
    snoozed = await _call(db, clickhouse, acme, acme.viewer, "POST", _service("snooze"), body)

    assert muted.status_code == 403, muted.text
    assert snoozed.status_code == 403, snoozed.text
    assert await _settings(db, acme) is None


async def test_a_service_detection_has_never_seen_is_not_found(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    response = await _call(
        db,
        clickhouse,
        acme,
        acme.admin,
        "PUT",
        _service("mute", "nobody-knows-me"),
        {"project_id": str(acme.project_id), "until": _ahead(hours=1)},
    )

    assert response.status_code == 404, response.text


async def test_another_organization_s_project_cannot_be_snoozed(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team, globex: Team
) -> None:
    response = await _call(
        db,
        clickhouse,
        acme,
        acme.owner,
        "POST",
        _service("snooze"),
        {"project_id": str(globex.project_id), "until": _ahead(hours=1)},
    )

    assert response.status_code == 404, response.text


# ------------------------------------------------------- project settings


def _detection(team: Team) -> str:
    return f"/projects/{team.project_id}/detection"


async def test_a_project_s_detection_settings_are_read_by_anyone_who_reads_it(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    for caller in (acme.viewer, None):
        response = await _call(db, clickhouse, acme, caller, "GET", _detection(acme))
        assert response.status_code == 200, response.text
        assert response.json() == {"sensitivity": "normal", "timezone": "UTC"}


async def test_an_admin_changes_the_sensitivity_and_time_zone(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """FR18, FR24, AC30, AC35: the normal is rebuilt for the new window and clock,
    with no learning period: the history is the same history."""
    response = await _call(
        db,
        clickhouse,
        acme,
        acme.admin,
        "PUT",
        _detection(acme),
        {"sensitivity": "low", "timezone": "Asia/Kolkata"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"sensitivity": "low", "timezone": "Asia/Kolkata"}
    project = await db.get(Project, acme.project_id)
    assert project is not None
    await db.refresh(project)
    assert (project.detection_sensitivity, project.timezone) == ("low", "Asia/Kolkata")
    [entry] = await _audit(db, acme, "project.detection_changed")
    assert entry.detail == {
        "sensitivity": {"from": "normal", "to": "low"},
        "timezone": {"from": "UTC", "to": "Asia/Kolkata"},
    }
    assert len(await _queued_baselines(db, acme)) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"sensitivity": "extreme", "timezone": "UTC"},
        {"sensitivity": "low", "timezone": "Mars/Olympus_Mons"},
        {"sensitivity": "low", "timezone": ""},
    ],
)
async def test_an_unknown_sensitivity_or_zone_is_refused(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team, body: dict[str, str]
) -> None:
    response = await _call(db, clickhouse, acme, acme.admin, "PUT", _detection(acme), body)

    assert response.status_code == 422, response.text
    project = await db.get(Project, acme.project_id)
    assert project is not None and project.detection_sensitivity == "normal"


async def test_a_member_may_not_change_the_sensitivity(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    """AC34."""
    response = await _call(
        db,
        clickhouse,
        acme,
        acme.member,
        "PUT",
        _detection(acme),
        {"sensitivity": "high", "timezone": "UTC"},
    )

    assert response.status_code == 403, response.text


async def test_a_token_reads_only_the_settings_of_the_project_it_may_read(
    db: AsyncSession, clickhouse: AsyncClient, acme: Team
) -> None:
    other = Project(org_id=acme.org_id, slug=f"staging-{uuid.uuid4().hex[:8]}", name="Staging")
    db.add(other)
    await db.flush()

    response = await _call(db, clickhouse, acme, None, "GET", f"/projects/{other.id}/detection")

    assert response.status_code == 404, response.text
