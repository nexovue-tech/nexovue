"""`GET /findings/{id}/explanation` and `POST .../read-again`, against real Postgres
(F3 plan T1.10, T1.14; AC23, AC80, AC92, FR47, FR3).

A token reads the facts in its own project and gets the same 404 as a missing finding
elsewhere; a token or a viewer may not ask for a read; a member may, once a minute. A
stored cause stays held until its bar is met, unless the non-production override is set.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
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
from nexovue_platform.evidence import GATHER_EVIDENCE_TASK
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.dependencies import (
    Identity,
    Reader,
    get_current_identity,
    get_current_reader,
    get_tenant_repository,
)
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.data.postgres.models import FindingExplanation, Job, Project
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.settings import API_PREFIX, Settings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_findings_explanation_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

CAUSE: Final = {
    "evidence_id": "deploy:d-1",
    "rule": "signed_deploy",
    "strength": "likely",
    "basis": "verified",
    "supported_by": [],
    "same_minute": False,
}
HEADER: Final = [{"text": "Likely: the deploy of "}, {"name": "checkout"}, {"text": " v2.1"}]
CARD: Final = [{"text": "Likely: deploy of "}, {"name": "checkout"}]


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


def _person(tenant: ProvisionedTenant, role: str = "owner") -> Reader:
    tenant.user.role = role
    return Reader(
        org_id=tenant.organization.id,
        identity=Identity(user=tenant.user, organization=tenant.organization),
    )


def _token(tenant: ProvisionedTenant) -> Reader:
    return Reader(
        org_id=tenant.organization.id, project_scope=tenant.project.id, token_id=uuid.uuid4()
    )


def _client(db: AsyncSession, reader: Reader, **settings: Any) -> httpx.AsyncClient:
    from nexovue_api.main import create_app

    configured = Settings(**{**TEST_ENVIRONMENT, **settings})
    app = create_app(configured)

    async def db_override() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: configured
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_current_reader] = lambda: reader
    app.dependency_overrides[get_tenant_repository] = lambda: _NoTelemetry()
    if reader.identity is not None:
        app.dependency_overrides[get_current_identity] = lambda: reader.identity
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _get(db: AsyncSession, reader: Reader, path: str, **settings: Any) -> httpx.Response:
    async with _client(db, reader, **settings) as client:
        return await client.get(f"{API_PREFIX}{path}")


async def _post(db: AsyncSession, reader: Reader, path: str) -> httpx.Response:
    async with _client(db, reader) as client:
        return await client.post(f"{API_PREFIX}{path}")


async def _finding(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    project_id: uuid.UUID | None = None,
    service: str = "checkout",
) -> FindingRow:
    row = FindingRow(
        org_id=tenant.organization.id,
        project_id=project_id or tenant.project.id,
        scope="service",
        service_name=service,
        severity="warning",
        state="open",
        title="checkout is slower than normal",
        signals=[],
        started_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    db.add(row)
    await db.flush()
    return row


async def _version(
    db: AsyncSession,
    finding: FindingRow,
    number: int,
    *,
    cause: dict[str, Any] | None = None,
    **fields: Any,
) -> None:
    db.add(
        FindingExplanation(
            org_id=finding.org_id,
            project_id=finding.project_id,
            finding_id=finding.id,
            version=number,
            fingerprint=str(number) * 64,
            evidence={
                "items": [
                    {
                        "id": "deploy:d-1",
                        "kind": "deploy",
                        "basis": "verified",
                        "sentence": [{"text": "Deploy of "}, {"name": "checkout"}, {"text": "."}],
                        "values": {"at": "2026-11-09T10:00:00+00:00"},
                    },
                    {
                        "id": "absence:could_not_read:errors",
                        "kind": "absence",
                        "basis": None,
                        "sentence": [{"text": "Could not read recent errors this time."}],
                        "values": {"reason": "could_not_read", "read": "errors"},
                    },
                ]
            },
            aliases={"names": {"service_1": "checkout"}, "facts": {}},
            likely_cause=cause,
            summary={"header": HEADER, "card": CARD} if cause else {"header": None, "card": None},
            model_input={"schema": 1},
            reads_failed=1,
            **fields,
        )
    )
    await db.flush()


async def test_a_token_reads_the_facts_in_its_project_and_404_elsewhere(db: AsyncSession) -> None:
    """AC23: facts, never words; another project's finding is a 404 like a missing one."""
    acme = await _tenant(db)
    staging = Project(
        org_id=acme.organization.id, slug=f"staging-{uuid.uuid4().hex[:8]}", name="Staging"
    )
    db.add(staging)
    await db.flush()
    mine, elsewhere = await _finding(db, acme), await _finding(db, acme, staging.id)
    await _version(db, mine, 1)

    read = await _get(db, _token(acme), f"/findings/{mine.id}/explanation")
    refused = await _get(db, _token(acme), f"/findings/{elsewhere.id}/explanation")

    assert read.status_code == 200 and refused.status_code == 404
    assert _person(acme).is_person and not _token(acme).is_person
    body = read.json()
    assert body["wording"] is None and body["wording_state"] == "off"
    assert [fact["kind"] for fact in body["facts"]] == ["deploy", "absence"]
    assert body["facts"][1]["read"] == "errors" and body["reads_failed"] == 1


async def test_another_organizations_finding_is_not_found(db: AsyncSession) -> None:
    acme, globex = await _tenant(db), await _tenant(db)
    theirs = await _finding(db, globex)

    assert (await _get(db, _person(acme), f"/findings/{theirs.id}/explanation")).status_code == 404


async def test_a_cause_is_held_until_its_bar_is_met(db: AsyncSession) -> None:
    """AC92: stored, and named on neither the page nor the card, until released; the
    non-production override shows it, with the shared count (FR47)."""
    acme = await _tenant(db)
    first, second = await _finding(db, acme), await _finding(db, acme, service="payments")
    await _version(db, first, 1, cause=CAUSE)
    await _version(db, second, 1, cause=CAUSE)

    held = (await _get(db, _person(acme), f"/findings/{first.id}/explanation")).json()
    shown = (
        await _get(db, _person(acme), f"/findings/{first.id}/explanation", show_held_causes=True)
    ).json()
    feed = (await _get(db, _person(acme), f"/findings?project_id={acme.project.id}")).json()

    assert held["likely_cause"] is None and held["cause_held"] is True
    assert not any(fact["is_cause"] for fact in held["facts"])
    assert {row["likely_cause_sentence"] for row in feed["findings"]} == {None}
    assert _plain(shown["likely_cause"]["header"]) == HEADER and shown["cause_held"] is False
    assert shown["shared_cause_count"] == 2
    assert shown["facts"][0]["is_cause"] is True


async def test_an_earlier_version_reads_by_number_and_a_changed_cause_says_what_it_was(
    db: AsyncSession,
) -> None:
    """AC4, FR3: `?version=` reads an earlier version; the latest names its predecessor's cause."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)
    await _version(db, finding, 1, cause=CAUSE)
    await _version(
        db, finding, 2, cause={**CAUSE, "evidence_id": "dependency:x", "rule": "dependency_edge"}
    )

    first = (await _get(db, _person(acme), f"/findings/{finding.id}/explanation?version=1")).json()
    latest = (
        await _get(db, _person(acme), f"/findings/{finding.id}/explanation", show_held_causes=True)
    ).json()
    missing = await _get(db, _person(acme), f"/findings/{finding.id}/explanation?version=9")

    assert (first["version"], first["latest_version"]) == (1, 2)
    assert _plain(latest["previous_cause"]) == HEADER
    assert missing.status_code == 404


async def test_before_any_read_the_state_says_reading_or_not_read(db: AsyncSession) -> None:
    acme = await _tenant(db)
    path = f"/findings/{(await _finding(db, acme)).id}/explanation"

    unread = (await _get(db, _person(acme), path)).json()
    assert unread["state"] == "not_read" and unread["facts"] == []

    response = await _post(db, _person(acme, "member"), f"{path}/read-again")
    reading = (await _get(db, _person(acme), path)).json()

    assert response.status_code == 200 and reading["state"] == "reading"


async def test_reading_again_is_once_a_minute_with_the_time_said(db: AsyncSession) -> None:
    """AC80: the second ask inside a minute is 409 with `next_read_at`; one job is queued."""
    acme = await _tenant(db)
    org_id = acme.organization.id
    finding = await _finding(db, acme)
    path = f"/findings/{finding.id}/explanation"
    await _version(db, finding, 1)
    member = _person(acme, "member")

    first = await _post(db, member, f"{path}/read-again")
    second = await _post(db, member, f"{path}/read-again")
    page = (await _get(db, member, path)).json()

    assert first.status_code == 200
    assert second.status_code == 409 and "next_read_at" in second.json()
    assert page["next_read_at"] is not None
    jobs = (
        await db.scalars(
            select(Job.kwargs).where(Job.org_id == org_id, Job.task == GATHER_EVIDENCE_TASK)
        )
    ).all()
    assert [json.loads(json.dumps(kwargs))["reason"] for kwargs in jobs] == ["person"]


async def test_a_viewer_and_a_token_may_not_ask_for_a_read(db: AsyncSession) -> None:
    """AC23's role cases: a viewer is refused, and a token is refused as a token is anywhere."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)

    viewer = await _post(
        db, _person(acme, "viewer"), f"/findings/{finding.id}/explanation/read-again"
    )

    assert viewer.status_code == 403
    queued = await db.scalar(
        text("SELECT count(*) FROM jobs WHERE org_id = :org_id"), {"org_id": acme.organization.id}
    )
    assert queued == 0


async def test_the_finding_says_until_when_its_raw_data_is_kept(db: AsyncSession) -> None:
    """Design, *Links*: `raw_kept_until` from the project's raw retention."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)

    body = (await _get(db, _person(acme), f"/findings/{finding.id}")).json()

    kept = datetime.fromisoformat(body["raw_kept_until"])
    assert kept > finding.started_at


def _plain(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in segment.items() if value is not None} for segment in segments
    ]


class _NoTelemetry:
    """A repository with nothing in it: the page's readings are empty."""

    def __getattr__(self, name: str) -> Any:
        async def nothing(*_: object, **__: object) -> list[Any]:
            return []

        return nothing
