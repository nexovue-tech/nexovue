"""A world for rung 3's jobs: a tenant, its Fix App on the simulated GitHub, a repository,
a code location, an AI connection, a consent and a finding, each row the test's own (F5
plan T3.10, T3.13, T3.17). Every test builds its own organization and drops it after."""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from alert_fixtures import AlertTenant, add_finding, drop_tenant, make_tenant
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, generate_key, keyring_from
from nexovue_platform.connections import CONNECTION_CREDENTIAL_PURPOSE, connection_secret_owner
from nexovue_platform.fixes.records import FIX_APP_CLIENT_SECRET_PURPOSE
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tracker_fake import CLIENT_ID, CLIENT_SECRET, FakeGitHub

from nexovue_scheduler.connections.fix_github import FixEndpoints

KEYS: Final = ConfiguredKeys(generate_key())
REPOSITORY: Final = "acme/shop"
REPOSITORY_ID: Final = 4200001
PROVIDER: Final = "https://provider.example/v1"
MODEL: Final = "model-a"
KEY_ID: Final = uuid.UUID("00000000-0000-4000-8000-000000000001")


@dataclass
class SourceWorld:
    tenant: AlertTenant
    user_id: uuid.UUID
    connection_id: uuid.UUID
    repository_id: uuid.UUID
    finding_id: uuid.UUID
    fake: FakeGitHub
    # The tracker account rung 3's jobs run as (F22 T8.7); arranging is the install's.
    jobs: AsyncEngine

    @property
    def org_id(self) -> uuid.UUID:
        return self.tenant.org_id

    def endpoints(self) -> FixEndpoints:
        return FixEndpoints(api=self.fake.base_url, web=self.fake.base_url, private_allowed=True)


def box(purpose: str) -> SealedBox:
    return SealedBox(keyring_from(KEYS.current, KEYS.previous), purpose=purpose)


async def build_world(
    engine: AsyncEngine,
    fake: FakeGitHub,
    *,
    jobs: AsyncEngine,
    folder: str | None = "services/shop",
    now: datetime,
) -> SourceWorld:
    tenant = await make_tenant(engine, with_check=False)
    connection_id, repository_id = uuid.uuid4(), uuid.uuid4()
    owner = connection_secret_owner(str(tenant.org_id), str(connection_id))
    # Each world its own installation: (App, installation) is unique across tenants.
    installation = fake.install(secrets.randbelow(10**9) + 10**7)
    fake.add_repository(REPOSITORY_ID, REPOSITORY)
    async with engine.begin() as connection:
        user_id = (
            await connection.execute(
                text("SELECT id FROM users WHERE org_id = :org"), {"org": tenant.org_id}
            )
        ).scalar_one()
        await connection.execute(
            text(
                """
                INSERT INTO connections (id, org_id, kind, state, sealed_credential,
                    sealed_client_secret, github_client_id, github_app_id, github_app_slug,
                    github_installation_id, granted_permissions, repositories_read_at)
                VALUES (:id, :org, 'github_fix', 'working', :credential, :secret, :client,
                    :app, 'nexovue-suggestions-acme', :installation, CAST(:granted AS jsonb),
                    :now)
                """
            ),
            {
                "id": connection_id,
                "org": tenant.org_id,
                "credential": box(CONNECTION_CREDENTIAL_PURPOSE).seal(
                    fake.private_key_pem, owner=owner
                ),
                "secret": box(FIX_APP_CLIENT_SECRET_PURPOSE).seal(CLIENT_SECRET, owner=owner),
                "client": CLIENT_ID,
                "app": fake.app_id,
                "installation": installation,
                "granted": json.dumps({"contents": "read", "metadata": "read"}),
                "now": now,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO connection_repositories (id, org_id, connection_id, external_id,
                    key, is_private)
                VALUES (:id, :org, :connection, :external, :key, true)
                """
            ),
            {
                "id": repository_id,
                "org": tenant.org_id,
                "connection": connection_id,
                "external": str(REPOSITORY_ID),
                "key": REPOSITORY,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO service_code_locations (org_id, project_id, service_name,
                    connection_repository_id, folder)
                VALUES (:org, NULL, 'checkout', :repository, :folder)
                """
            ),
            {"org": tenant.org_id, "repository": repository_id, "folder": folder},
        )
        await connection.execute(
            text(
                """
                INSERT INTO ai_connections (org_id, wire_format, base_url, model, key_id,
                    call_seconds, monthly_token_limit, enabled, consented_at,
                    consented_base_url, consented_fingerprint, consented_schema)
                VALUES (:org, 'openai_chat', :url, :model, :key_id, 30, 1000000, true, :now,
                    :url, :fingerprint, 1)
                """
            ),
            {
                "org": tenant.org_id,
                "url": PROVIDER,
                "model": MODEL,
                "key_id": KEY_ID,
                "now": now,
                "fingerprint": "f" * 64,
            },
        )
        finding_id = await add_finding(connection, tenant, started_at=now)
    await consent(engine, tenant.org_id, repository_id, folder=folder, now=now)
    return SourceWorld(tenant, user_id, connection_id, repository_id, finding_id, fake, jobs)


async def consent(
    engine: AsyncEngine,
    org_id: uuid.UUID,
    repository_id: uuid.UUID,
    *,
    folder: str | None,
    now: datetime,
    require_github_access: bool = False,
    model: str = MODEL,
    excluded: list[str] | None = None,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO fix_source_consents (org_id, connection_repository_id,
                    provider_base_url, provider_format, key_id, model, folder_scope,
                    extra_excluded_paths, require_github_access, consented_at)
                VALUES (:org, :repository, :url, 'openai_chat', :key_id, :model, :folder,
                    CAST(:excluded AS jsonb), :required, :now)
                """
            ),
            {
                "org": org_id,
                "repository": repository_id,
                "url": PROVIDER,
                "key_id": KEY_ID,
                "model": model,
                "folder": folder,
                "excluded": json.dumps(excluded or []),
                "required": require_github_access,
                "now": now,
            },
        )


async def source_fix(
    engine: AsyncEngine,
    world: SourceWorld,
    *,
    candidates: list[dict[str, Any]],
    now: datetime,
    candidates_from: str = "stack",
    read_commit: str | None = None,
    folder: str | None = "services/shop",
    state: str = "gathering",
    **columns: Any,
) -> uuid.UUID:
    fix_id = uuid.uuid4()
    extra = "".join(f", {name}" for name in columns)
    values = "".join(f", :{name}" for name in columns)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                f"""
                INSERT INTO finding_fixes (id, org_id, project_id, finding_id, kind, state,
                    state_changed_at, connection_repository_id, folder, path_candidates,
                    candidates_from, read_commit, requested_by, requested_at{extra})
                VALUES (:id, :org, :project, :finding, 'source', :state, :now, :repository,
                    :folder, CAST(:candidates AS jsonb), :from, :commit, :user, :now{values})
                """
            ),
            {
                "id": fix_id,
                "org": world.org_id,
                "project": world.tenant.project_id,
                "finding": world.finding_id,
                "state": state,
                "now": now,
                "repository": world.repository_id,
                "folder": folder,
                "candidates": None if state != "gathering" else json.dumps(candidates),
                "from": candidates_from,
                "commit": read_commit,
                "user": world.user_id,
                **columns,
            },
        )
    return fix_id


async def fix_row(engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text("SELECT * FROM finding_fixes WHERE org_id = :org AND id = :id"),
                {"org": world.org_id, "id": fix_id},
            )
        ).one()


async def pack_of(engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID) -> Any | None:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text("SELECT * FROM finding_fix_source_packs WHERE org_id = :org AND fix_id = :id"),
                {"org": world.org_id, "id": fix_id},
            )
        ).one_or_none()


async def audit_reads(engine: AsyncEngine, world: SourceWorld) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT actor_user_id, detail FROM audit_log "
                    "WHERE org_id = :org AND action = 'repository.read' ORDER BY at"
                ),
                {"org": world.org_id},
            )
        )


async def drop_world(engine: AsyncEngine, world: SourceWorld) -> None:
    """The test's own rows only: its queued jobs and results, then its organization."""
    async with engine.begin() as connection:
        for table in ("jobs", "job_results"):
            await connection.execute(
                text(f"DELETE FROM {table} WHERE org_id = :org"), {"org": world.org_id}
            )
    await drop_tenant(engine, world.tenant)
