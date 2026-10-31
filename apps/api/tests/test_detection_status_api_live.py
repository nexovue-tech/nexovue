"""Detection's status routes, through HTTP, against real Postgres (F6, T3.4).

`GET /projects/{id}/detection/status` and `GET /services/{name}/detection` are on
the read surface (ADR-017), so each is called both as a signed-in person and as
an API token. The rows the scheduler would write are written here directly, in
its shapes (`detection/project_job.py`), and rolled back after each test.

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

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from live import require_stack
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.dependencies import Identity, Reader, get_current_reader
from nexovue_api.data.postgres.models import (
    DetectionProjectRun,
    DetectionState,
    Finding,
    Project,
    ServiceDetectionSettings,
    ServiceLastSeen,
)
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.settings import API_PREFIX, Settings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)


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
    reachable=_reachable(POSTGRES_DSN, 5432),
    suite="apps/api/tests/test_detection_status_api_live.py",
    remedy="Postgres is not reachable — run `just dev`",
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


async def _tenant(db: AsyncSession, name: str) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name=name,
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )


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


async def _get(db: AsyncSession, reader: Reader, path: str, **params: Any) -> httpx.Response:
    from nexovue_api.main import create_app

    settings = Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]
    app = create_app(settings)

    async def db_override() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_current_reader] = lambda: reader
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(f"{API_PREFIX}{path}", params=params)


def _status_path(project_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/detection/status"


def _ago(**delta: float) -> datetime:
    return datetime.now(UTC) - timedelta(**delta)


def _slower(verdict: str, learning_until: str | None = None) -> dict[str, Any]:
    """`detection_state.signals` as the project job writes it after a run."""
    return {"slower": {"verdict": verdict, "clear_minutes": 0, "learning_until": learning_until}}


async def _detection(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    *,
    services: dict[str, dict[str, Any]],
    last_run: datetime,
    seen: datetime | None = None,
    project_id: uuid.UUID | None = None,
    judged: dict[str, datetime] | None = None,
) -> None:
    """A project the scheduler has judged: its services, its run, and its traffic.

    Each service's last judged minute is 4 minutes back, the normal lag, unless
    `judged` names another.
    """
    on_time = _ago(minutes=4).replace(second=0, microsecond=0)
    org_id, project = tenant.organization.id, project_id or tenant.project.id
    for name, signals in services.items():
        db.add(
            DetectionState(
                org_id=org_id,
                project_id=project,
                service_name=name,
                last_judged_minute=(judged or {}).get(name, on_time),
                signals=signals,
            )
        )
        db.add(
            ServiceLastSeen(
                org_id=org_id,
                project_id=project,
                service_name=name,
                last_seen_at=seen or _ago(seconds=10),
            )
        )
    db.add(DetectionProjectRun(org_id=org_id, project_id=project, last_completed_at=last_run))
    await db.flush()


# ------------------------------------------------------------ not running (AC26, AC43)


async def test_a_project_whose_job_last_completed_over_3_minutes_ago_is_not_running(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC26: the scheduler stopped 5 minutes ago while the project had traffic."""
    stopped = _ago(minutes=5)
    await _detection(db, acme, services={"checkout": _slower("normal")}, last_run=stopped)

    for reader in (_callers(acme).person, _callers(acme).token):
        response = await _get(db, reader, _status_path(acme.project.id))
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "not_running"
        assert datetime.fromisoformat(body["not_running_since"]) == stopped


async def test_a_stopped_job_shows_on_the_service_too(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    await _detection(db, acme, services={"checkout": _slower("normal")}, last_run=_ago(minutes=5))

    response = await _get(
        db,
        _callers(acme).person,
        "/services/checkout/detection",
        project_id=acme.project.id,
    )

    assert response.status_code == 200
    assert response.json()["state"] == "not_running"


@pytest.mark.parametrize("seconds_ago", [5, 60, 170])
async def test_a_healthy_project_judged_on_time_never_reads_not_running(
    db: AsyncSession, acme: ProvisionedTenant, seconds_ago: int
) -> None:
    """AC43: freshness is the job's last run, never `last_judged_minute`, which trails
    the clock by minutes by design (design 2.1, item 2)."""
    await _detection(
        db,
        acme,
        services={"checkout": _slower("normal"), "search": _slower("elevated")},
        last_run=_ago(seconds=seconds_ago),
    )

    body = (await _get(db, _callers(acme).person, _status_path(acme.project.id))).json()

    assert body["state"] == "judging"
    assert body["not_running_since"] is None
    assert body["services_judged"] == 2
    assert body["services_learning"] == 0
    assert body["services_delayed"] == 0, "a minute judged on time is never delayed"
    assert body["delayed_since"] is None


async def test_a_quiet_project_the_scheduler_does_not_queue_is_not_called_stopped(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """No traffic in 24 hours and no finding open: the coordinator queues no job,
    so an old run is detection idling, not detection stopped."""
    await _detection(
        db,
        acme,
        services={"checkout": _slower("normal")},
        last_run=_ago(days=2),
        seen=_ago(days=2),
    )

    body = (await _get(db, _callers(acme).person, _status_path(acme.project.id))).json()

    assert body["state"] == "judging"
    assert body["not_running_since"] is None


async def test_an_open_finding_keeps_a_quiet_project_queued_so_an_old_run_is_not_running(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    await _detection(
        db,
        acme,
        services={"checkout": _slower("unusual")},
        last_run=_ago(minutes=10),
        seen=_ago(days=2),
    )
    await _finding(db, acme, started=_ago(minutes=30))

    body = (await _get(db, _callers(acme).person, _status_path(acme.project.id))).json()

    assert body["state"] == "not_running"
    assert body["open_findings"] == 1


# ------------------------------------------------------------- data delayed (BR5)


def _minute(minutes_ago: int) -> datetime:
    return _ago(minutes=minutes_ago).replace(second=0, microsecond=0)


async def test_a_fresh_run_whose_judging_stands_at_an_old_minute_reads_data_delayed(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """The job runs, but the pipeline settles no newer minute for it to judge."""
    stuck = _minute(12)
    await _detection(
        db,
        acme,
        services={"checkout": _slower("normal"), "search": _slower("normal")},
        last_run=_ago(seconds=20),
        judged={"checkout": stuck, "search": stuck - timedelta(minutes=1)},
    )

    for reader in (_callers(acme).person, _callers(acme).token):
        body = (await _get(db, reader, _status_path(acme.project.id))).json()
        assert body["state"] == "data_delayed"
        assert datetime.fromisoformat(body["delayed_since"]) == stuck + timedelta(minutes=3)
        assert body["services_delayed"] == 2
        assert body["not_running_since"] is None
        service = (
            await _get(db, reader, "/services/checkout/detection", project_id=acme.project.id)
        ).json()
        assert service["state"] == "data_delayed"


async def test_one_service_behind_is_delayed_while_the_project_judges_on_time(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    await _detection(
        db,
        acme,
        services={"checkout": _slower("normal"), "search": _slower("normal")},
        last_run=_ago(seconds=20),
        judged={"search": _minute(12)},
    )

    body = (await _get(db, _callers(acme).person, _status_path(acme.project.id))).json()
    search = (
        await _get(
            db, _callers(acme).person, "/services/search/detection", project_id=acme.project.id
        )
    ).json()
    checkout = (
        await _get(
            db, _callers(acme).person, "/services/checkout/detection", project_id=acme.project.id
        )
    ).json()

    assert body["state"] == "judging"
    assert body["services_delayed"] == 1
    assert body["delayed_since"] is None
    assert search["state"] == "data_delayed"
    assert checkout["state"] == "judged"


async def test_a_stale_run_reads_not_running_never_data_delayed(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """A stopped job outranks the old minute it left behind."""
    await _detection(
        db,
        acme,
        services={"checkout": _slower("normal")},
        last_run=_ago(minutes=8),
        judged={"checkout": _minute(12)},
    )

    body = (await _get(db, _callers(acme).person, _status_path(acme.project.id))).json()
    service = (
        await _get(
            db, _callers(acme).person, "/services/checkout/detection", project_id=acme.project.id
        )
    ).json()

    assert body["state"] == "not_running"
    assert body["services_delayed"] == 0
    assert body["delayed_since"] is None
    assert service["state"] == "not_running"


# ---------------------------------------------------------------- learning (AC42)


async def test_a_project_whose_every_service_is_learning_says_when_judging_starts(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC42: never "all systems normal"; learning, and the first date judging starts."""
    await _detection(
        db,
        acme,
        services={
            "checkout": _slower("learning", "2026-11-04"),
            "search": _slower("learning", "2026-11-03"),
        },
        last_run=_ago(seconds=20),
    )

    for reader in (_callers(acme).person, _callers(acme).token):
        body = (await _get(db, reader, _status_path(acme.project.id))).json()
        assert body["state"] == "learning"
        assert body["earliest_judging_date"] == "2026-11-03"
        assert body["services_learning"] == 2
        assert body["services_judged"] == 0


async def test_some_services_learning_are_counted_beside_the_judged_ones(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    await _detection(
        db,
        acme,
        services={
            "checkout": _slower("normal"),
            "search": _slower("learning", "2026-11-03"),
            "quiet": _slower("too_little_traffic"),
            "fresh": _slower("no_baseline"),
        },
        last_run=_ago(seconds=20),
    )

    body = (await _get(db, _callers(acme).person, _status_path(acme.project.id))).json()

    assert body["state"] == "judging"
    assert body["services_total"] == 4
    assert body["services_judged"] == 2
    assert body["services_learning"] == 2
    assert body["earliest_judging_date"] == "2026-11-03"


async def test_a_project_detection_has_seen_nothing_of_has_no_services(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    body = (await _get(db, _callers(acme).person, _status_path(acme.project.id))).json()

    assert body == {
        "state": "no_services",
        "services_total": 0,
        "services_learning": 0,
        "services_judged": 0,
        "services_delayed": 0,
        "services_muted": 0,
        "earliest_judging_date": None,
        "not_running_since": None,
        "delayed_since": None,
        "open_findings": 0,
    }


async def test_muted_services_and_open_findings_are_counted(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    await _detection(
        db,
        acme,
        services={"checkout": _slower("unusual"), "search": _slower("normal")},
        last_run=_ago(seconds=20),
    )
    _settings(db, acme, "checkout", muted_until=_ago(hours=-2))
    _settings(db, acme, "search", muted_until=_ago(hours=2))
    await _finding(db, acme, started=_ago(minutes=30), muted=True)
    await _finding(db, acme, service_name="search", started=_ago(hours=3), closed=_ago(hours=2))

    body = (await _get(db, _callers(acme).person, _status_path(acme.project.id))).json()

    assert body["services_muted"] == 1
    assert body["open_findings"] == 1


# ---------------------------------------------------------------- one service


async def test_a_service_reads_each_signal_s_phase_its_controls_and_its_findings_in_range(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    await _detection(
        db, acme, services={"checkout": _slower("learning", "2026-11-03")}, last_run=_ago(seconds=5)
    )
    snoozed = _ago(hours=-1)
    _settings(db, acme, "checkout", snoozed_until=snoozed, muted_until=_ago(hours=1))
    inside = await _finding(db, acme, started=_ago(hours=2), closed=_ago(minutes=90))
    still_open = await _finding(db, acme, started=_ago(minutes=20), severity="critical")
    await _finding(db, acme, started=_ago(hours=30), closed=_ago(hours=29))
    await _finding(db, acme, service_name="search", started=_ago(minutes=20))

    for reader in (_callers(acme).person, _callers(acme).token):
        response = await _get(
            db,
            reader,
            "/services/checkout/detection",
            project_id=acme.project.id,
            **{"from": _ago(hours=6).isoformat(), "to": _ago(seconds=0).isoformat()},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "learning"
        assert body["signals"] == [
            {
                "signal": "slower",
                "state": "learning",
                "learning_until": "2026-11-03",
                "reason": None,
            },
            {
                "signal": "failing",
                "state": "not_judged",
                "learning_until": None,
                "reason": "not_recorded",
            },
            {
                "signal": "traffic",
                "state": "not_judged",
                "learning_until": None,
                "reason": "not_recorded",
            },
        ]
        assert body["muted_until"] is None, "a mute that has ended is not shown"
        assert datetime.fromisoformat(body["snoozed_until"]) == snoozed
        assert [marker["id"] for marker in body["findings"]] == [str(inside.id), str(still_open.id)]
        assert body["findings"][1]["closed_at"] is None
        assert body["findings"][1]["severity"] == "critical"
        assert body["truncated"] is False


@pytest.mark.parametrize(
    ("signals", "state"),
    [
        (_slower("normal"), "judged"),
        (_slower("too_little_traffic"), "judged"),
        (_slower("no_baseline"), "learning"),
    ],
)
async def test_a_service_past_learning_is_judged_even_when_too_quiet_this_minute(
    db: AsyncSession, acme: ProvisionedTenant, signals: dict[str, Any], state: str
) -> None:
    await _detection(db, acme, services={"checkout": signals}, last_run=_ago(seconds=5))

    body = (
        await _get(
            db, _callers(acme).person, "/services/checkout/detection", project_id=acme.project.id
        )
    ).json()

    assert body["state"] == state


async def test_a_muted_service_reads_muted(db: AsyncSession, acme: ProvisionedTenant) -> None:
    await _detection(db, acme, services={"checkout": _slower("normal")}, last_run=_ago(seconds=5))
    muted_until = _ago(days=-3)
    _settings(db, acme, "checkout", muted_until=muted_until)
    await db.flush()

    body = (
        await _get(
            db, _callers(acme).person, "/services/checkout/detection", project_id=acme.project.id
        )
    ).json()

    assert body["state"] == "muted"
    assert datetime.fromisoformat(body["muted_until"]) == muted_until


async def test_a_service_detection_has_never_seen_reads_no_detection_yet(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    body = (
        await _get(
            db, _callers(acme).person, "/services/unseen/detection", project_id=acme.project.id
        )
    ).json()

    assert body["state"] == "no_detection_yet"
    assert {signal["state"] for signal in body["signals"]} == {"not_judged"}
    assert body["findings"] == []


# ------------------------------------------------------------------- scope


@pytest.mark.parametrize("route", ["status", "service"])
async def test_another_organizations_project_is_refused(
    db: AsyncSession, acme: ProvisionedTenant, globex: ProvisionedTenant, route: str
) -> None:
    await _detection(db, globex, services={"checkout": _slower("normal")}, last_run=_ago(minutes=9))

    response = await _read(db, _callers(acme).person, route, globex.project.id)

    assert response.status_code == 404


@pytest.mark.parametrize("route", ["status", "service"])
async def test_a_token_asking_about_a_project_outside_its_scope_is_refused(
    db: AsyncSession, acme: ProvisionedTenant, staging: Project, route: str
) -> None:
    await _detection(
        db,
        acme,
        services={"checkout": _slower("normal")},
        last_run=_ago(minutes=9),
        project_id=staging.id,
    )

    response = await _read(db, _callers(acme).token, route, staging.id)

    assert response.status_code == 404


async def test_one_project_s_detection_never_answers_for_another(
    db: AsyncSession, acme: ProvisionedTenant, staging: Project
) -> None:
    """AC28: a stopped staging project leaves the first project's status alone."""
    await _detection(db, acme, services={"checkout": _slower("normal")}, last_run=_ago(seconds=5))
    await _detection(
        db,
        acme,
        services={"checkout": _slower("learning", "2026-11-09")},
        last_run=_ago(minutes=9),
        project_id=staging.id,
    )

    body = (await _get(db, _callers(acme).person, _status_path(acme.project.id))).json()

    assert body["state"] == "judging"
    assert body["services_total"] == 1
    assert body["earliest_judging_date"] is None


async def test_a_window_that_ends_before_it_starts_is_refused(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    response = await _get(
        db,
        _callers(acme).person,
        "/services/checkout/detection",
        project_id=acme.project.id,
        **{"from": _ago(hours=1).isoformat(), "to": _ago(hours=2).isoformat()},
    )

    assert response.status_code == 422


# ----------------------------------------------------------------- helpers


async def _read(
    db: AsyncSession, reader: Reader, route: str, project_id: uuid.UUID
) -> httpx.Response:
    if route == "status":
        return await _get(db, reader, _status_path(project_id))
    return await _get(db, reader, "/services/checkout/detection", project_id=project_id)


def _settings(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    service_name: str,
    *,
    muted_until: datetime | None = None,
    snoozed_until: datetime | None = None,
) -> None:
    db.add(
        ServiceDetectionSettings(
            org_id=tenant.organization.id,
            project_id=tenant.project.id,
            service_name=service_name,
            muted_until=muted_until,
            snoozed_until=snoozed_until,
        )
    )


async def _finding(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    *,
    started: datetime,
    service_name: str = "checkout",
    closed: datetime | None = None,
    severity: str = "warning",
    muted: bool = False,
) -> Finding:
    row = Finding(
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        scope="service",
        service_name=service_name,
        severity=severity,
        state="open" if closed is None else "closed",
        closed_reason=None if closed is None else "recovered",
        closed_at=closed,
        muted=muted,
        title=f"{service_name} is slower than normal",
        signals=[],
        started_at=started,
    )
    db.add(row)
    await db.flush()
    return row
