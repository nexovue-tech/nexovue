"""A tracker's webhook only rings the doorbell, against real Postgres (F22 plan T5.1;
ADR-035; design, *Webhook handling*; AC46's webhook half, AC47, AC48, AC85, AC139).

Every test runs in a transaction that is rolled back, through the real route where the
answer matters. What is proved here:

- a signed delivery brings its issue's links' next read forward to now, and does nothing
  else: no case, link status or seen state moves, no job is added, nothing is audited
  (AC47's second half, AC48);
- a bad, missing, SHA-1-only or other secret's signature, an unknown or disconnected
  connection, one with no secret, and a kind with no webhook form are all refused in the
  same words, and change nothing (AC47);
- replaying one delivery 100 times moves the read once, and a link read within 30 seconds
  is not moved (AC48's two reads a minute);
- a link not followed, stopped, waiting for a person or in backoff is never revived, and a
  link already due keeps its place (AC85, FR39);
- the organization is the connection's: another organization's link to the same issue id
  is untouched (AC139);
- a body over 1 MiB is refused before any signature is looked at.

The webhook's effect on a case through the real sync job, within a minute, is
`apps/scheduler/tests/test_connections_sync_live.py` (AC46, and AC48's lease with two jobs
started together): that job is the scheduler's, and the simulated GitHub lives there.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import hashlib
import hmac
import json
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
from environment import DEPLOYED_KEYS, TEST_ENVIRONMENT
from live import require_stack
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, keyring_from
from nexovue_platform.connections import (
    CONNECTION_WEBHOOK_SECRET_PURPOSE,
    connection_secret_owner,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.connections import router, service
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.errors.exceptions import DependencyUnavailableError, UnauthenticatedError
from nexovue_api.settings import API_PREFIX, Settings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
# The key the app under test is built with, so a secret sealed here opens there.
KEYS: Final = ConfiguredKeys(DEPLOYED_KEYS["CONNECTION_SECRET_KEY"])
SECRET: Final = "a-webhook-secret-github-gave"
GITHUB_HEADER: Final = "X-Hub-Signature-256"
NOW: Final = datetime.now(UTC).replace(microsecond=0)
LATER: Final = NOW + timedelta(minutes=5)


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_connections_webhook_live.py",
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


@dataclass
class Tracker:
    """One organization's GitHub connection, a case on a finding, and its synced link."""

    tenant: ProvisionedTenant
    connection_id: uuid.UUID
    finding_id: uuid.UUID
    link_id: uuid.UUID
    issue_id: str

    @property
    def org_id(self) -> uuid.UUID:
        return self.tenant.organization.id


async def _tenant(db: AsyncSession, name: str) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name=name,
        email=f"webhook-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )


def _sealed(org_id: uuid.UUID, connection_id: uuid.UUID, secret: str = SECRET) -> bytes:
    box = SealedBox(
        keyring_from(KEYS.current, KEYS.previous), purpose=CONNECTION_WEBHOOK_SECRET_PURPOSE
    )
    return box.seal(secret, owner=connection_secret_owner(str(org_id), str(connection_id)))


async def _connection(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    *,
    kind: str = "github",
    state: str = "working",
    with_secret: bool = True,
) -> uuid.UUID:
    connection_id = uuid.uuid4()
    org_id = tenant.organization.id
    disconnected = state == "disconnected"
    await db.execute(
        text(
            "INSERT INTO connections (id, org_id, kind, state, sealed_credential, "
            "sealed_webhook_secret, github_app_id, github_installation_id) "
            "VALUES (:id, :org, :kind, :state, :credential, :secret, :app, :installation)"
        ),
        {
            "id": connection_id,
            "org": org_id,
            "kind": kind,
            "state": state,
            "credential": None if disconnected else b"sealed",
            "secret": _sealed(org_id, connection_id) if with_secret and not disconnected else None,
            "app": uuid.uuid4().int % 10**9,
            "installation": uuid.uuid4().int % 10**9,
        },
    )
    return connection_id


async def _finding(db: AsyncSession, tenant: ProvisionedTenant) -> uuid.UUID:
    finding_id: uuid.UUID = (
        await db.execute(
            text(
                """
                INSERT INTO findings (org_id, project_id, scope, service_name, severity, title,
                    started_at, state, signals, case_state, case_opened_at, case_activity_at)
                VALUES (:org, :project, 'service', :service, 'critical', 'x', :started, 'open',
                    '[]'::jsonb, 'investigating', :started, :started)
                RETURNING id
                """
            ),
            {
                "org": tenant.organization.id,
                "project": tenant.project.id,
                # One open finding per service and project.
                "service": f"checkout-{uuid.uuid4().hex[:8]}",
                "started": NOW - timedelta(hours=1),
            },
        )
    ).scalar_one()
    return finding_id


async def _link(
    db: AsyncSession,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    finding_id: uuid.UUID,
    issue_id: str,
    *,
    status: str = "in_sync",
    next_sync_at: datetime | None = LATER,
    last_synced_at: datetime | None = NOW - timedelta(minutes=1),
    failures: int = 0,
) -> uuid.UUID:
    link_id: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO finding_links (org_id, finding_id, connection_id, role, sync_status, "
                "external_id, external_key, seen_state, next_sync_at, last_synced_at, failures) "
                "VALUES (:org, :finding, :connection, 'synced', :status, :issue, 'acme/api#12', "
                "'open', :next, :last, :failures) RETURNING id"
            ),
            {
                "org": org_id,
                "finding": finding_id,
                "connection": connection_id,
                "status": status,
                "issue": issue_id,
                "next": next_sync_at,
                "last": last_synced_at,
                "failures": failures,
            },
        )
    ).scalar_one()
    return link_id


@pytest.fixture
async def tracker(db: AsyncSession) -> Tracker:
    tenant = await _tenant(db, "Webhook")
    connection_id = await _connection(db, tenant)
    finding_id = await _finding(db, tenant)
    issue_id = str(uuid.uuid4().int % 10**12)
    link_id = await _link(db, tenant.organization.id, connection_id, finding_id, issue_id)
    return Tracker(tenant, connection_id, finding_id, link_id, issue_id)


def _issues_delivery(issue_id: str, **extra: Any) -> bytes:
    """GitHub's `issues` event, as much of it as matters, with what it says it did."""
    return json.dumps(
        {
            "action": "closed",
            "issue": {"id": int(issue_id), "number": 12, "state": "closed", **extra},
            "repository": {"id": 42, "full_name": "acme/api"},
        }
    ).encode()


def _signature(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _signed(body: bytes, secret: str = SECRET) -> dict[str, str]:
    return {GITHUB_HEADER: _signature(body, secret)}


async def _ring(
    db: AsyncSession,
    connection_id: uuid.UUID,
    body: bytes,
    headers: dict[str, str],
    *,
    keys: ConfiguredKeys = KEYS,
    now: datetime = NOW,
) -> None:
    await service.ring_from_webhook(
        db, connection_id=connection_id, body=body, headers=headers, keys=keys, now=now
    )


async def _post(db: AsyncSession, path: str, body: bytes, headers: dict[str, str]) -> Any:
    from nexovue_api.main import create_app

    settings = Settings(**TEST_ENVIRONMENT, **DEPLOYED_KEYS)  # type: ignore[arg-type]
    app = create_app(settings)

    async def session() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.post(f"{API_PREFIX}{path}", content=body, headers=headers)


async def _row(db: AsyncSession, sql: str, **values: Any) -> Any:
    return (await db.execute(text(sql), values)).one()


async def _link_row(db: AsyncSession, link_id: uuid.UUID) -> Any:
    return await _row(db, "SELECT * FROM finding_links WHERE id = :id", id=link_id)


async def _what_could_change(db: AsyncSession, tracker: Tracker) -> tuple[Any, ...]:
    """Everything a webhook must leave alone: the case, the link but its next read, the
    jobs and the audit trail."""
    finding = await _row(
        db,
        "SELECT state, case_state, case_resolved_at, case_activity_at FROM findings WHERE id = :id",
        id=tracker.finding_id,
    )
    link = await _row(
        db,
        "SELECT sync_status, seen_state, wanted_state, tracker_closed_at, last_synced_at, "
        "failures FROM finding_links WHERE id = :id",
        id=tracker.link_id,
    )
    jobs = await _row(db, "SELECT count(*) FROM jobs WHERE org_id = :org", org=tracker.org_id)
    audit = await _row(db, "SELECT count(*) FROM audit_log WHERE org_id = :org", org=tracker.org_id)
    events = await _row(
        db, "SELECT count(*) FROM finding_events WHERE finding_id = :id", id=tracker.finding_id
    )
    return (tuple(finding), tuple(link), jobs[0], audit[0], events[0])


# ------------------------------------------------------- a signed delivery (AC47)


async def test_a_signed_delivery_makes_its_issue_due_now_and_changes_nothing_else(
    db: AsyncSession, tracker: Tracker
) -> None:
    before = await _what_could_change(db, tracker)
    body = _issues_delivery(tracker.issue_id)

    response = await _post(db, f"/connections/webhook/{tracker.connection_id}", body, _signed(body))

    assert response.status_code == 204
    assert (await _link_row(db, tracker.link_id)).next_sync_at <= datetime.now(UTC)
    # It said "closed": the case, the link's seen state, jobs and audit are untouched.
    assert await _what_could_change(db, tracker) == before


async def test_a_body_claiming_a_state_never_sets_one(db: AsyncSession, tracker: Tracker) -> None:
    """AC47's second half: whatever a signed body says happened, only the next read moves."""
    before = await _what_could_change(db, tracker)
    for action in ("closed", "reopened", "deleted", "transferred"):
        body = json.dumps(
            {
                "action": action,
                "issue": {"id": int(tracker.issue_id), "state": "closed", "state_reason": "x"},
                "case_state": "resolved",
                "sync_status": "gone",
            }
        ).encode()
        await _ring(db, tracker.connection_id, body, _signed(body))
    assert await _what_could_change(db, tracker) == before
    assert (await _link_row(db, tracker.link_id)).next_sync_at == NOW


# ------------------------------------------------------------- refusals (AC47)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {GITHUB_HEADER: "sha256=" + "0" * 64},
        {GITHUB_HEADER: "not a signature"},
        # GitHub's legacy SHA-1 header is not read, even correctly made.
        {"X-Hub-Signature": "sha1=0000000000000000000000000000000000000000"},
    ],
)
async def test_a_bad_or_missing_signature_is_refused_and_changes_nothing(
    db: AsyncSession, tracker: Tracker, headers: dict[str, str]
) -> None:
    before = await _what_could_change(db, tracker)
    with pytest.raises(UnauthenticatedError) as refused:
        await _ring(db, tracker.connection_id, _issues_delivery(tracker.issue_id), headers)
    assert refused.value.detail == service.WEBHOOK_REFUSED
    assert (await _link_row(db, tracker.link_id)).next_sync_at == LATER
    assert await _what_could_change(db, tracker) == before


async def test_a_body_signed_with_another_secret_is_refused(
    db: AsyncSession, tracker: Tracker
) -> None:
    body = _issues_delivery(tracker.issue_id)
    with pytest.raises(UnauthenticatedError):
        await _ring(db, tracker.connection_id, body, _signed(body, "another-secret"))
    assert (await _link_row(db, tracker.link_id)).next_sync_at == LATER


async def test_a_signature_over_other_bytes_is_refused(db: AsyncSession, tracker: Tracker) -> None:
    signed = _issues_delivery(tracker.issue_id)
    sent = signed + b" "
    with pytest.raises(UnauthenticatedError):
        await _ring(db, tracker.connection_id, sent, _signed(signed))


async def test_the_route_answers_401_to_an_unsigned_delivery(
    db: AsyncSession, tracker: Tracker
) -> None:
    response = await _post(
        db,
        f"/connections/webhook/{tracker.connection_id}",
        _issues_delivery(tracker.issue_id),
        {},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == service.WEBHOOK_REFUSED
    assert (await _link_row(db, tracker.link_id)).next_sync_at == LATER


@pytest.mark.parametrize("connection", ["unknown", "disconnected", "no_form"])
async def test_a_connection_that_cannot_prove_a_delivery_is_refused_alike(
    db: AsyncSession, tracker: Tracker, connection: str
) -> None:
    """Not found, disconnected, or of a kind with no webhook form (F5's `github_fix`, and
    Jira until Step 7): the same words as a bad signature, so a caller learns nothing."""
    if connection == "unknown":
        connection_id = uuid.uuid4()
    elif connection == "disconnected":
        connection_id = await _connection(db, tracker.tenant, state="disconnected")
    else:
        connection_id = await _connection(db, tracker.tenant, kind="github_fix")
    body = _issues_delivery(tracker.issue_id)
    with pytest.raises(UnauthenticatedError) as refused:
        await _ring(db, connection_id, body, _signed(body))
    assert refused.value.detail == service.WEBHOOK_REFUSED


async def test_a_github_connection_with_no_secret_is_refused(
    db: AsyncSession, tracker: Tracker
) -> None:
    """An App created with its webhook inactive (an unreachable install) has none."""
    tenant = await _tenant(db, "Unreachable")
    connection_id = await _connection(db, tenant, with_secret=False)
    body = _issues_delivery(tracker.issue_id)
    with pytest.raises(UnauthenticatedError):
        await _ring(db, connection_id, body, _signed(body))


async def test_a_secret_sealed_for_another_connection_does_not_open(
    db: AsyncSession, tracker: Tracker
) -> None:
    """The seal names its connection: copied onto another row, it is no secret at all."""
    tenant = await _tenant(db, "Copier")
    connection_id = await _connection(db, tenant, with_secret=False)
    await db.execute(
        text("UPDATE connections SET sealed_webhook_secret = :secret WHERE id = :id"),
        {"secret": _sealed(tracker.org_id, tracker.connection_id), "id": connection_id},
    )
    body = _issues_delivery(tracker.issue_id)
    with pytest.raises(UnauthenticatedError):
        await _ring(db, connection_id, body, _signed(body))


async def test_without_the_connection_key_nothing_is_checked(
    db: AsyncSession, tracker: Tracker
) -> None:
    body = _issues_delivery(tracker.issue_id)
    with pytest.raises(DependencyUnavailableError):
        await _ring(db, tracker.connection_id, body, _signed(body), keys=ConfiguredKeys(""))
    assert (await _link_row(db, tracker.link_id)).next_sync_at == LATER


async def test_a_body_over_1_mib_is_refused_before_its_signature_is_read(
    db: AsyncSession, tracker: Tracker
) -> None:
    body = b"x" * (router.MAX_WEBHOOK_BYTES + 1)
    response = await _post(db, f"/connections/webhook/{tracker.connection_id}", body, {})
    assert response.status_code == 422
    assert response.json()["detail"] == router.WEBHOOK_TOO_LARGE


async def test_a_body_of_exactly_1_mib_is_read(db: AsyncSession, tracker: Tracker) -> None:
    padding = router.MAX_WEBHOOK_BYTES - len(_issues_delivery(tracker.issue_id, pad=""))
    body = _issues_delivery(tracker.issue_id, pad="p" * padding)
    assert len(body) == router.MAX_WEBHOOK_BYTES
    response = await _post(db, f"/connections/webhook/{tracker.connection_id}", body, _signed(body))
    assert response.status_code == 204


# --------------------------------------------------- shapes that name no issue


@pytest.mark.parametrize(
    "body",
    [
        b'{"zen": "Keep it logically awesome.", "hook_id": 1}',
        b'{"action": "created", "installation": {"id": 7}}',
        b'{"issue": {"id": "12"}}',
        b'{"issue": {"id": true}}',
        b'{"issue": {"id": -12}}',
        b'{"issue": {"id": 123456789012345678901}}',
        b"[1, 2]",
        b"not json",
        b"\xff\xfe",
    ],
)
async def test_a_signed_body_naming_no_issue_is_answered_and_does_nothing(
    db: AsyncSession, tracker: Tracker, body: bytes
) -> None:
    before = await _what_could_change(db, tracker)
    await _ring(db, tracker.connection_id, body, _signed(body))
    assert (await _link_row(db, tracker.link_id)).next_sync_at == LATER
    assert await _what_could_change(db, tracker) == before


# ----------------------------------------------------------- replays (AC48)


async def test_a_delivery_replayed_100_times_moves_one_read_and_adds_no_job(
    db: AsyncSession, tracker: Tracker
) -> None:
    before = await _what_could_change(db, tracker)
    body = _issues_delivery(tracker.issue_id)
    for replay in range(100):
        await _ring(
            db, tracker.connection_id, body, _signed(body), now=NOW + timedelta(seconds=replay)
        )
    # The first moved it to NOW; every later one found it already due and left it there.
    assert (await _link_row(db, tracker.link_id)).next_sync_at == NOW
    assert await _what_could_change(db, tracker) == before


async def test_a_link_read_within_30_seconds_is_read_30_seconds_after_that_read(
    db: AsyncSession, tracker: Tracker
) -> None:
    """With the sweep's 30 seconds, at most two reads of the issue a minute (AC48), and a
    change just after a read still arrives within the minute (AC46)."""
    await db.execute(
        text("UPDATE finding_links SET last_synced_at = :at WHERE id = :id"),
        {"at": NOW - timedelta(seconds=10), "id": tracker.link_id},
    )
    body = _issues_delivery(tracker.issue_id)
    for replay in range(100):
        await _ring(
            db, tracker.connection_id, body, _signed(body), now=NOW + timedelta(seconds=replay % 5)
        )
    assert (await _link_row(db, tracker.link_id)).next_sync_at == NOW + timedelta(seconds=20)


# ---------------------------------------- links a webhook never revives (AC85)


@pytest.mark.parametrize(
    ("status", "next_sync_at", "failures"),
    [
        ("not_followed", None, 0),
        ("stopped", None, 0),
        # Waiting for a person after a refused write: no read is scheduled.
        ("needs_attention", None, 0),
        # Gone for a week and no longer read.
        ("gone", None, 0),
        # In backoff after a failure: the webhook does not cut it short (FR39).
        ("waiting", LATER, 2),
    ],
)
async def test_a_link_not_being_followed_is_never_revived(
    db: AsyncSession,
    tracker: Tracker,
    status: str,
    next_sync_at: datetime | None,
    failures: int,
) -> None:
    finding_id = await _finding(db, tracker.tenant)
    issue_id = str(uuid.uuid4().int % 10**12)
    link_id = await _link(
        db,
        tracker.org_id,
        tracker.connection_id,
        finding_id,
        issue_id,
        status=status,
        next_sync_at=next_sync_at,
        failures=failures,
    )
    body = _issues_delivery(issue_id)
    await _ring(db, tracker.connection_id, body, _signed(body))
    link = await _link_row(db, link_id)
    assert (link.sync_status, link.next_sync_at) == (status, next_sync_at)


async def test_a_link_already_due_keeps_its_place(db: AsyncSession, tracker: Tracker) -> None:
    due = NOW - timedelta(minutes=3)
    await db.execute(
        text("UPDATE finding_links SET next_sync_at = :at WHERE id = :id"),
        {"at": due, "id": tracker.link_id},
    )
    body = _issues_delivery(tracker.issue_id)
    await _ring(db, tracker.connection_id, body, _signed(body))
    assert (await _link_row(db, tracker.link_id)).next_sync_at == due


async def test_every_followed_link_to_the_issue_rings_and_no_other(
    db: AsyncSession, tracker: Tracker
) -> None:
    """One issue may be a reference on other cases (FR41); each is read sooner. Another
    issue on the same connection is not."""
    finding_id = await _finding(db, tracker.tenant)
    reference = (
        await db.execute(
            text(
                "INSERT INTO finding_links (org_id, finding_id, connection_id, role, sync_status, "
                "external_id, external_key, seen_state, next_sync_at, last_synced_at) "
                "VALUES (:org, :finding, :connection, 'reference', 'in_sync', :issue, "
                "'acme/api#12', 'open', :next, :last) RETURNING id"
            ),
            {
                "org": tracker.org_id,
                "finding": finding_id,
                "connection": tracker.connection_id,
                "issue": tracker.issue_id,
                "next": LATER,
                "last": NOW - timedelta(minutes=1),
            },
        )
    ).scalar_one()
    other = await _link(
        db,
        tracker.org_id,
        tracker.connection_id,
        await _finding(db, tracker.tenant),
        str(int(tracker.issue_id) + 1),
    )
    body = _issues_delivery(tracker.issue_id)
    await _ring(db, tracker.connection_id, body, _signed(body))
    assert (await _link_row(db, tracker.link_id)).next_sync_at == NOW
    assert (await _link_row(db, reference)).next_sync_at == NOW
    assert (await _link_row(db, other)).next_sync_at == LATER


# ------------------------------------------------------------- tenancy (AC139)


async def test_the_organization_is_the_connection_s_never_another_s(
    db: AsyncSession, tracker: Tracker
) -> None:
    """Another organization links an issue with the same id on its own connection: a
    delivery proved by this connection's secret reaches this organization's link only."""
    elsewhere = await _tenant(db, "Elsewhere")
    their_connection = await _connection(db, elsewhere)
    their_link = await _link(
        db,
        elsewhere.organization.id,
        their_connection,
        await _finding(db, elsewhere),
        tracker.issue_id,
    )
    body = _issues_delivery(tracker.issue_id)
    await _ring(db, tracker.connection_id, body, _signed(body))
    assert (await _link_row(db, tracker.link_id)).next_sync_at == NOW
    assert (await _link_row(db, their_link)).next_sync_at == LATER
