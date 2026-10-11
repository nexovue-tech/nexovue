"""Checks against real Postgres: what is saved, what is refused, and who can reach it.

Two real organizations, real rows, and every cross-tenant attempt answered as
though the row did not exist. Everything runs inside a transaction that is
rolled back, so the suite leaves nothing behind.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from nexovue_platform import erasure
from nexovue_platform.checks.config import parse_config
from nexovue_platform.checks.sealing import ConfiguredKeys
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.checks import service
from nexovue_api.checks.schemas import CreateCheckRequest, CreatedCheck, UpdateCheckRequest
from nexovue_api.checksecrets import service as check_secrets
from nexovue_api.data.postgres.models import AuditLogEntry, CheckSecret
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.heartbeats import service as heartbeats
from nexovue_api.maintenance import service as maintenance
from nexovue_api.maintenance.schemas import WriteMaintenanceWindowRequest
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

TARGET: Final = "https://example.com/health"
LOCATIONS: Final = ["mumbai", "hyderabad"]
SECRET_KEY: Final = "qPYHqC1y6hD0s6knf12GgLTcvJsKkPdzaDgvE8sAFA4="
STATUS_OK: Final = {"source": "status_code", "comparison": "less_than", "target": "400"}


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection((parts.hostname or "", parts.port or 5432), timeout=2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="the live checks suite",
    remedy="Postgres is not running — start it with `just dev`",
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


async def _new_tenant(db: AsyncSession) -> ProvisionedTenant:
    unique = uuid.uuid4().hex[:12]
    return await provision_tenant(
        db,
        organization_name=f"Checks Live {unique}",
        email=f"owner-{unique}@live.test",
        password="correct-horse-battery",
    )


def _api_config(url: str = TARGET, **request: Any) -> Any:
    return parse_config(
        {"type": "api", "request": {"url": url, "assertions": [STATUS_OK], **request}}
    )


async def _create(
    db: AsyncSession, tenant: ProvisionedTenant, config: Any = None, **fields: Any
) -> CreatedCheck:
    return await service.create(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        request=CreateCheckRequest(
            project_id=tenant.project.id,
            name=fields.pop("name", "Health"),
            config=config if config is not None else _api_config(),
            **fields,
        ),
        available_locations=LOCATIONS,
    )


async def _problems(call: Any) -> list[dict[str, str]]:
    with pytest.raises(InvalidRequestError) as refused:
        await call
    return list(refused.value.extensions.get("errors", []))


async def _audit_actions(db: AsyncSession, org_id: uuid.UUID) -> Sequence[str]:
    rows = await db.scalars(select(AuditLogEntry).where(AuditLogEntry.org_id == org_id))
    return [entry.action for entry in rows]


# --- tenancy ------------------------------------------------------------------------


async def test_a_check_is_created_and_listed_for_its_own_project(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    created = await _create(db, tenant)

    listed = await service.list_for_project(
        db,
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        available_locations=LOCATIONS,
    )

    assert [check.id for check in listed.checks] == [created.id]
    assert listed.checks[0].enabled is True
    assert listed.checks[0].type == "api"
    assert listed.checks[0].displayed_state is None, "never run is not a state"
    assert listed.available_locations == LOCATIONS


async def test_another_organizations_check_cannot_be_read_edited_or_deleted(
    db: AsyncSession,
) -> None:
    """A 404, not a 403: whether the row exists is itself not theirs to learn."""
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    theirs = await _create(db, owner)
    org = stranger.organization.id

    with pytest.raises(NotFoundError):
        await service.get(db, org_id=org, check_id=theirs.id)
    with pytest.raises(NotFoundError):
        await service.update(
            db,
            org_id=org,
            actor_user_id=stranger.user.id,
            check_id=theirs.id,
            request=UpdateCheckRequest(enabled=False),
            available_locations=LOCATIONS,
        )
    with pytest.raises(NotFoundError):
        await service.remove(db, org_id=org, actor_user_id=stranger.user.id, check_id=theirs.id)
    with pytest.raises(NotFoundError):
        await service.issue_heartbeat_token(
            db, org_id=org, actor_user_id=stranger.user.id, check_id=theirs.id
        )


async def test_a_check_cannot_be_created_on_another_organizations_project(
    db: AsyncSession,
) -> None:
    owner, stranger = await _new_tenant(db), await _new_tenant(db)

    with pytest.raises(NotFoundError):
        await service.create(
            db,
            org_id=stranger.organization.id,
            actor_user_id=stranger.user.id,
            request=CreateCheckRequest(project_id=owner.project.id, name="x", config=_api_config()),
            available_locations=LOCATIONS,
        )


# --- validation (FR38, AC25) ------------------------------------------------------------


async def test_every_problem_is_named_before_anything_is_stored(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    config = parse_config(
        {
            "type": "multistep",
            "steps": [
                {
                    "url": "http://169.254.169.254/latest/meta-data/",
                    "assertions": [STATUS_OK],
                },
                {
                    "url": "https://api.example.com/{{order_id}}",
                    "auth": {"kind": "bearer", "token_secret": "MISSING_TOKEN"},
                    "assertions": [STATUS_OK],
                },
            ],
        }
    )

    problems = await _problems(
        _create(db, tenant, config, locations=["mars"], degraded_ms=500, failing_ms=200)
    )

    fields = {problem["field"] for problem in problems}
    assert "config.steps.0.url" in fields
    assert "config.steps.1" in fields
    assert "locations" in fields
    assert "failing_ms" in fields
    reasons = " ".join(problem["reason"] for problem in problems)
    assert "MISSING_TOKEN" in reasons and "order_id" in reasons
    listed = await service.list_for_project(
        db,
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        available_locations=LOCATIONS,
    )
    assert listed.checks == []


async def test_a_defined_secret_and_variable_make_the_same_check_valid(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    box = check_secrets.secret_box(ConfiguredKeys(SECRET_KEY))
    await check_secrets.write_secret(
        db,
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        name="TOKEN",
        value="a-long-enough-token",
        box=box,
        actor_user_id=tenant.user.id,
    )
    await check_secrets.write_variable(
        db, org_id=tenant.organization.id, project_id=tenant.project.id, name="order_id", value="7"
    )
    config = _api_config(
        "https://api.example.com/orders/{{order_id}}",
        auth={"kind": "bearer", "token_secret": "TOKEN"},
    )

    created = await _create(db, tenant, config)

    assert created.config.type == "api"
    secrets = await check_secrets.list_secrets(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    variables = await check_secrets.list_variables(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    assert [(item.name, item.used_by_checks) for item in secrets.secrets] == [("TOKEN", 1)]
    assert [(item.name, item.used_by_checks) for item in variables.variables] == [("order_id", 1)]


async def test_a_url_whose_host_is_a_variable_is_judged_when_it_runs(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    await check_secrets.write_variable(
        db, org_id=tenant.organization.id, project_id=tenant.project.id, name="base", value="x"
    )
    created = await _create(db, tenant, _api_config("{{base}}/health"))
    assert created.id


async def test_the_kind_of_a_check_cannot_change(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    created = await _create(db, tenant)

    problems = await _problems(
        service.update(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            check_id=created.id,
            request=UpdateCheckRequest(
                config=parse_config({"type": "tcp", "host": "a.example.com", "port": 1})
            ),
            available_locations=LOCATIONS,
        )
    )
    assert problems == [{"field": "config.type", "reason": "a check's kind cannot be changed"}]


async def test_pausing_a_check_whose_secret_was_deleted_still_pauses_it(db: AsyncSession) -> None:
    """Only an edit to what a check does is re-validated."""
    tenant = await _new_tenant(db)
    org, project = tenant.organization.id, tenant.project.id
    await check_secrets.write_secret(
        db,
        org_id=org,
        project_id=project,
        name="TOKEN",
        value="token-value",
        box=check_secrets.secret_box(ConfiguredKeys(SECRET_KEY)),
        actor_user_id=tenant.user.id,
    )
    created = await _create(
        db, tenant, _api_config(auth={"kind": "bearer", "token_secret": "TOKEN"})
    )
    await check_secrets.delete_secret(
        db, org_id=org, project_id=project, name="TOKEN", actor_user_id=tenant.user.id
    )

    paused = await service.update(
        db,
        org_id=org,
        actor_user_id=tenant.user.id,
        check_id=created.id,
        request=UpdateCheckRequest(enabled=False),
        available_locations=LOCATIONS,
    )

    assert paused.enabled is False
    assert paused.displayed_state == "paused"


# --- secrets (FR20, AC17, EC12) -----------------------------------------------------------


async def test_a_secret_is_stored_sealed_and_listed_without_its_value(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    marker = f"marker-{uuid.uuid4()}"
    summary = await check_secrets.write_secret(
        db,
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        name="API_KEY",
        value=marker,
        box=check_secrets.secret_box(ConfiguredKeys(SECRET_KEY)),
        actor_user_id=tenant.user.id,
    )
    listed = await check_secrets.list_secrets(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    stored = await db.scalar(
        select(CheckSecret).where(CheckSecret.org_id == tenant.organization.id)
    )

    assert marker not in summary.model_dump_json() + listed.model_dump_json()
    assert stored is not None and marker.encode() not in stored.sealed_value
    assert "check_secret.written" in await _audit_actions(db, tenant.organization.id)
    audit_text = (
        await db.execute(text("SELECT string_agg(target, ' ') FROM audit_log"))
    ).scalar_one()
    assert marker not in (audit_text or "")


async def test_no_secret_key_is_a_503_not_a_secret_stored_in_the_clear() -> None:
    from nexovue_api.errors.exceptions import DependencyUnavailableError

    with pytest.raises(DependencyUnavailableError):
        check_secrets.secret_box(ConfiguredKeys(""))


# --- heartbeats (FR16, AC14) ---------------------------------------------------------------


async def test_a_heartbeat_token_is_shown_once_and_a_ping_records_the_call(
    db: AsyncSession,
) -> None:
    tenant = await _new_tenant(db)
    created = await _create(
        db, tenant, parse_config({"type": "heartbeat", "grace_seconds": 60}), interval_seconds=300
    )
    assert created.heartbeat_token
    assert created.locations == []

    await heartbeats.record_ping(db, token=created.heartbeat_token)
    after = await service.get(db, org_id=tenant.organization.id, check_id=created.id)
    assert after.last_ping_at is not None
    assert after.last_ping_at > datetime.now(UTC) - timedelta(minutes=1)


async def test_a_rotated_or_unknown_token_is_refused_the_same_way(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    created = await _create(db, tenant, parse_config({"type": "heartbeat"}))
    assert created.heartbeat_token
    rotated = await service.issue_heartbeat_token(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, check_id=created.id
    )

    with pytest.raises(NotFoundError):
        await heartbeats.record_ping(db, token=created.heartbeat_token)
    with pytest.raises(NotFoundError):
        await heartbeats.record_ping(db, token="not-a-token")
    await heartbeats.record_ping(db, token=rotated.token)
    assert rotated.ping_path.endswith(rotated.token)


# --- maintenance (FR36) -----------------------------------------------------------------


async def test_a_window_covering_a_check_puts_it_in_maintenance(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    created = await _create(db, tenant)
    now = datetime.now(UTC)
    await maintenance.create(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        request=WriteMaintenanceWindowRequest(
            project_id=tenant.project.id,
            name="Deploy",
            starts_at=now - timedelta(minutes=5),
            ends_at=now + timedelta(minutes=30),
            check_ids=[created.id],
        ),
    )

    after = await service.get(db, org_id=tenant.organization.id, check_id=created.id)
    assert after.in_maintenance is True
    assert after.displayed_state == "maintenance"


async def test_a_window_that_names_nothing_is_refused(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    now = datetime.now(UTC)
    problems = await _problems(
        maintenance.create(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            request=WriteMaintenanceWindowRequest(
                project_id=tenant.project.id,
                name="Nothing",
                starts_at=now,
                ends_at=now - timedelta(minutes=1),
                recurrence="daily",
            ),
        )
    )
    assert {problem["field"] for problem in problems} == {"ends_at", "check_ids"}


# --- audit ----------------------------------------------------------------------------


async def test_creating_editing_and_deleting_record_who_did_it(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    created = await _create(db, tenant)
    await service.update(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        check_id=created.id,
        request=UpdateCheckRequest(name="Renamed"),
        available_locations=LOCATIONS,
    )
    impact = await service.deletion_impact(db, org_id=tenant.organization.id, check_id=created.id)
    await service.remove(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, check_id=created.id
    )

    actions = await _audit_actions(db, tenant.organization.id)
    assert {"check.created", "check.updated", "check.deleted"} <= set(actions)
    assert impact.files == 0


# --- what a task carries (AC18) ----------------------------------------------------


async def test_a_run_now_definition_carries_the_secret_sealed_never_in_the_clear(
    db: AsyncSession,
) -> None:
    tenant = await _new_tenant(db)
    marker = f"marker-{uuid.uuid4()}"
    await check_secrets.write_secret(
        db,
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        name="TOKEN",
        value=marker,
        box=check_secrets.secret_box(ConfiguredKeys(SECRET_KEY)),
        actor_user_id=tenant.user.id,
    )

    definition = await service.run_now_definition(
        db,
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        name="Run now",
        config=_api_config(auth={"kind": "bearer", "token_secret": "TOKEN"}),
        degraded_ms=None,
        failing_ms=None,
        location="mumbai",
        available_locations=LOCATIONS,
    )

    task_arguments = definition.model_dump_json()
    assert "TOKEN" in definition.sealed_secrets
    assert marker not in task_arguments


# --- files (FR4, AC6, BR9) ----------------------------------------------------------

S3_ENDPOINT: Final = os.environ.get("NEXOVUE_TEST_S3_ENDPOINT", "http://localhost:18333")


@pytest.fixture
async def store() -> Any:
    from nexovue_api.data.objectstore import ObjectStore
    from nexovue_api.settings import ObjectStoreSettings

    store = ObjectStore(
        ObjectStoreSettings(
            endpoint=S3_ENDPOINT,
            bucket="nexovue-test-checks",
            access_key_id=os.environ.get("NEXOVUE_TEST_S3_KEY", "nexovue_dev_access"),
            secret_access_key=os.environ.get("NEXOVUE_TEST_S3_SECRET", "nexovue_dev_secret"),
            region="us-east-1",
        )
    )
    await store.ensure_bucket()
    return store


async def test_a_file_over_three_megabytes_is_refused_and_one_under_is_kept(
    db: AsyncSession, store: Any
) -> None:
    from nexovue_api.checkfiles import service as check_files

    tenant = await _new_tenant(db)
    org, project = tenant.organization.id, tenant.project.id

    with pytest.raises(InvalidRequestError):
        await check_files.upload(
            db,
            store,
            org_id=org,
            project_id=project,
            name="big.bin",
            content_type=None,
            body=b"x" * (3 * 1024 * 1024 + 1),
        )

    kept = await check_files.upload(
        db,
        store,
        org_id=org,
        project_id=project,
        name="small.bin",
        content_type="application/octet-stream",
        body=b"\x00\x01\x02",
    )
    try:
        assert await store.get(check_files.object_key(org, kept.id)) == b"\x00\x01\x02"

        created = await _create(
            db,
            tenant,
            _api_config(
                method="PUT",
                body={"kind": "binary", "file_id": str(kept.id)},
            ),
        )
        impact = await service.deletion_impact(db, org_id=org, check_id=created.id)
        assert impact.files == 1

        await service.remove(db, org_id=org, actor_user_id=tenant.user.id, check_id=created.id)
        # Detached, not deleted: the object goes once the deletion is durable, by the sweep.
        detached = (
            await db.execute(
                text("SELECT check_id, detached_at FROM check_files WHERE id = :id"),
                {"id": kept.id},
            )
        ).one()
        assert detached.check_id is None and detached.detached_at is not None
    finally:
        await store.delete_prefix(f"{erasure.CHECK_FILES_ROOT}/{org}/")


# --- imports never overwrite (review finding) ---------------------------------------


async def test_a_second_import_cannot_repoint_the_first_imports_variable(db: AsyncSession) -> None:
    from nexovue_api.checkimports import service as imports
    from nexovue_api.checkimports.schemas import (
        ImportConfirmRequest,
        ImportedCheck,
        ImportedSecret,
        ImportedVariable,
    )

    tenant = await _new_tenant(db)

    def request(base_url: str, token: str) -> ImportConfirmRequest:
        return ImportConfirmRequest(
            project_id=tenant.project.id,
            checks=[
                ImportedCheck(
                    name="Orders",
                    config=_api_config(
                        "{{base_url}}/orders", auth={"kind": "bearer", "token_secret": "API_TOKEN"}
                    ),
                )
            ],
            secrets=[ImportedSecret(name="API_TOKEN", value=token)],
            variables=[ImportedVariable(name="base_url", value=base_url)],
        )

    async def confirm(body: ImportConfirmRequest) -> None:
        await imports.confirm(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            request=body,
            check_secret_keys=ConfiguredKeys(SECRET_KEY),
            available_locations=LOCATIONS,
        )

    await confirm(request("https://a.example.com", "token-one"))
    # The same values again are not a collision.
    await confirm(request("https://a.example.com", "token-one"))

    problems = await _problems(confirm(request("https://b.example.com", "token-two")))

    assert {problem["field"] for problem in problems} == {"variables.0.name", "secrets.0.name"}
    values = await check_secrets.variable_values(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    assert values["base_url"] == "https://a.example.com"
