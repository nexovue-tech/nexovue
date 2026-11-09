"""Tracker connections in the API, against real Postgres (F22 plan T3.5, T3.8, T3.11;
AC74, AC76, AC79, AC118, AC136). Every test runs in a transaction that is rolled back.

The API never calls GitHub (AC32): it creates the setup, checks the return, seals what
GitHub handed back, and adds a job, in the same transaction, with ids only. So this
suite reads the job rows it would have added, and searches every response for anything
sealed.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, generate_key, keyring_from
from nexovue_platform.connections import (
    CHECK_CONNECTION_TASK,
    CONNECTION_CREDENTIAL_PURPOSE,
    CONNECTION_MANIFEST_CODE_PURPOSE,
    CONNECTION_TEST_RESULT_KIND,
    CONNECTIONS_INTERACTIVE_QUEUE,
    CONVERT_MANIFEST_TASK,
    REFRESH_CONNECTION_TASK,
    connection_secret_owner,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.connections import service
from nexovue_api.connections.schemas import Availability
from nexovue_api.data.postgres.models import AuditLogEntry, Connection
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
KEYS: Final = ConfiguredKeys(generate_key())
NOW: Final = datetime.now(UTC).replace(microsecond=0)
CONSOLE: Final = "https://nexovue.acme.example"
SELF_HOSTED: Final = Availability(deployment="self-hosted", enabled=True, app_base_url=CONSOLE)
HOSTED_SENTENCE: Final = "Tracker connections are not available on the hosted service yet."
OFF_SENTENCE: Final = (
    "Tracker connections are turned off on this install. "
    "An operator can turn them on: see the setup page."
)
# A PEM's shape, spelled in pieces so no secret scanner reads this file as holding a key.
_KEY_LABEL: Final = "PRIVATE" + " KEY"
PEM_BEGIN: Final = f"-----BEGIN {_KEY_LABEL}-----"
PEM_END: Final = f"-----END {_KEY_LABEL}-----"
NEW_KEY: Final = f"{PEM_BEGIN}\nMIIBVgIBADANBgkqhkiG9w0BAQEFAASCAUAwggE8\n{PEM_END}\n"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_connections_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            # As the API makes its sessions (`lifespan.py`): objects survive a commit.
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


@pytest.fixture
async def tenant(db: AsyncSession) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name="Connections",
        email=f"connections-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )


async def _colleague(db: AsyncSession, tenant: ProvisionedTenant) -> uuid.UUID:
    colleague: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO users (org_id, email, role, password_hash) "
                "VALUES (:org, :email, 'admin', 'not-a-hash') RETURNING id"
            ),
            {"org": tenant.organization.id, "email": f"admin-{uuid.uuid4()}@example.test"},
        )
    ).scalar_one()
    return colleague


async def _start(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    *,
    availability: Availability = SELF_HOSTED,
    reachable: bool = False,
    github_organization: str | None = None,
    now: datetime = NOW,
) -> Any:
    return await service.start_github(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        availability=availability,
        reachable=reachable,
        github_organization=github_organization,
        now=now,
    )


async def _complete(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    *,
    state: str,
    code: str = "4f1c0de5",
    user_id: uuid.UUID | None = None,
    now: datetime = NOW,
) -> Any:
    return await service.complete_github(
        db,
        org_id=tenant.organization.id,
        actor_user_id=user_id or tenant.user.id,
        state=state,
        code=code,
        keys=KEYS,
        availability=SELF_HOSTED,
        now=now,
    )


async def _row(db: AsyncSession, connection_id: uuid.UUID) -> Connection:
    row = await db.scalar(select(Connection).where(Connection.id == connection_id))
    assert row is not None
    await db.refresh(row)
    return row


async def _jobs(db: AsyncSession, tenant: ProvisionedTenant) -> list[Any]:
    return list(
        (
            await db.execute(
                text("SELECT task, queue, kwargs FROM jobs WHERE org_id = :org ORDER BY id"),
                {"org": tenant.organization.id},
            )
        ).all()
    )


async def _actions(db: AsyncSession, tenant: ProvisionedTenant) -> list[str]:
    entries = await db.scalars(
        select(AuditLogEntry.action).where(
            AuditLogEntry.org_id == tenant.organization.id,
            AuditLogEntry.action.like("connection.%"),
        )
    )
    return list(entries)


def _ids(tenant: ProvisionedTenant, connection_id: uuid.UUID) -> dict[str, str]:
    return {"org_id": str(tenant.organization.id), "connection_id": str(connection_id)}


def _box(purpose: str) -> SealedBox:
    return SealedBox(keyring_from(KEYS.current), purpose=purpose)


async def _working(db: AsyncSession, tenant: ProvisionedTenant) -> uuid.UUID:
    """A connection whose App is made, installed and tested."""
    connection_id = uuid.uuid4()
    owner = connection_secret_owner(str(tenant.organization.id), str(connection_id))
    db.add(
        Connection(
            id=connection_id,
            org_id=tenant.organization.id,
            kind="github",
            state="working",
            sealed_credential=_box(CONNECTION_CREDENTIAL_PURPOSE).seal("old key", owner=owner),
            github_app_id=7,
            github_app_slug="nexovue-connections",
            github_installation_id=70,
            last_ok_at=NOW,
        )
    )
    await db.flush()
    await db.execute(
        text(
            "INSERT INTO connection_repositories (org_id, connection_id, external_id, key, "
            "is_private) VALUES (:org, :id, '11', 'acme/api', true)"
        ),
        {"org": tenant.organization.id, "id": connection_id},
    )
    return connection_id


# ------------------------------------------------------------------- starting


async def test_starting_creates_a_private_app_s_manifest_and_keeps_only_the_state_s_hash(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    started = await _start(db, tenant)

    row = await _row(db, started.connection_id)
    assert (row.kind, row.state, row.setup_user_id) == ("github", "setting_up", tenant.user.id)
    assert row.setup_state_hash == hashlib.sha256(started.state.encode()).digest()
    assert row.setup_expires_at == NOW + timedelta(hours=1)
    manifest = started.manifest
    assert manifest["public"] is False
    assert manifest["default_permissions"] == {"issues": "write", "metadata": "read"}
    assert manifest["default_events"] == ["issues"]
    assert manifest["hook_attributes"] == {
        "url": f"{CONSOLE}/api/v1/connections/webhook/{started.connection_id}",
        "active": False,
    }
    assert manifest["redirect_url"] == f"{CONSOLE}/settings/connections/github/return"
    assert manifest["setup_url"] == f"{CONSOLE}/settings/connections/github/installed"
    assert manifest["name"].startswith("nexovue-")
    assert len(manifest["name"]) <= 34
    assert started.post_url == "https://github.com/settings/apps/new"
    assert await _actions(db, tenant) == ["connection.created"]


async def test_a_reachable_server_asks_github_for_its_webhook(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    started = await _start(db, tenant, reachable=True, github_organization="acme-inc")

    assert started.manifest["hook_attributes"]["active"] is True
    assert started.post_url == "https://github.com/organizations/acme-inc/settings/apps/new"


async def test_an_organization_name_outside_github_s_shape_is_refused(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    with pytest.raises(InvalidRequestError):
        await _start(db, tenant, github_organization="evil.example/../x")


@pytest.mark.parametrize(
    ("availability", "sentence"),
    [
        (Availability(deployment="hosted", enabled=True, app_base_url=CONSOLE), HOSTED_SENTENCE),
        (Availability(deployment="self-hosted", enabled=False, app_base_url=CONSOLE), OFF_SENTENCE),
    ],
)
async def test_no_setup_starts_where_connections_do_not_exist(
    db: AsyncSession, tenant: ProvisionedTenant, availability: Availability, sentence: str
) -> None:
    """AC136 on hosted, AC118 with `NEXOVUE_CONNECTIONS=no`."""
    with pytest.raises(ConflictError) as refused:
        await _start(db, tenant, availability=availability)

    assert refused.value.detail == sentence
    assert (
        await db.scalar(select(Connection).where(Connection.org_id == tenant.organization.id))
        is None
    )


async def test_a_live_connection_blocks_a_second_setup(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    await _working(db, tenant)

    with pytest.raises(ConflictError):
        await _start(db, tenant)


async def test_a_setup_abandoned_past_its_hour_does_not_block_a_new_one(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC76: it is marked disconnected first, so the one-live-row index lets the new in."""
    abandoned = await _start(db, tenant, now=NOW - timedelta(hours=2))

    fresh = await _start(db, tenant)

    assert (await _row(db, abandoned.connection_id)).state == "disconnected"
    assert (await _row(db, abandoned.connection_id)).setup_state_hash is None
    assert (await _row(db, fresh.connection_id)).state == "setting_up"


async def test_an_app_made_but_not_installed_still_blocks_after_its_hour(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """GitHub holds that App; dropping the row would orphan it. *Stop setting up* does that."""
    made = await _start(db, tenant, now=NOW - timedelta(hours=2))
    (await _row(db, made.connection_id)).github_app_id = 9
    await db.flush()

    with pytest.raises(ConflictError):
        await _start(db, tenant)


# ---------------------------------------------------------------- the return


async def test_the_return_seals_the_code_and_adds_a_job_naming_ids_only(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC74: the code is in no job row; the job's kwargs are ids."""
    started = await _start(db, tenant)

    completed = await _complete(db, tenant, state=started.state, code="c0de4f1c")

    row = await _row(db, started.connection_id)
    owner = connection_secret_owner(str(tenant.organization.id), str(started.connection_id))
    assert completed.connection_id == started.connection_id
    assert row.setup_state_hash is None
    assert row.sealed_manifest_code is not None
    assert _box(CONNECTION_MANIFEST_CODE_PURPOSE).open(row.sealed_manifest_code, owner=owner) == (
        "c0de4f1c"
    )
    [job] = await _jobs(db, tenant)
    assert (job.task, job.queue) == (CONVERT_MANIFEST_TASK, CONNECTIONS_INTERACTIVE_QUEUE)
    assert job.kwargs == _ids(tenant, started.connection_id)
    assert "c0de4f1c" not in json.dumps(job.kwargs)


async def test_a_state_that_was_never_issued_is_refused_and_changes_nothing(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    started = await _start(db, tenant)

    with pytest.raises(InvalidRequestError) as refused:
        await _complete(db, tenant, state="not-the-state")

    assert refused.value.extensions["reason"] == "invalid"
    assert (await _row(db, started.connection_id)).sealed_manifest_code is None
    assert await _jobs(db, tenant) == []


async def test_a_state_used_once_is_refused_the_second_time(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    started = await _start(db, tenant)
    await _complete(db, tenant, state=started.state)

    with pytest.raises(InvalidRequestError) as refused:
        await _complete(db, tenant, state=started.state, code="another")

    assert refused.value.extensions["reason"] == "invalid"
    assert len(await _jobs(db, tenant)) == 1


async def test_a_state_returned_by_someone_else_is_refused(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    started = await _start(db, tenant)
    colleague = await _colleague(db, tenant)

    with pytest.raises(InvalidRequestError) as refused:
        await _complete(db, tenant, state=started.state, user_id=colleague)

    assert refused.value.extensions["reason"] == "invalid"


async def test_a_state_past_its_hour_is_refused_and_frees_the_slot(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    started = await _start(db, tenant, now=NOW - timedelta(hours=1, minutes=1))

    with pytest.raises(InvalidRequestError) as refused:
        await _complete(db, tenant, state=started.state)

    assert refused.value.extensions["reason"] == "expired"
    row = await _row(db, started.connection_id)
    assert (row.state, row.sealed_manifest_code) == ("disconnected", None)


@pytest.mark.parametrize("code", ["../../app/installations", "a b", "x" * 101, ""])
async def test_a_code_outside_github_s_shape_never_reaches_a_job(
    db: AsyncSession, tenant: ProvisionedTenant, code: str
) -> None:
    """The code becomes part of a GitHub path in the job; only a plain token may."""
    started = await _start(db, tenant)

    with pytest.raises(InvalidRequestError):
        await _complete(db, tenant, state=started.state, code=code)

    assert await _jobs(db, tenant) == []


# -------------------------------------------------------------- what is shown


async def test_the_list_says_what_is_connected_and_never_carries_a_secret(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    connection_id = await _working(db, tenant)

    overview = await service.overview(
        db, org_id=tenant.organization.id, support_view=False, availability=SELF_HOSTED,
        email_available=False,
    )  # fmt: skip

    assert overview.available is True and overview.unavailable_reason is None
    assert overview.email_available is False
    [shown] = overview.connections
    assert (shown.id, shown.kind, shown.state) == (connection_id, "github", "working")
    assert shown.app_slug == "nexovue-connections"
    assert shown.last_ok_at == NOW
    assert [(place.key, place.is_private) for place in shown.repositories] == [("acme/api", True)]
    printed = overview.model_dump_json()
    assert "sealed" not in printed and "old key" not in printed


async def test_a_support_view_sees_kinds_and_states_only(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC79: no repository name, no App, no site, during a support view."""
    await _working(db, tenant)

    overview = await service.overview(
        db, org_id=tenant.organization.id, support_view=True, availability=SELF_HOSTED,
        email_available=True,
    )  # fmt: skip

    [shown] = overview.connections
    assert (shown.kind, shown.state) == ("github", "working")
    assert shown.repositories == [] and shown.app_slug is None and shown.setup_step is None
    assert "acme/api" not in overview.model_dump_json()


async def test_the_list_on_hosted_says_why_and_lists_nothing(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    hosted = Availability(deployment="hosted", enabled=True, app_base_url=CONSOLE)

    overview = await service.overview(
        db, org_id=tenant.organization.id, support_view=False, availability=hosted,
        email_available=True,
    )  # fmt: skip

    assert (overview.available, overview.unavailable_reason) == (False, "hosted")
    assert overview.connections == []


@pytest.mark.parametrize(
    ("columns", "step"),
    [
        ({}, "on_github"),
        ({"setup_state_hash": None, "sealed_manifest_code": b"x"}, "converting"),
        ({"github_app_id": 1, "github_app_slug": "nexovue-a", "setup_state_hash": None},
         "not_installed"),
        ({"github_app_id": 1, "github_installation_id": 2, "setup_state_hash": None,
          "last_failure": "no_repository"}, "no_repository"),
        ({"github_app_id": 1, "github_installation_id": 2, "setup_state_hash": None,
          "last_failure": "unauthorized"}, "test_failed"),
        ({"github_app_id": 1, "github_installation_id": 2, "setup_state_hash": None}, "testing"),
        ({"setup_state_hash": None, "last_failure": "manifest_refused"}, "refused"),
    ],
)  # fmt: skip
async def test_a_setup_says_which_step_it_is_on(
    db: AsyncSession, tenant: ProvisionedTenant, columns: dict[str, Any], step: str
) -> None:
    """Design, *GitHub setup screens*: each row names its one action."""
    started = await _start(db, tenant)
    row = await _row(db, started.connection_id)
    for name, value in columns.items():
        setattr(row, name, value)
    await db.flush()

    overview = await service.overview(
        db, org_id=tenant.organization.id, support_view=False, availability=SELF_HOSTED,
        email_available=True,
    )  # fmt: skip

    [shown] = overview.connections
    assert shown.setup_step == step


async def test_an_abandoned_setup_reads_as_expired(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    await _start(db, tenant, now=NOW - timedelta(hours=2))

    overview = await service.overview(
        db, org_id=tenant.organization.id, support_view=False, availability=SELF_HOSTED,
        email_available=True,
    )  # fmt: skip

    [shown] = overview.connections
    assert shown.setup_step == "expired"


# ------------------------------------------------------------- tests and jobs


async def test_a_connection_test_adds_a_job_and_its_result_is_read_back(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    connection_id = await _working(db, tenant)

    accepted = await service.dispatch_test(
        db, org_id=tenant.organization.id, connection_id=connection_id, availability=SELF_HOSTED
    )

    [job] = await _jobs(db, tenant)
    assert (job.task, job.queue) == (CHECK_CONNECTION_TASK, CONNECTIONS_INTERACTIVE_QUEUE)
    assert job.kwargs == {**_ids(tenant, connection_id), "request_id": accepted.request_id}
    pending = await service.test_result(
        db, org_id=tenant.organization.id, request_id=accepted.request_id
    )
    assert pending.pending is True
    await db.execute(
        text(
            "INSERT INTO job_results (request_id, org_id, kind, payload, expires_at) "
            "VALUES (:id, :org, :kind, CAST(:payload AS jsonb), now() + interval '5 minutes')"
        ),
        {
            "id": accepted.request_id,
            "org": tenant.organization.id,
            "kind": CONNECTION_TEST_RESULT_KIND,
            "payload": json.dumps(
                {"ok": False, "failure": "unauthorized", "state": "needs_attention"}
            ),
        },
    )
    found = await service.test_result(
        db, org_id=tenant.organization.id, request_id=accepted.request_id
    )
    assert (found.pending, found.ok, found.failure, found.state) == (
        False,
        False,
        "unauthorized",
        "needs_attention",
    )
    elsewhere = await service.test_result(db, org_id=uuid.uuid4(), request_id=accepted.request_id)
    assert elsewhere.pending is True


async def test_a_refresh_adds_a_job_for_the_app_s_connection(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    connection_id = await _working(db, tenant)

    await service.refresh_github(db, org_id=tenant.organization.id, availability=SELF_HOSTED)

    [job] = await _jobs(db, tenant)
    assert (job.task, job.kwargs) == (REFRESH_CONNECTION_TASK, _ids(tenant, connection_id))


async def test_a_refresh_with_no_app_yet_is_refused(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    await _start(db, tenant)

    with pytest.raises(NotFoundError):
        await service.refresh_github(db, org_id=tenant.organization.id, availability=SELF_HOSTED)


async def test_a_test_of_a_connection_in_another_organization_finds_nothing(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    connection_id = await _working(db, tenant)

    with pytest.raises(NotFoundError):
        await service.dispatch_test(
            db, org_id=uuid.uuid4(), connection_id=connection_id, availability=SELF_HOSTED
        )


# --------------------------------------------------- replacing and disconnecting


async def test_replacing_the_app_key_reseals_in_place_and_tests_it(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR43, T3.11: the row and its places stay; the old key is gone; a test runs."""
    connection_id = await _working(db, tenant)

    accepted = await service.replace_credential(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        connection_id=connection_id,
        private_key=NEW_KEY,
        keys=KEYS,
        availability=SELF_HOSTED,
    )

    row = await _row(db, connection_id)
    owner = connection_secret_owner(str(tenant.organization.id), str(connection_id))
    assert row.sealed_credential is not None
    assert _box(CONNECTION_CREDENTIAL_PURPOSE).open(row.sealed_credential, owner=owner) == NEW_KEY
    assert row.github_installation_id == 70
    [job] = await _jobs(db, tenant)
    assert job.kwargs == {**_ids(tenant, connection_id), "request_id": accepted.request_id}
    assert await _actions(db, tenant) == ["connection.credential_replaced"]


@pytest.mark.parametrize(
    "key",
    ["not a key", f"{PEM_BEGIN}\n" + "A" * 20_000 + f"\n{PEM_END}"],
)
async def test_something_that_is_not_an_app_key_is_refused(
    db: AsyncSession, tenant: ProvisionedTenant, key: str
) -> None:
    connection_id = await _working(db, tenant)

    with pytest.raises(InvalidRequestError):
        await service.replace_credential(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            connection_id=connection_id,
            private_key=key,
            keys=KEYS,
            availability=SELF_HOSTED,
        )

    assert await _jobs(db, tenant) == []


async def test_disconnecting_deletes_every_secret_at_once_and_tells_the_hooks(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR32: credentials deleted in that transaction; F5's hook is called with the ids."""
    connection_id = await _working(db, tenant)
    told: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def hook(session: AsyncSession, org_id: uuid.UUID, disconnected: uuid.UUID) -> None:
        told.append((org_id, disconnected))

    await service.disconnect(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        connection_id=connection_id,
        hooks=(hook,),
    )

    row = await _row(db, connection_id)
    assert row.state == "disconnected"
    assert (row.sealed_credential, row.sealed_webhook_secret, row.sealed_manifest_code) == (
        None,
        None,
        None,
    )
    assert told == [(tenant.organization.id, connection_id)]
    assert await _actions(db, tenant) == ["connection.deleted"]
    with pytest.raises(NotFoundError):
        await service.disconnect(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            connection_id=connection_id,
            hooks=(),
        )


async def test_waiting_for_a_test_holds_no_transaction_and_keeps_what_was_loaded(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """The wait ends its read between polls. It must not expire what the request already
    loaded, such as the caller's identity, which async SQLAlchemy cannot reload lazily."""
    connection_id = await _working(db, tenant)
    loaded = await _row(db, connection_id)

    waited = await service.test_result(
        db, org_id=tenant.organization.id, request_id=str(uuid.uuid4()), wait_seconds=0.6
    )

    assert waited.pending is True
    assert loaded.github_app_slug == "nexovue-connections"
