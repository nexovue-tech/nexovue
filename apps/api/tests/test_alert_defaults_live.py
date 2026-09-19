"""What an organization and a project are created with, against real Postgres.

The zero-configuration promise (US21.1, FR8, AC21): a new organization's first
check that goes down emails the owner, which is only true if signup leaves an
owners channel and a default rule sending to it. And the promise's limit (AC22):
a default rule somebody deleted stays deleted.

Every test runs in a transaction that is rolled back.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from nexovue_platform.alerts.model import DEFAULT_RULE_NAME, OWNERS_CHANNEL_NAME
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.data.postgres.models import (
    AlertRule,
    AlertRuleChannel,
    AuditLogEntry,
    Incident,
    NotificationChannel,
)
from nexovue_api.projects import service as projects
from nexovue_api.projects.schemas import ProjectCreateRequest
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
    suite="apps/api/tests/test_alert_defaults_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session in a transaction that is always rolled back."""
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
async def tenant(db: AsyncSession) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name="Defaults",
        email=f"defaults-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )


async def _rules(db: AsyncSession, project_id: uuid.UUID) -> list[AlertRule]:
    return list(await db.scalars(select(AlertRule).where(AlertRule.project_id == project_id)))


async def _channels_of(db: AsyncSession, rule: AlertRule) -> list[NotificationChannel]:
    statement = (
        select(NotificationChannel)
        .join(AlertRuleChannel, AlertRuleChannel.channel_id == NotificationChannel.id)
        .where(AlertRuleChannel.rule_id == rule.id)
    )
    return list(await db.scalars(statement))


async def test_signup_leaves_an_owners_channel_and_a_default_rule_sending_to_it(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    channels = list(
        await db.scalars(
            select(NotificationChannel).where(NotificationChannel.org_id == tenant.organization.id)
        )
    )
    assert [(channel.kind, channel.name) for channel in channels] == [
        ("owners", OWNERS_CHANNEL_NAME)
    ]

    rules = await _rules(db, tenant.project.id)
    assert len(rules) == 1
    rule = rules[0]
    assert rule.name == DEFAULT_RULE_NAME
    assert rule.is_default is True
    assert rule.enabled is True
    assert rule.condition == {
        "kind": "check_state",
        "check_ids": None,
        "states": {"down": "critical"},
    }
    assert [channel.id for channel in await _channels_of(db, rule)] == [channels[0].id]


async def test_both_defaults_are_audited_in_the_owners_name(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    actions = {
        entry.action: entry.actor_user_id
        for entry in await db.scalars(
            select(AuditLogEntry).where(AuditLogEntry.org_id == tenant.organization.id)
        )
    }
    assert actions["notification_channel.created"] == tenant.user.id
    assert actions["alert_rule.created"] == tenant.user.id


async def test_a_new_project_gets_its_own_default_rule_to_the_same_owners(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    created = await projects.create(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        request=ProjectCreateRequest(name="Staging"),
    )

    first = (await _rules(db, tenant.project.id))[0]
    second = await _rules(db, created.id)
    assert len(second) == 1
    assert second[0].id != first.id
    assert [channel.kind for channel in await _channels_of(db, second[0])] == ["owners"]
    assert (await _channels_of(db, second[0]))[0].id == (await _channels_of(db, first))[0].id


async def test_a_deleted_default_rule_is_not_recreated(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC22. Creating another project is the one other thing that provisions a
    rule, and it must provision only its own."""
    await db.execute(delete(AlertRule).where(AlertRule.project_id == tenant.project.id))
    await projects.create(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        request=ProjectCreateRequest(name="Another"),
    )
    assert await _rules(db, tenant.project.id) == []


async def test_deleting_a_project_takes_its_rules_and_closes_its_incidents(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """A deleted project is never evaluated again; rules left behind would still be
    named on the channels they send to, and their open incidents never close."""
    staging = await projects.create(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        request=ProjectCreateRequest(name="Staging"),
    )
    [rule] = await _rules(db, staging.id)
    incident = Incident(
        org_id=tenant.organization.id,
        project_id=staging.id,
        rule_id=rule.id,
        rule_name=rule.name,
        condition_kind="check_state",
        subject_key=f"check:{uuid.uuid4()}",
        subject_label="Checkout API",
        severity="critical",
        opened_at=datetime.now(UTC),
    )
    db.add(incident)
    await db.flush()

    await projects.delete(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, project_id=staging.id
    )
    assert await _rules(db, staging.id) == []
    await db.refresh(incident)
    assert incident.resolved_at is not None
    assert len(await _rules(db, tenant.project.id)) == 1
