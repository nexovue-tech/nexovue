"""Rows and clients rung 2b's API suites share (F5 plan T2b.7, T2b.11): an organization
with a consented AI connection, a finding, its explanation holding F3's `model_input` and
real names apart, general rows, and the app with a person's identity. Each suite passes its
own `now`; every row is inside the suite's rolled-back transaction.
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
from nexovue_platform.ai.connection import consent_fingerprint
from nexovue_platform.checks.sealing import generate_key
from nexovue_platform.evidence.model_facts import SCHEMA_VERSION
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.dependencies import Identity, get_current_identity
from nexovue_api.auth.sessions import Impersonation
from nexovue_api.data.postgres.models import AIConnection, FindingExplanation, FindingFix
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.settings import API_PREFIX, Settings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
BASE_URL: Final = "https://provider.example"
# AI on a self-hosted install with a key configured; causes released for the founder's judging.
AI_ON: Final = {
    "NEXOVUE_DEPLOYMENT": "self-hosted",
    "AI_SECRET_KEY": generate_key(),
    "SHOW_HELD_CAUSES": "true",
}
REAL_NAME: Final = "checkout-api"
MODEL_INPUT: Final[dict[str, Any]] = {
    "finding": {"subject": "service_1", "signal": "failing", "severity": "warning"},
    "facts": [
        {"id": "e1", "kind": "error_group", "subject": "service_1", "error": "error_1"},
        {"id": "e2", "kind": "deploy", "subject": "service_1", "version": "version_1"},
    ],
    "likely_cause": {"evidence_id": "e2", "strength": "likely"},
    "schema": 1,
}
NO_CAUSE_INPUT: Final[dict[str, Any]] = {
    "finding": MODEL_INPUT["finding"],
    "facts": [{"id": "e2", "kind": "deploy", "subject": "service_1", "version": "version_1"}],
    "schema": 1,
}
CAUSE: Final = {"evidence_id": "deploy:d-1", "rule": "signed_deploy", "strength": "likely"}
EVIDENCE: Final = {
    "items": [
        {"id": "error_group:g-1", "kind": "error_group", "values": {"group_id": "g-1"}},
        {"id": "deploy:d-1", "kind": "deploy", "values": {}},
    ]
}
ALIASES: Final = {
    "names": {"service_1": REAL_NAME, "error_1": "TimeoutError", "version_1": "v2.1"},
    "facts": {"e1": "error_group:g-1", "e2": "deploy:d-1"},
}
STORED: Final[dict[str, Any]] = {
    "outcome": "suggestion",
    "abstain_reason": None,
    "summary": "Calls from service_1 began failing after version_1.",
    "steps": ["Wrap the outbound call in a retry."],
    "example": {"language": "python", "code": "import time\n\ntime.sleep(1)\n"},
    "evidence_ids": ["e2"],
}


def reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


@pytest.fixture
def now() -> datetime:
    """The suite's own moment, taken once per test; the routes read the real clock."""
    return datetime.now(UTC).replace(microsecond=0)


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


async def tenant(db: AsyncSession) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name="Acme",
        email=f"acme-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )


def person(tenant: ProvisionedTenant, role: str = "member") -> Identity:
    tenant.user.role = role
    return Identity(user=tenant.user, organization=tenant.organization)


def support_view(tenant: ProvisionedTenant, now: datetime) -> Identity:
    tenant.user.role = "owner"
    return Identity(
        user=tenant.user,
        organization=tenant.organization,
        impersonation=Impersonation(
            grant_id=str(uuid.uuid4()),
            impersonation_id=str(uuid.uuid4()),
            operator_label="support",
            reason="a customer asked",
            until=now + timedelta(hours=1),
        ),
    )


def client(db: AsyncSession, identity: Identity, **settings: Any) -> httpx.AsyncClient:
    from nexovue_api.main import create_app

    configured = Settings(**{**TEST_ENVIRONMENT, **AI_ON, **settings})
    app = create_app(configured)

    async def db_override() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: configured
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_current_identity] = lambda: identity
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def read(db: AsyncSession, identity: Identity, finding: FindingRow, **settings: Any) -> Any:
    async with client(db, identity, **settings) as http:
        response = await http.get(f"{API_PREFIX}/findings/{finding.id}/fixes/general")
    assert response.status_code == 200, response.text
    return response.json()


async def ask(
    db: AsyncSession, identity: Identity, finding: FindingRow, language: str = "python"
) -> httpx.Response:
    async with client(db, identity) as http:
        return await http.post(
            f"{API_PREFIX}/findings/{finding.id}/fixes/general", json={"language": language}
        )


async def connect(db: AsyncSession, tenant: ProvisionedTenant, *, enabled: bool = True) -> None:
    db.add(
        AIConnection(
            org_id=tenant.organization.id,
            wire_format="openai_chat",
            base_url=BASE_URL,
            model="llama3.1:8b",
            call_seconds=60,
            monthly_token_limit=100_000,
            enabled=enabled,
            consented_at=datetime.now(UTC),
            consented_base_url=BASE_URL,
            consented_fingerprint=consent_fingerprint(
                wire_format="openai_chat", base_url=BASE_URL, region=None, key_id=None
            ),
            consented_schema=SCHEMA_VERSION,
        )
    )
    await db.flush()


async def finding(
    db: AsyncSession, tenant: ProvisionedTenant, now: datetime, **fields: Any
) -> FindingRow:
    row = FindingRow(
        **{
            "org_id": tenant.organization.id,
            "project_id": tenant.project.id,
            "scope": "service",
            "service_name": "checkout",
            "severity": "warning",
            "state": "open",
            "title": "checkout is failing",
            "signals": [],
            "started_at": now - timedelta(minutes=30),
            **fields,
        }
    )
    db.add(row)
    await db.flush()
    return row


async def version(
    db: AsyncSession,
    finding: FindingRow,
    now: datetime,
    *,
    number: int = 1,
    model_input: dict[str, Any] | None = None,
    cause: dict[str, Any] | None = CAUSE,
) -> FindingExplanation:
    row = FindingExplanation(
        org_id=finding.org_id,
        project_id=finding.project_id,
        finding_id=finding.id,
        version=number,
        read_at=now - timedelta(minutes=20),
        fingerprint=str(number) * 64,
        evidence=EVIDENCE,
        aliases=ALIASES,
        likely_cause=cause,
        summary={"header": None, "card": None},
        model_input=model_input or MODEL_INPUT,
    )
    db.add(row)
    await db.flush()
    return row


async def fix(
    db: AsyncSession,
    finding: FindingRow,
    now: datetime,
    *,
    state: str = "proposed",
    suggestion: dict[str, Any] | None = STORED,
    reason: str | None = None,
    version: int = 1,
    minutes_ago: int = 5,
) -> FindingFix:
    row = FindingFix(
        org_id=finding.org_id,
        project_id=finding.project_id,
        finding_id=finding.id,
        kind="general",
        explanation_version=version,
        state=state,
        state_changed_at=now - timedelta(minutes=minutes_ago),
        general_language="python",
        general_suggestion=suggestion if state == "proposed" else None,
        evidence_ids=["e2"] if state == "proposed" else None,
        reason=reason,
        tokens_used=460,
        requested_at=now - timedelta(minutes=minutes_ago),
    )
    db.add(row)
    await db.flush()
    return row


async def ready(db: AsyncSession, now: datetime) -> tuple[ProvisionedTenant, FindingRow]:
    acme = await tenant(db)
    await connect(db, acme)
    made = await finding(db, acme, now)
    await version(db, made, now)
    return acme, made
