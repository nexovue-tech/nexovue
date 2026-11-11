"""`GET /findings/{id}/fixes` against real Postgres (plan T1.2, T1.3; spec FR1 to FR5,
AC34, AC35a, AC11a's hosted half).

Rung 1 reads F28's release before the finding and the version before that, on that
service only. The brief is built from F3's stored facts with AI off, names a cause only
once its rule is released, is absent for a finding closed as a false alarm, and carries
no repository line on the hosted service, where no section shows either. Every row is
this test's own, inside a transaction rolled back at the end; the clock is fixed.

This suite fails rather than skips when Postgres is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from live import require_stack
from nexovue_platform.fixes.brief import (
    NO_CODE_LOCATION,
    NO_LIKELY_CAUSE,
    NOTHING_READ,
    OPENING_LINE,
    PROJECT_FINDING,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.dependencies import Identity, get_current_identity
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.data.postgres.models import FindingExplanation, Release, ReleasePeriod
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.findings.fix_facts import LONGER_THAN_LOOKBACK
from nexovue_api.settings import API_PREFIX, Settings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

# A fixed clock: the finding started here, and every moment is set from it.
STARTED: Final = datetime(2026, 11, 11, 14, 2, tzinfo=UTC)
SIGNALS: Final = [
    {
        "signal": "slower",
        "key": "slower",
        "normal": 110.0,
        "current": 330.0,
        "worst": 412.0,
        "comparison": "Tuesdays around 15:00, last 3 weeks",
        "endpoint": "POST /pay",
    }
]
CAUSE: Final = {
    "evidence_id": "release:p-1",
    "rule": "signed_deploy",
    "strength": "likely",
    "basis": "verified",
    "supported_by": [],
    "same_minute": False,
}
SIGNED_CAUSE_LINE: Final = "Likely cause: a signed deploy of this service just before this."


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_routes_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
            )
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _tenant(db: AsyncSession) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name="Acme",
        email=f"acme-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )


def _person(tenant: ProvisionedTenant, role: str = "owner") -> Identity:
    tenant.user.role = role
    return Identity(user=tenant.user, organization=tenant.organization)


async def _get(
    db: AsyncSession,
    identity: Identity,
    finding: FindingRow,
    *,
    deployment: str = "self-hosted",
    base_url: str = "",
    **settings: Any,
) -> dict[str, Any]:
    from nexovue_api.main import create_app

    configured = Settings(
        **{
            **TEST_ENVIRONMENT,
            "NEXOVUE_DEPLOYMENT": deployment,
            "APP_BASE_URL": base_url,
            **settings,
        }
    )
    app = create_app(configured)

    async def db_override() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: configured
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_current_identity] = lambda: identity
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(f"{API_PREFIX}/findings/{finding.id}/fixes")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _finding(
    db: AsyncSession, tenant: ProvisionedTenant, *, service: str | None = "checkout", **fields: Any
) -> FindingRow:
    row = FindingRow(
        **{
            "org_id": tenant.organization.id,
            "project_id": tenant.project.id,
            "scope": "service" if service is not None else "project",
            "service_name": service,
            "services": None if service is not None else ["checkout", "payments"],
            "severity": "warning",
            "state": "open",
            "title": "checkout is slower than normal",
            "signals": SIGNALS,
            "started_at": STARTED,
            **fields,
        }
    )
    db.add(row)
    await db.flush()
    return row


async def _period(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    version: str,
    minutes_before: int,
    *,
    service: str = "checkout",
    last_state: str | None = "finished",
    release: Release | None = None,
    ended_minutes_before: int | None = None,
) -> Release:
    if release is None:
        release = Release(
            org_id=tenant.organization.id,
            project_id=tenant.project.id,
            service_name=service,
            version=version,
            first_reported_at=STARTED - timedelta(minutes=minutes_before),
            last_state=last_state,
        )
        db.add(release)
        await db.flush()
    db.add(
        ReleasePeriod(
            org_id=tenant.organization.id,
            release_id=release.id,
            project_id=tenant.project.id,
            service_name=service,
            started_at=STARTED - timedelta(minutes=minutes_before),
            start_basis="reported",
            ended_at=None
            if ended_minutes_before is None
            else STARTED - timedelta(minutes=ended_minutes_before),
        )
    )
    await db.flush()
    return release


async def _explanation(
    db: AsyncSession, finding: FindingRow, *, cause: dict[str, Any] | None = None
) -> None:
    db.add(
        FindingExplanation(
            org_id=finding.org_id,
            project_id=finding.project_id,
            finding_id=finding.id,
            version=1,
            fingerprint="1" * 64,
            evidence={
                "items": [
                    {
                        "id": "release:p-1",
                        "kind": "deploy",
                        "basis": "verified",
                        "role": "last_before",
                        "sentence": [{"text": "Last deploy: "}, {"name": "v2.1"}],
                        "values": {
                            "version": "v2.1",
                            "at": (STARTED - timedelta(minutes=4)).isoformat(),
                        },
                    },
                    {
                        "id": "error_group:ab12",
                        "kind": "error_group",
                        "basis": "telemetry_text",
                        "sentence": [{"text": "New error: "}, {"name": "TimeoutError"}],
                        "values": {"group_id": "ab12", "exception_type": "pay.TimeoutError"},
                    },
                ]
            },
            aliases={"names": {}, "facts": {}},
            likely_cause=cause,
            summary={"header": None, "card": None},
            wording_state="off",
        )
    )
    await db.flush()


def _lines(body: dict[str, Any]) -> list[str]:
    brief = body["brief"]
    assert brief is not None
    lines: list[str] = brief["text"].split("\n")
    return lines


def _sentences(body: dict[str, Any]) -> list[str]:
    return [
        "".join(segment["name"] or segment["text"] or "" for segment in sentence)
        for sentence in body["deploy_line"]["sentences"]
    ]


# ------------------------------------------------------------------- rung 1 (FR1)


async def test_rung_one_names_the_version_that_ran_before_the_release_before_this(
    db: AsyncSession,
) -> None:
    acme = await _tenant(db)
    v20 = await _period(db, acme, "v2.0", 300, last_state="failed", ended_minutes_before=45)
    await _period(db, acme, "v9.9", 100, service="payments")
    v21 = await _period(db, acme, "v2.1", 45)
    finding = await _finding(db, acme)

    body = await _get(db, _person(acme, "viewer"), finding)

    assert body["deploy_line"]["release_id"] == str(v21.id)
    assert body["deploy_line"]["earlier"] == {
        "release_id": str(v20.id),
        "version": "v2.0",
        "last_state": "failed",
    }
    assert _sentences(body) == [
        "v2.0 ran before it. CI's last report on it said it failed.",
        LONGER_THAN_LOOKBACK,
    ]


async def test_a_version_that_came_back_names_the_one_it_replaced(db: AsyncSession) -> None:
    acme = await _tenant(db)
    v20 = await _period(db, acme, "v2.0", 300, ended_minutes_before=20)
    await _period(db, acme, "v2.1", 20, ended_minutes_before=5)
    await _period(db, acme, "v2.0", 5, release=v20)
    finding = await _finding(db, acme)

    body = await _get(db, _person(acme), finding)

    assert body["deploy_line"]["release_id"] == str(v20.id)
    assert _sentences(body) == ["v2.1 ran before it."]


async def test_no_release_before_and_a_project_finding_add_nothing(db: AsyncSession) -> None:
    acme = await _tenant(db)
    quiet = await _finding(db, acme)
    project = await _finding(db, acme, service=None)

    assert (await _get(db, _person(acme), quiet))["deploy_line"] is None
    assert (await _get(db, _person(acme), project))["deploy_line"] is None


# ------------------------------------------------------------- rung 2 (FR2 to FR5)


async def test_the_brief_is_built_with_ai_off_from_f3s_stored_facts(db: AsyncSession) -> None:
    """AC35a: `wording_state = off` and no code location, each to its wording. The link
    is built from the install's own address, for a closed finding's fixed window."""
    acme = await _tenant(db)
    finding = await _finding(
        db,
        acme,
        state="closed",
        closed_reason="recovered",
        closed_at=STARTED + timedelta(minutes=30),
    )
    await _explanation(db, finding)

    body = await _get(db, _person(acme, "member"), finding, base_url="https://console.test")

    lines = _lines(body)
    assert lines[0] == OPENING_LINE
    assert "Endpoint (reported by telemetry): POST /pay" in lines
    assert NO_LIKELY_CAUSE in lines
    assert "Last deploy before this: v2.1, 4 minutes before." in lines
    assert (
        "Error type (reported by telemetry): TimeoutError. In the console: "
        "https://console.test/errors?group=ab12&from=2026-11-11T13%3A47%3A00Z"
        f"&to=2026-11-11T14%3A37%3A00Z&project={acme.project.id}&finding={finding.id}"
    ) in lines
    assert NO_CODE_LOCATION in lines and NOTHING_READ not in lines
    assert body["section"] is None


async def test_ac35a_with_no_explanation_row_the_brief_says_none_was_read(
    db: AsyncSession,
) -> None:
    acme = await _tenant(db)
    finding = await _finding(db, acme)

    lines = _lines(await _get(db, _person(acme), finding))

    assert lines[0] == OPENING_LINE and lines[-1] == NOTHING_READ
    assert "http" not in "\n".join(lines), "no link is built to a guessed host"


async def test_ac35a_a_project_finding_names_no_repository(db: AsyncSession) -> None:
    acme = await _tenant(db)
    finding = await _finding(db, acme, service=None)

    assert PROJECT_FINDING in _lines(await _get(db, _person(acme), finding))


async def test_a_held_cause_stays_out_of_the_brief_until_its_rule_is_released(
    db: AsyncSession,
) -> None:
    """F3 FR50: copying the brief must not reveal a cause the page holds."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)
    await _explanation(db, finding, cause=CAUSE)

    held = _lines(await _get(db, _person(acme), finding))
    shown = _lines(await _get(db, _person(acme), finding, show_held_causes=True))

    assert NO_LIKELY_CAUSE in held and SIGNED_CAUSE_LINE not in held
    assert SIGNED_CAUSE_LINE in shown


@pytest.mark.parametrize("reason", ["false_alarm", "service_gone"])
async def test_fr5_no_brief_for_a_finding_closed_as_not_a_problem(
    db: AsyncSession, reason: str
) -> None:
    acme = await _tenant(db)
    finding = await _finding(
        db, acme, state="closed", closed_reason=reason, closed_at=STARTED + timedelta(minutes=5)
    )

    assert (await _get(db, _person(acme), finding))["brief"] is None


async def test_the_hosted_service_shows_no_section_and_no_repository_line(
    db: AsyncSession,
) -> None:
    """AC11a's hosted half, and *What each buyer gets*: rung 2 without a repository line."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)

    body = await _get(db, _person(acme), finding, deployment="hosted")

    assert body["section"] is None
    assert NO_CODE_LOCATION not in _lines(body)


async def test_another_organizations_finding_is_not_found(db: AsyncSession) -> None:
    from nexovue_api.main import create_app

    acme, globex = await _tenant(db), await _tenant(db)
    theirs = await _finding(db, globex)
    configured = Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]
    app = create_app(configured)

    async def db_override() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: configured
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_current_identity] = lambda: _person(acme)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(f"{API_PREFIX}/findings/{theirs.id}/fixes")

    assert response.status_code == 404
