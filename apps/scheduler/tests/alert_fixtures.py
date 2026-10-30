"""A real tenant in Postgres for the alert suites, removed afterwards.

`state_store.record` and the outbox commit their own transactions, which is the
behaviour under test, so these suites cannot run inside a rolled-back one. Each
tenant is created with the rows signup would make — an owner, a project, the
owners channel and the default rule — plus one check, and is deleted with its
organization at the end: every row it owns cascades from there.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)


def postgres_reachable() -> bool:
    parts = urlsplit(POSTGRES_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


def future() -> datetime:
    """A month from now: where these suites decide and deliver.

    The stack's own deliveries worker claims every due notification in the
    database. A test's rows are due only a month from now, so that worker never
    takes them, and the test's claims — made at that future moment — always do.
    """
    return datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)


@dataclass(frozen=True, slots=True)
class AlertTenant:
    org_id: uuid.UUID
    project_id: uuid.UUID
    owner_email: str
    check_id: uuid.UUID | None
    owners_channel_id: uuid.UUID
    default_rule_id: uuid.UUID


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    created = create_async_engine(ASYNC_DSN, pool_size=4)
    try:
        yield created
    finally:
        await created.dispose()


@pytest.fixture
async def tenant(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await make_tenant(engine)
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


async def drop_tenant(engine: AsyncEngine, tenant: AlertTenant) -> None:
    """Every row the tenant owns cascades from its organization."""
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM organizations WHERE id = :org_id"), {"org_id": tenant.org_id}
        )


async def make_tenant(
    engine: AsyncEngine, *, verified: bool = True, with_check: bool = True
) -> AlertTenant:
    """`with_check=False` for a test whose counts must not move: the dev stack runs any enabled
    check, and keeps running it until the beat refreshes even after it is deleted."""
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    check_id = uuid.uuid4() if with_check else None
    email = f"owner-{org_id.hex[:12]}@example.test"
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Alerts')"),
            {"id": org_id, "slug": f"alerts-{org_id.hex[:12]}"},
        )
        await connection.execute(
            text(
                """
                INSERT INTO users (org_id, email, role, password_hash, email_verified_at)
                VALUES (:org_id, :email, 'owner', 'not-a-hash',
                        CASE WHEN :verified THEN now() END)
                """
            ),
            {"org_id": org_id, "email": email, "verified": verified},
        )
        await connection.execute(
            text(
                "INSERT INTO projects (id, org_id, slug, name) "
                "VALUES (:id, :org_id, 'production', 'Production')"
            ),
            {"id": project_id, "org_id": org_id},
        )
        if check_id is not None:
            await connection.execute(
                text(
                    """
                    INSERT INTO checks
                        (id, org_id, project_id, name, type, config, interval_seconds)
                    VALUES (:id, :org_id, :project_id, 'Checkout API', 'tcp',
                            CAST(:config AS jsonb), 60)
                    """
                ),
                {
                    "id": check_id,
                    "org_id": org_id,
                    "project_id": project_id,
                    "config": '{"type": "tcp", "host": "example.test", "port": 443}',
                },
            )
        owners = await add_channel(connection, org_id, kind="owners", name="Organization owners")
        rule = await add_rule(
            connection,
            org_id,
            project_id,
            name="Any check down",
            condition={"kind": "check_state", "check_ids": None, "states": {"down": "critical"}},
            channel_ids=[owners],
            is_default=True,
        )
    return AlertTenant(
        org_id=org_id,
        project_id=project_id,
        owner_email=email,
        check_id=check_id,
        owners_channel_id=owners,
        default_rule_id=rule,
    )


async def add_channel(
    connection: Any,
    org_id: uuid.UUID,
    *,
    kind: str,
    name: str,
    config: str = "{}",
    muted: bool = False,
) -> uuid.UUID:
    channel_id: uuid.UUID = (
        await connection.execute(
            text(
                """
                INSERT INTO notification_channels (org_id, kind, name, config, muted_until)
                VALUES (:org_id, :kind, :name, CAST(:config AS jsonb),
                        CASE WHEN :muted THEN now() + interval '1 hour' END)
                RETURNING id
                """
            ),
            {"org_id": org_id, "kind": kind, "name": name, "config": config, "muted": muted},
        )
    ).scalar_one()
    return channel_id


async def add_rule(
    connection: Any,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    name: str,
    condition: dict[str, Any],
    channel_ids: list[uuid.UUID],
    is_default: bool = False,
    muted: bool = False,
) -> uuid.UUID:
    rule_id: uuid.UUID = (
        await connection.execute(
            text(
                """
                INSERT INTO alert_rules
                    (org_id, project_id, name, condition_kind, condition, is_default, muted_until)
                VALUES (:org_id, :project_id, :name, 'check_state', CAST(:condition AS jsonb),
                        :is_default, CASE WHEN :muted THEN now() + interval '1 hour' END)
                RETURNING id
                """
            ),
            {
                "org_id": org_id,
                "project_id": project_id,
                "name": name,
                "condition": json.dumps(condition),
                "is_default": is_default,
                "muted": muted,
            },
        )
    ).scalar_one()
    for channel_id in channel_ids:
        await connection.execute(
            text(
                "INSERT INTO alert_rule_channels (org_id, rule_id, channel_id) "
                "VALUES (:org_id, :rule_id, :channel_id)"
            ),
            {"org_id": org_id, "rule_id": rule_id, "channel_id": channel_id},
        )
    return rule_id


async def rows(engine: AsyncEngine, sql: str, **parameters: Any) -> list[Any]:
    async with engine.connect() as connection:
        return list((await connection.execute(text(sql), parameters)).fetchall())
