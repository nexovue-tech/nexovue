"""Deploy webhooks against real Postgres: signed, stored, filed and tenant-scoped (P1-36;
F28 Step 2).

The offline suite proves the queries carry a tenant predicate, that nothing unsigned
reaches the store, and that every field is checked. This proves the database agrees:
real organizations, real rows, a real signature computed the way GitHub computes one
over the exact bytes, secrets sealed and opened, the hourly allowance counted in
`rate_limit_windows`, and each deploy filed under its release with the periods it
calls for (AC6 to AC14, AC50 to AC53, AC57, AC59, AC61).

Most cases run inside a transaction that is rolled back. The races commit, and delete
their organizations afterwards.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import socket
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from nexovue_platform import releases as release_rules
from nexovue_platform.checks.sealing import (
    DEPLOY_WEBHOOK_PURPOSE,
    Keyring,
    SealedBox,
    decode_key,
    generate_key,
)
from nexovue_platform.evidence import GATHER_EVIDENCE_TASK
from nexovue_platform.masking import PLACEHOLDER
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexovue_api.data.postgres.models import (
    AuditLogEntry,
    DeployEvent,
    ProjectWebhookSecret,
    Release,
    ReleasePeriod,
)
from nexovue_api.deploys import service
from nexovue_api.errors.exceptions import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    RateLimitExceededError,
    UnauthenticatedError,
)
from nexovue_api.ratelimit.limiter import Decision, RateLimiter
from nexovue_api.ratelimit.policy import DEPLOYS_ACCEPTED
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

VERSION: Final = "2026.08.29-1"
SERVICE: Final = "checkout"
BOX: Final = SealedBox(Keyring(current=decode_key(generate_key())), purpose=DEPLOY_WEBHOOK_PURPOSE)


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection((parts.hostname or "", parts.port or 5432), timeout=2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="the live deploy webhook suite",
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


@pytest.fixture
async def limiter() -> AsyncIterator[RateLimiter]:
    """The real exact limiter, counting in `rate_limit_windows` on its own connections."""
    engine = create_async_engine(ASYNC_DSN)
    try:
        yield RateLimiter(async_sessionmaker(engine), instances=1)
    finally:
        await engine.dispose()


async def _new_tenant(db: AsyncSession) -> ProvisionedTenant:
    unique = uuid.uuid4().hex[:12]
    return await provision_tenant(
        db,
        organization_name=f"Deploys Live {unique}",
        email=f"owner-{unique}@live.test",
        password="correct-horse-battery",
    )


async def _mint(db: AsyncSession, tenant: ProvisionedTenant) -> tuple[uuid.UUID, str]:
    """A secret, as a customer copies it out of the console: shown this once."""
    minted = await service.create_webhook_secret(
        db,
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        actor_user_id=tenant.user.id,
        box=BOX,
    )
    return minted.minted.id, minted.minted.secret


def _item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "delivery_id": uuid.uuid4().hex,
        "service_name": SERVICE,
        "version": VERSION,
        "deployed_at": datetime.now(UTC).isoformat(),
    }
    item.update(overrides)
    return item


def _body(*items: dict[str, Any], dry_run: bool = False) -> bytes:
    payload: dict[str, Any] = {"deploys": list(items) or [_item()]}
    if dry_run:
        payload["dry_run"] = True
    return json.dumps(payload).encode()


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _always(cost: int) -> Decision:
    return Decision(allowed=True, quota=DEPLOYS_ACCEPTED, remaining=0, reset_seconds=1)


def _charging(limiter: RateLimiter, project_id: uuid.UUID) -> service.Charge:
    async def charge(cost: int) -> Decision:
        return await limiter.consume(
            scope="project", identity=str(project_id), quota=DEPLOYS_ACCEPTED, cost=cost
        )

    return charge


async def _send(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    secret: str,
    body: bytes,
    *,
    charge: service.Charge = _always,
    signature: str | None = None,
    project_id: uuid.UUID | None = None,
) -> Any:
    return await service.record_from_webhook(
        db,
        project_id=project_id or tenant.project.id,
        body=body,
        signature=signature if signature is not None else _sign(body, secret),
        box=BOX,
        charge=charge,
    )


async def _deploys(db: AsyncSession, org_id: uuid.UUID) -> Sequence[DeployEvent]:
    return (
        await db.scalars(
            select(DeployEvent).where(DeployEvent.org_id == org_id).order_by(DeployEvent.id)
        )
    ).all()


async def _release(db: AsyncSession, tenant: ProvisionedTenant, version: str) -> Release | None:
    found: Release | None = await db.scalar(
        select(Release).where(
            Release.org_id == tenant.organization.id,
            Release.project_id == tenant.project.id,
            Release.service_name == SERVICE,
            Release.version == version,
        )
    )
    if found is not None:
        await db.refresh(found)
    return found


async def _periods(db: AsyncSession, release_id: uuid.UUID) -> list[ReleasePeriod]:
    rows = list(
        await db.scalars(
            select(ReleasePeriod)
            .where(ReleasePeriod.release_id == release_id)
            .order_by(ReleasePeriod.started_at)
        )
    )
    for row in rows:
        await db.refresh(row)
    return rows


async def _audit(db: AsyncSession, org_id: uuid.UUID, action: str) -> list[AuditLogEntry]:
    return list(
        await db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.org_id == org_id, AuditLogEntry.action == action
            )
        )
    )


# ------------------------------------------------------------------- refusals


async def test_a_signed_batch_is_stored_under_the_projects_own_tenancy(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    secret_id, secret = await _mint(db, tenant)

    answer = await _send(db, tenant, secret, _body())

    assert answer.stored == 1
    stored = await _deploys(db, tenant.organization.id)
    assert [row.id for row in stored] == [answer.deploys[0].deploy_id]
    assert stored[0].project_id == tenant.project.id
    assert stored[0].webhook_secret_id == secret_id
    assert stored[0].release_id == answer.deploys[0].release_id


@pytest.mark.parametrize("how", ["stranger", "unsigned", "altered", "unknown project"])
async def test_every_refusal_holds_in_the_same_words_writing_nothing(
    db: AsyncSession, how: str
) -> None:
    """AC6, against the store: nothing written, not even a refusal or a last use."""
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    _, secret = await _mint(db, owner)
    _, strangers = await _mint(db, stranger)
    body = _body()
    signature = {
        "stranger": _sign(body, strangers),
        "unsigned": "",
        "altered": _sign(body.replace(SERVICE.encode(), b"payments"), secret),
        "unknown project": _sign(body, secret),
    }[how]

    with pytest.raises(UnauthenticatedError) as refused:
        await _send(
            db,
            owner,
            secret,
            body,
            signature=signature,
            project_id=uuid.uuid4() if how == "unknown project" else None,
        )

    assert refused.value.detail == service.REFUSED_DETAIL
    assert await _deploys(db, owner.organization.id) == []
    shown = await service.webhook_secrets(
        db, org_id=owner.organization.id, project_id=owner.project.id
    )
    assert shown.last_refusal is None
    assert all(listed.last_used_at is None for listed in shown.secrets)


async def test_a_captured_body_sent_again_stores_nothing_whatever_it_claims(
    db: AsyncSession,
) -> None:
    """AC7, AC10: the id is inside the signed bytes, so a replay is answered as a repeat
    and changes nothing on the release."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    body = _body()
    first = await _send(db, tenant, secret, body)
    before = await _release(db, tenant, VERSION)
    assert before is not None
    counted, reported = before.report_count, before.last_reported_at

    again = await _send(db, tenant, secret, body)

    assert again.stored == 0
    assert again.deploys[0].repeated is True
    assert again.deploys[0].deploy_id == first.deploys[0].deploy_id
    assert len(await _deploys(db, tenant.organization.id)) == 1
    after = await _release(db, tenant, VERSION)
    assert after is not None
    assert (after.report_count, after.last_reported_at) == (counted, reported)


async def test_a_batch_mixing_new_and_repeated_items_stores_only_the_new(
    db: AsyncSession,
) -> None:
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    seen_before = _item()
    await _send(db, tenant, secret, _body(seen_before))

    answer = await _send(db, tenant, secret, _body(seen_before, _item(version="2.0.0")))

    assert answer.stored == 1
    assert [one.repeated for one in answer.deploys] == [True, False]
    assert len(await _deploys(db, tenant.organization.id)) == 2


async def test_a_hundred_services_in_one_batch_store_a_hundred_deploys_and_one_entry(
    db: AsyncSession,
) -> None:
    """AC8: one transaction, one `deploy.batch_recorded` with the secret, the count and
    the first deploy; and a body with every field at its limit is accepted."""
    tenant = await _new_tenant(db)
    secret_id, secret = await _mint(db, tenant)
    metadata = {"k": "v" * (1024 - len('{"k":""}'))}
    items = [
        _item(
            delivery_id=f"{n:03d}" + "d" * 197,
            service_name=f"{n:03d}" + "s" * 197,
            version="v" * 200,
            commit_sha="a" * 64,
            url="https://example.com/" + "u" * 2028,
            source="s" * 64,
            metadata=metadata,
        )
        for n in range(100)
    ]

    answer = await _send(db, tenant, secret, _body(*items))

    assert answer.stored == 100
    assert len(await _deploys(db, tenant.organization.id)) == 100
    (entry,) = await _audit(db, tenant.organization.id, "deploy.batch_recorded")
    assert entry.detail == {
        "secret_id": str(secret_id),
        "count": 100,
        "first_deploy_id": str(answer.deploys[0].deploy_id),
    }


async def test_a_bad_item_stores_nothing_and_shows_as_the_last_refusal(db: AsyncSession) -> None:
    """AC9: the whole body is refused, and the signed sender's refusal is recorded."""
    tenant = await _new_tenant(db)
    secret_id, secret = await _mint(db, tenant)
    # The refusal commits before it raises, which expires loaded rows; keep the ids.
    org_id, project_id = tenant.organization.id, tenant.project.id

    with pytest.raises(InvalidRequestError) as refused:
        await _send(db, tenant, secret, _body(_item(), _item(commit_sha="short")))

    assert refused.value.detail.startswith("Item 2, field `commit_sha`")
    assert await _deploys(db, org_id) == []
    shown = await service.webhook_secrets(db, org_id=org_id, project_id=project_id)
    assert shown.last_refusal is not None
    assert (shown.last_refusal.position, shown.last_refusal.field) == (2, "commit_sha")
    assert shown.last_refusal.secret_id == secret_id


async def test_a_dry_run_stores_nothing_and_moves_only_the_last_check(
    db: AsyncSession, limiter: RateLimiter
) -> None:
    """AC13: no deploy, no release, "last check received" moves, "last deploy received"
    does not, and nothing is charged."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)

    answer = await _send(
        db, tenant, secret, _body(dry_run=True), charge=_charging(limiter, tenant.project.id)
    )

    assert answer.message == "valid, not stored"
    assert await _deploys(db, tenant.organization.id) == []
    assert await _release(db, tenant, VERSION) is None
    shown = await service.webhook_secrets(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    assert shown.last_check_at is not None
    assert shown.last_deploy_at is None
    spent = await limiter.consume(
        scope="project", identity=str(tenant.project.id), quota=DEPLOYS_ACCEPTED, cost=1
    )
    assert spent.remaining == DEPLOYS_ACCEPTED.limit - 1


# ------------------------------------------------------------- the hourly limit


async def test_a_batch_that_would_cross_the_limit_is_refused_whole_and_charges_nothing(
    db: AsyncSession, limiter: RateLimiter
) -> None:
    """AC14: with 950 used, a batch of 100 is refused with Retry-After and stores
    nothing; the 50 left still store; the 1,001st is refused."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    charge = _charging(limiter, tenant.project.id)
    assert (await charge(950)).allowed

    with pytest.raises(RateLimitExceededError) as refused:
        await _send(
            db, tenant, secret, _body(*[_item(version=f"a{n}") for n in range(100)]), charge=charge
        )
    assert refused.value.retry_after_seconds >= 1
    assert await _deploys(db, tenant.organization.id) == []

    fifty = await _send(
        db, tenant, secret, _body(*[_item(version=f"b{n}") for n in range(50)]), charge=charge
    )
    assert fifty.stored == 50

    with pytest.raises(RateLimitExceededError):
        await _send(db, tenant, secret, _body(_item(version="one-more")), charge=charge)


async def test_neither_an_unsigned_flood_nor_a_replayed_capture_spends_the_allowance(
    db: AsyncSession, limiter: RateLimiter
) -> None:
    """AC14: the limit is counted after the signature and only for new items, so a
    capture replayed 2,000 times costs one, and a genuine new deploy still lands."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    charge = _charging(limiter, tenant.project.id)
    captured = _body()
    await _send(db, tenant, secret, captured, charge=charge)

    for _ in range(2_000):
        await _send(db, tenant, secret, captured, charge=charge)
    for _ in range(200):
        with pytest.raises(UnauthenticatedError):
            await _send(db, tenant, secret, _body(), charge=charge, signature="sha256=" + "0" * 64)

    genuine = await _send(db, tenant, secret, _body(_item(version="genuine")), charge=charge)
    assert genuine.stored == 1
    spent = await charge(1)
    assert spent.remaining == DEPLOYS_ACCEPTED.limit - 3


# ---------------------------------------------------------------- the answers


async def test_each_answer_says_whether_the_service_and_version_are_known(
    db: AsyncSession,
) -> None:
    """AC52: a misspelt service answers `known: false`, so CI's own output shows it."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    await db.execute(
        text(
            "INSERT INTO service_last_seen (org_id, project_id, service_name, last_seen_at) "
            "VALUES (:org, :project, :service, now())"
        ),
        {"org": tenant.organization.id, "project": tenant.project.id, "service": SERVICE},
    )
    await _seen(db, tenant, "1.0.0", datetime.now(UTC) - timedelta(hours=1))

    answer = await _send(
        db, tenant, secret, _body(_item(version="1.0.0"), _item(service_name="chekout"))
    )

    right, misspelt = answer.deploys
    assert (right.known, right.version_seen, right.recent_versions) == (True, True, ["1.0.0"])
    assert right.release_id is not None
    assert (misspelt.known, misspelt.version_seen) == (False, False)


async def test_a_token_in_metadata_is_never_stored_in_the_clear(db: AsyncSession) -> None:
    """AC53, against the stored row."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)

    await _send(
        db,
        tenant,
        secret,
        _body(_item(metadata={"deploy_token": "ghp_abcdef", "note": "Bearer " + "x" * 30})),
    )

    (stored,) = await _deploys(db, tenant.organization.id)
    assert stored.event_metadata == {
        "deploy_token": PLACEHOLDER,
        "note": f"Bearer {PLACEHOLDER}",
    }


# -------------------------------------------------------------------- secrets


async def test_a_secret_is_sealed_at_rest_and_shown_only_at_creation(db: AsyncSession) -> None:
    """AC3, AC4: the column holds no usable secret, and no read returns it afterwards."""
    tenant = await _new_tenant(db)
    secret_id, secret = await _mint(db, tenant)

    row = await db.scalar(select(ProjectWebhookSecret).where(ProjectWebhookSecret.id == secret_id))
    assert row is not None
    assert secret.encode() not in row.sealed_secret
    assert row.secret_hint == secret[-4:]
    listed = await service.webhook_secrets(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    assert secret not in listed.model_dump_json()
    assert "secret" not in listed.secrets[0].model_dump()


async def test_a_sixth_live_secret_is_refused_with_a_reason(db: AsyncSession) -> None:
    """AC5. A withdrawn one makes room."""
    tenant = await _new_tenant(db)
    minted = [await _mint(db, tenant) for _ in range(service.MAX_LIVE_SECRETS)]

    with pytest.raises(ConflictError) as refused:
        await _mint(db, tenant)
    assert "5 live deploy secrets" in refused.value.detail

    await service.revoke_webhook_secret(
        db, org_id=tenant.organization.id, secret_id=minted[0][0], actor_user_id=tenant.user.id
    )
    await _mint(db, tenant)


async def test_a_rotation_drops_no_delivery(db: AsyncSession) -> None:
    """AC50: mint the new, send on it, withdraw the old; every delivery is accepted, and
    "last used" moves to the new one."""
    tenant = await _new_tenant(db)
    old_id, old = await _mint(db, tenant)
    await _send(db, tenant, old, _body())
    new_id, new = await _mint(db, tenant)
    await _send(db, tenant, old, _body())
    await _send(db, tenant, new, _body())
    await service.revoke_webhook_secret(
        db, org_id=tenant.organization.id, secret_id=old_id, actor_user_id=tenant.user.id
    )

    await _send(db, tenant, new, _body())
    with pytest.raises(UnauthenticatedError):
        await _send(db, tenant, old, _body())

    assert len(await _deploys(db, tenant.organization.id)) == 4
    shown = await service.webhook_secrets(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    assert [(one.id, one.last_used_at is not None) for one in shown.secrets] == [(new_id, True)]


async def test_another_organizations_secrets_and_deploys_cannot_be_read(
    db: AsyncSession,
) -> None:
    """A 404, not a 403: whether the project exists is not theirs to learn."""
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    _, secret = await _mint(db, owner)
    await _send(db, owner, secret, _body())

    with pytest.raises(NotFoundError):
        await service.webhook_secrets(
            db, org_id=stranger.organization.id, project_id=owner.project.id
        )
    with pytest.raises(NotFoundError):
        await service.list_for_project(
            db, org_id=stranger.organization.id, project_id=owner.project.id, limit=10
        )
    mine = await service.list_for_project(
        db, org_id=owner.organization.id, project_id=owner.project.id, limit=10
    )
    assert [row.version for row in mine.deploys] == [VERSION]


async def test_no_audit_entry_carries_the_secret(db: AsyncSession) -> None:
    """A log that records the credential is worse than no log."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    await _send(db, tenant, secret, _body())
    await db.flush()

    statement = select(AuditLogEntry).where(AuditLogEntry.org_id == tenant.organization.id)
    for row in (await db.scalars(statement)).all():
        assert secret not in row.target
        assert secret not in (row.actor_label or "")
        assert secret not in json.dumps(row.detail or {})


# ------------------------------------------------------------ releases, periods


async def _seen(
    db: AsyncSession, tenant: ProvisionedTenant, version: str, at: datetime
) -> uuid.UUID:
    """A version the sync counted: first seen at `at`, its period's start measured."""
    release_id = await db.scalar(
        text(
            "INSERT INTO releases (org_id, project_id, service_name, version, first_seen_at, "
            "last_seen_at) VALUES (:org, :project, :service, :version, :at, :at) RETURNING id"
        ),
        {
            "org": tenant.organization.id,
            "project": tenant.project.id,
            "service": SERVICE,
            "version": version,
            "at": at,
        },
    )
    await db.execute(
        text(
            "INSERT INTO release_periods (org_id, release_id, project_id, service_name, "
            "started_at, start_basis) VALUES (:org, :release, :project, :service, :at, 'seen')"
        ),
        {
            "org": tenant.organization.id,
            "release": release_id,
            "project": tenant.project.id,
            "service": SERVICE,
            "at": at,
        },
    )
    assert isinstance(release_id, uuid.UUID)
    return release_id


@pytest.mark.parametrize("state", ["started", "finished"])
async def test_a_report_opens_a_reported_period_naming_what_it_replaced(
    db: AsyncSession, state: str
) -> None:
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    earlier = datetime.now(UTC) - timedelta(hours=2)
    later = datetime.now(UTC) - timedelta(hours=1)

    await _send(db, tenant, secret, _body(_item(version="v1", deployed_at=earlier.isoformat())))
    await _send(
        db,
        tenant,
        secret,
        _body(_item(version="v2", deployed_at=later.isoformat(), state=state)),
    )

    v2 = await _release(db, tenant, "v2")
    assert v2 is not None
    assert (v2.first_reported_at, v2.report_count, v2.last_state) == (later, 1, state)
    (period,) = await _periods(db, v2.id)
    assert (period.started_at, period.start_basis, period.previous_version) == (
        later,
        "reported",
        "v1",
    )


# Founder decision, 2026-11-09: only `started` and `finished` open a period. A failed
# or rolled-back deploy did not replace what was running, so its report is stored and
# its state shown, but it opens no period, ends none, and brings no version back.
NOT_DEPLOYED: Final = ("failed", "rolled_back")


@pytest.mark.parametrize("state", NOT_DEPLOYED)
async def test_a_deploy_that_did_not_go_out_opens_no_period_and_ends_none(
    db: AsyncSession, state: str
) -> None:
    """On a service with no version in traffic, where reports alone decide periods."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    earlier = datetime.now(UTC) - timedelta(hours=2)
    later = datetime.now(UTC) - timedelta(hours=1)
    first = await _send(
        db, tenant, secret, _body(_item(version="v1", deployed_at=earlier.isoformat()))
    )

    await _send(
        db,
        tenant,
        secret,
        _body(_item(version="v2", deployed_at=later.isoformat(), state=state)),
    )

    v2 = await _release(db, tenant, "v2")
    assert v2 is not None
    assert (v2.first_reported_at, v2.report_count, v2.last_state) == (later, 1, state)
    assert await _periods(db, v2.id) == []
    (still,) = await _periods(db, first.deploys[0].release_id)
    assert (still.started_at, still.ended_at) == (earlier, None)


@pytest.mark.parametrize("state", NOT_DEPLOYED)
async def test_a_deploy_that_did_not_go_out_opens_no_period_where_traffic_has_versions(
    db: AsyncSession, state: str
) -> None:
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    counted = datetime.now(UTC) - timedelta(hours=2)
    v1 = await _seen(db, tenant, "v1", counted)

    await _send(db, tenant, secret, _body(_item(version="v2", state=state)))

    v2 = await _release(db, tenant, "v2")
    assert v2 is not None and v2.last_state == state
    assert await _periods(db, v2.id) == []
    (still,) = await _periods(db, v1)
    assert (still.started_at, still.ended_at) == (counted, None)


@pytest.mark.parametrize("state", NOT_DEPLOYED)
async def test_a_deploy_that_did_not_go_out_brings_no_version_back(
    db: AsyncSession, state: str
) -> None:
    """AC59's v1, v2, v1, with the second v1 failed: v1 does not return, v2 serves on."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    base = datetime.now(UTC) - timedelta(hours=3)
    times = [base, base + timedelta(hours=1), base + timedelta(hours=2)]

    for version, at, sent in zip(
        ("v1", "v2", "v1"), times, ("finished", "finished", state), strict=True
    ):
        await _send(
            db,
            tenant,
            secret,
            _body(_item(version=version, deployed_at=at.isoformat(), state=sent)),
        )

    v1, v2 = await _release(db, tenant, "v1"), await _release(db, tenant, "v2")
    assert v1 is not None and v2 is not None
    (only,) = await _periods(db, v1.id)
    assert (only.started_at, only.ended_at) == (times[0], times[1])
    (serving,) = await _periods(db, v2.id)
    assert (serving.started_at, serving.ended_at) == (times[1], None)
    assert v1.last_state == state


async def test_a_report_never_moves_a_measured_start(db: AsyncSession) -> None:
    """AC57: a seen version keeps its start whatever a later or back-dated report says;
    `rolled_back` on it sets its state only."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    counted = datetime.now(UTC) - timedelta(hours=1)
    release_id = await _seen(db, tenant, VERSION, counted)

    await _send(
        db,
        tenant,
        secret,
        _body(
            _item(deployed_at=(counted - timedelta(days=6)).isoformat()),
            _item(deployed_at=datetime.now(UTC).isoformat(), state="rolled_back"),
        ),
    )

    (period,) = await _periods(db, release_id)
    assert (period.started_at, period.start_basis, period.ended_at) == (counted, "seen", None)
    release = await _release(db, tenant, VERSION)
    assert release is not None
    assert (release.first_seen_at, release.last_state, release.report_count) == (
        counted,
        "rolled_back",
        2,
    )


async def test_a_version_reported_again_on_a_service_with_no_version_comes_back(
    db: AsyncSession,
) -> None:
    """AC59: reported v1, v2, then v1 again gives v1 a second period, a return; v2's
    period ends where v1's return starts."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    base = datetime.now(UTC) - timedelta(hours=3)
    times = [base, base + timedelta(hours=1), base + timedelta(hours=2)]

    for version, at in zip(("v1", "v2", "v1"), times, strict=True):
        await _send(db, tenant, secret, _body(_item(version=version, deployed_at=at.isoformat())))

    v1, v2 = await _release(db, tenant, "v1"), await _release(db, tenant, "v2")
    assert v1 is not None and v2 is not None
    first, back = await _periods(db, v1.id)
    (between,) = await _periods(db, v2.id)
    assert (first.started_at, first.ended_at, first.is_return) == (times[0], times[1], False)
    assert (between.started_at, between.ended_at) == (times[1], times[2])
    assert (back.started_at, back.ended_at, back.is_return) == (times[2], None, True)
    assert back.previous_version == "v2"


async def test_one_batch_reporting_v1_v2_v1_reads_like_three_requests(db: AsyncSession) -> None:
    """The writes are sorted by version for their locks, and the periods by claimed time,
    so one body gives what three separate requests give."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    base = datetime.now(UTC) - timedelta(hours=3)
    times = [base, base + timedelta(hours=1), base + timedelta(hours=2)]

    await _send(
        db,
        tenant,
        secret,
        _body(
            *[
                _item(version=version, deployed_at=at.isoformat())
                for version, at in zip(("v1", "v2", "v1"), times, strict=True)
            ]
        ),
    )

    v1 = await _release(db, tenant, "v1")
    assert v1 is not None
    assert [(p.started_at, p.is_return) for p in await _periods(db, v1.id)] == [
        (times[0], False),
        (times[2], True),
    ]


async def test_a_project_at_the_release_cap_evicts_before_a_report_creates(
    db: AsyncSession,
) -> None:
    """FR56, from the webhook's door: junk of an unknown service goes first, and the new
    release is created."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    await db.execute(
        text(
            "INSERT INTO releases (org_id, project_id, service_name, version, "
            "first_reported_at, last_reported_at, report_count) "
            "SELECT :org, :project, 'junk', 'j' || n, now(), now(), 1 "
            "FROM generate_series(1, :count) AS n"
        ),
        {
            "org": tenant.organization.id,
            "project": tenant.project.id,
            "count": release_rules.RELEASES_PER_PROJECT,
        },
    )

    await _send(db, tenant, secret, _body(_item(version="real")))

    assert await _release(db, tenant, "real") is not None
    held = await db.scalar(
        text("SELECT count(*) FROM releases WHERE project_id = :project"),
        {"project": tenant.project.id},
    )
    assert held == release_rules.RELEASES_PER_PROJECT
    shown = await service.webhook_secrets(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    assert shown.over_release_cap is True


# ------------------------------------------------------------------- removing


async def test_removing_a_deploy_deletes_a_release_nothing_else_supports(
    db: AsyncSession,
) -> None:
    """AC61: the release and its period go, the period it had ended reopens, and it is
    audited with who removed it."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    earlier = datetime.now(UTC) - timedelta(hours=2)
    v1 = await _send(
        db, tenant, secret, _body(_item(version="v1", deployed_at=earlier.isoformat()))
    )
    v2 = await _send(db, tenant, secret, _body(_item(version="v2")))

    await service.remove_deploy(
        db,
        org_id=tenant.organization.id,
        deploy_id=v2.deploys[0].deploy_id,
        actor_user_id=tenant.user.id,
    )

    assert await _release(db, tenant, "v2") is None
    (period,) = await _periods(db, v1.deploys[0].release_id)
    assert period.ended_at is None
    (entry,) = await _audit(db, tenant.organization.id, "deploy.removed")
    assert entry.actor_user_id == tenant.user.id
    assert entry.detail is not None and entry.detail["deploy_id"] == str(v2.deploys[0].deploy_id)


async def test_removing_one_of_two_reports_works_the_release_out_again(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    first_at = datetime.now(UTC) - timedelta(hours=2)
    second_at = datetime.now(UTC) - timedelta(hours=1)
    first = await _send(db, tenant, secret, _body(_item(deployed_at=first_at.isoformat())))
    await _send(db, tenant, secret, _body(_item(deployed_at=second_at.isoformat(), state="failed")))

    await service.remove_deploy(
        db,
        org_id=tenant.organization.id,
        deploy_id=first.deploys[0].deploy_id,
        actor_user_id=tenant.user.id,
    )

    release = await _release(db, tenant, VERSION)
    assert release is not None
    assert (
        release.first_reported_at,
        release.last_reported_at,
        release.report_count,
        release.last_state,
    ) == (second_at, second_at, 1, "failed")
    # What is left is a failed deploy, which opens no period (founder, 2026-11-09).
    assert await _periods(db, release.id) == []


@pytest.mark.parametrize("state", NOT_DEPLOYED)
async def test_working_periods_out_again_skips_a_deploy_that_did_not_go_out(
    db: AsyncSession, state: str
) -> None:
    """D37's recalculation follows the same rule as the webhook: with v3 removed, v1's
    period reopens, and v2's failed report between them neither ends it nor opens one."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    base = datetime.now(UTC) - timedelta(hours=3)
    v1 = await _send(db, tenant, secret, _body(_item(version="v1", deployed_at=base.isoformat())))
    await _send(
        db,
        tenant,
        secret,
        _body(
            _item(version="v2", deployed_at=(base + timedelta(hours=1)).isoformat(), state=state)
        ),
    )
    v3 = await _send(
        db,
        tenant,
        secret,
        _body(_item(version="v3", deployed_at=(base + timedelta(hours=2)).isoformat())),
    )

    await service.remove_deploy(
        db,
        org_id=tenant.organization.id,
        deploy_id=v3.deploys[0].deploy_id,
        actor_user_id=tenant.user.id,
    )

    v2 = await _release(db, tenant, "v2")
    assert v2 is not None
    assert await _periods(db, v2.id) == []
    (reopened,) = await _periods(db, v1.deploys[0].release_id)
    assert (reopened.started_at, reopened.ended_at) == (base, None)


async def test_withdrawing_a_secret_can_remove_what_it_sent_since_a_time(
    db: AsyncSession,
) -> None:
    """D37, AC61: what the leaked secret signed after the chosen time goes; what it
    signed before, and what another secret signed, stays."""
    tenant = await _new_tenant(db)
    leaked_id, leaked = await _mint(db, tenant)
    _, kept = await _mint(db, tenant)
    await _send(db, tenant, leaked, _body(_item(version="before")))
    await db.execute(
        text("UPDATE deploy_events SET created_at = now() - interval '1 hour' WHERE org_id = :org"),
        {"org": tenant.organization.id},
    )
    await _send(db, tenant, leaked, _body(_item(version="planted")))
    await _send(db, tenant, kept, _body(_item(version="honest")))

    await service.revoke_webhook_secret(
        db,
        org_id=tenant.organization.id,
        secret_id=leaked_id,
        actor_user_id=tenant.user.id,
        remove_since=datetime.now(UTC) - timedelta(minutes=10),
    )

    versions = sorted(row.version for row in await _deploys(db, tenant.organization.id))
    assert versions == ["before", "honest"]
    assert await _release(db, tenant, "planted") is None
    (entry,) = await _audit(db, tenant.organization.id, "deploy.removed")
    assert entry.detail is not None
    assert (entry.detail["count"], entry.detail["secret_id"]) == (1, str(leaked_id))


async def test_another_organizations_deploy_cannot_be_removed(db: AsyncSession) -> None:
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    _, secret = await _mint(db, owner)
    sent = await _send(db, owner, secret, _body())

    with pytest.raises(NotFoundError):
        await service.remove_deploy(
            db,
            org_id=stranger.organization.id,
            deploy_id=sent.deploys[0].deploy_id,
            actor_user_id=stranger.user.id,
        )
    assert len(await _deploys(db, owner.organization.id)) == 1


# ---------------------------------------------------------------------- races


@dataclass
class _Committed:
    tenant: ProvisionedTenant
    secret: str


async def _committed_tenant(engine: Any) -> _Committed:
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        tenant = await _new_tenant(setup)
        _, secret = await _mint(setup, tenant)
        await setup.commit()
    return _Committed(tenant, secret)


async def _drop(engine: Any, made: _Committed) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM organizations WHERE id = :id"), {"id": made.tenant.organization.id}
        )


async def test_two_identical_batches_at_once_store_each_deploy_once_and_both_succeed() -> None:
    """AC10: both race past the read for a repeat; the unique key lets one in, and the
    other answers with the winner's deploy rather than a 500."""
    engine = create_async_engine(ASYNC_DSN)
    made = await _committed_tenant(engine)
    try:
        body = _body(_item(), _item(service_name="payments"))

        async def deliver() -> list[uuid.UUID | None]:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                answer = await _send(session, made.tenant, made.secret, body)
                await session.commit()
                return [one.deploy_id for one in answer.deploys]

        first, second = await asyncio.gather(deliver(), deliver())

        async with AsyncSession(engine) as check:
            stored = await _deploys(check, made.tenant.organization.id)
            release = await check.scalar(
                select(Release.report_count).where(
                    Release.org_id == made.tenant.organization.id, Release.service_name == SERVICE
                )
            )
        assert first == second
        assert sorted(row.id for row in stored) == sorted(i for i in first if i is not None)
        assert release == 1
    finally:
        await _drop(engine, made)
        await engine.dispose()


async def test_batches_in_opposite_orders_racing_a_sync_end_in_no_server_error() -> None:
    """AC51: every writer takes release rows in one order, service then version, so two
    batches naming the same services in opposite orders, and a sync-shaped writer
    upserting the same releases, all finish."""
    engine = create_async_engine(ASYNC_DSN)
    made = await _committed_tenant(engine)
    services = [f"svc-{n:02d}" for n in range(20)]
    try:

        async def batch(order: list[str], tag: str) -> None:
            body = _body(*[_item(service_name=name, version="v1") for name in order])
            async with AsyncSession(engine, expire_on_commit=False) as session:
                await _send(session, made.tenant, made.secret, body)
                await session.commit()

        async def sync_like() -> None:
            # The sync's own statement shape (`releases/sync.py`, `_SAVE_RELEASE`), in its
            # order: sorted by service and version, in one transaction.
            async with engine.begin() as connection:
                for name in sorted(services):
                    await connection.execute(
                        text(
                            "INSERT INTO releases (org_id, project_id, service_name, version, "
                            "first_seen_at, last_seen_at, last_activity_at) "
                            "VALUES (:org, :project, :service, 'v1', now(), now(), now()) "
                            "ON CONFLICT (project_id, service_name, version) DO UPDATE "
                            "SET last_seen_at = GREATEST(releases.last_seen_at, "
                            "EXCLUDED.last_seen_at) WHERE releases.org_id = :org"
                        ),
                        {
                            "org": made.tenant.organization.id,
                            "project": made.tenant.project.id,
                            "service": name,
                        },
                    )

        for _ in range(5):
            await asyncio.gather(
                batch(services, "forward"), batch(services[::-1], "backward"), sync_like()
            )

        async with AsyncSession(engine) as check:
            stored = await _deploys(check, made.tenant.organization.id)
        assert len(stored) == 5 * 2 * len(services)
    finally:
        await _drop(engine, made)
        await engine.dispose()


# ------------------------------------------------- F3: a deploy reads a finding's facts again


async def _finding(
    db: AsyncSession, tenant: ProvisionedTenant, *, started_at: datetime, open_: bool = True
) -> uuid.UUID:
    finding_id: uuid.UUID = await db.scalar(
        text(
            """
            INSERT INTO findings (org_id, project_id, scope, service_name, severity, state,
                closed_reason, title, signals, started_at, closed_at)
            VALUES (:org_id, :project_id, 'service', :service, 'warning',
                CASE WHEN :open THEN 'open' ELSE 'closed' END,
                CASE WHEN :open THEN NULL ELSE 'recovered' END,
                'slower', '[]', :started_at,
                CASE WHEN :open THEN NULL ELSE CAST(:started_at AS timestamptz) END)
            RETURNING id
            """
        ),
        {
            "org_id": tenant.organization.id,
            "project_id": tenant.project.id,
            "service": SERVICE,
            "open": open_,
            "started_at": started_at,
        },
    )
    return finding_id


async def _gathers(db: AsyncSession, tenant: ProvisionedTenant) -> list[tuple[str, str]]:
    rows = await db.execute(
        text("SELECT kwargs FROM jobs WHERE org_id = :org_id AND task = :task"),
        {"org_id": tenant.organization.id, "task": GATHER_EVIDENCE_TASK},
    )
    return sorted((row.kwargs["finding_id"], row.kwargs["reason"]) for row in rows)


@pytest.mark.parametrize(
    ("minutes_after", "open_", "read"),
    [(10, True, True), (40, True, False), (5, False, False), (-5, True, False)],
)
async def test_a_signed_deploy_reads_again_only_an_open_finding_it_came_before(
    db: AsyncSession, minutes_after: int, open_: bool, read: bool
) -> None:
    """F3 AC79's deploy case: a `deploy` gather only for an open finding on that service
    that began within 30 minutes after the deploy."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    deployed_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    finding_id = await _finding(
        db, tenant, started_at=deployed_at + timedelta(minutes=minutes_after), open_=open_
    )

    await _send(db, tenant, secret, _body(_item(deployed_at=deployed_at.isoformat())))

    assert await _gathers(db, tenant) == ([(str(finding_id), "deploy")] if read else [])


async def test_a_captured_body_sent_again_reads_nothing_again(db: AsyncSession) -> None:
    """F3 AC93's intake half: a replayed signed body stores nothing, so it asks for no read."""
    tenant = await _new_tenant(db)
    _, secret = await _mint(db, tenant)
    deployed_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    await _finding(db, tenant, started_at=deployed_at + timedelta(minutes=10))
    body = _body(_item(deployed_at=deployed_at.isoformat()))
    await _send(db, tenant, secret, body)
    await db.execute(
        text("DELETE FROM jobs WHERE org_id = :org_id"), {"org_id": tenant.organization.id}
    )

    await _send(db, tenant, secret, body)

    assert await _gathers(db, tenant) == []
