"""Notification channels against real Postgres (F21: FR1, FR2, FR23, FR25, FR26,
AC2, AC26). Every test runs in a transaction that is rolled back.

The rule this suite exists for is BR6: a secret goes in and never comes out. So
beyond each behaviour, every response and every audit row the suite produces is
searched for the secrets it wrote.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import base64
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from nexovue_platform.checks.sealing import (
    NOTIFICATION_CHANNEL_PURPOSE,
    SealedBox,
    channel_secret_owner,
    decode_key,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.channels import service
from nexovue_api.channels.schemas import (
    CreateChannelRequest,
    SendTestResult,
    UpdateChannelRequest,
)
from nexovue_api.data.postgres.models import (
    AlertRuleChannel,
    AuditLogEntry,
    NotificationAttempt,
    NotificationChannel,
)
from nexovue_api.errors.exceptions import (
    ConflictError,
    DependencyUnavailableError,
    InvalidRequestError,
    NotFoundError,
)
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
KEY: Final = base64.urlsafe_b64encode(os.urandom(32)).decode()
SLACK_URL: Final = "https://hooks.slack.com/services/T000/B000/sl4ck-s3cret-t0ken"
ROUTING_KEY: Final = "R0ut1ngK3yR0ut1ngK3yR0ut1ngK3y12"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_channels_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
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
        organization_name="Channels",
        email=f"channels-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )


async def _create(
    db: AsyncSession, tenant: ProvisionedTenant, email_configured: bool = True, **request: Any
) -> Any:
    return await service.create(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        request=CreateChannelRequest(**request),
        secret_key=KEY,
        email_configured=email_configured,
    )


async def _audit_text(db: AsyncSession, tenant: ProvisionedTenant) -> str:
    entries = await db.scalars(
        select(AuditLogEntry).where(AuditLogEntry.org_id == tenant.organization.id)
    )
    return "\n".join(f"{entry.action} {entry.target} {entry.actor_label}" for entry in entries)


async def test_a_slack_channel_keeps_its_url_sealed_and_shows_only_the_host(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    created = await _create(db, tenant, kind="slack", name="#incidents", secret=SLACK_URL)

    assert created.destination == "hooks.slack.com"
    assert created.secret_set is True
    assert created.signing_secret is None
    assert "sl4ck-s3cret-t0ken" not in created.model_dump_json()

    stored = await db.scalar(
        select(NotificationChannel).where(NotificationChannel.id == created.id)
    )
    assert stored is not None and stored.sealed_secret is not None
    box = SealedBox(decode_key(KEY), purpose=NOTIFICATION_CHANNEL_PURPOSE)
    owner = channel_secret_owner(str(tenant.organization.id), str(created.id))
    assert box.open(stored.sealed_secret, owner=owner) == SLACK_URL


async def test_a_webhook_shows_its_signing_secret_once(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    created = await _create(
        db,
        tenant,
        kind="webhook",
        name="Incident bot",
        config={"url": "https://hooks.example.test/n"},
    )
    assert created.signing_secret is not None and created.signing_secret.startswith("whsec_")
    assert created.destination == "hooks.example.test"

    again = await service.get(db, org_id=tenant.organization.id, channel_id=created.id)
    assert "signing_secret" not in again.model_dump()
    assert created.signing_secret not in again.model_dump_json()

    rotated = await service.rotate_signing_secret(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        channel_id=created.id,
        secret_key=KEY,
    )
    assert rotated.signing_secret != created.signing_secret


@pytest.mark.parametrize(
    ("request_fields", "field"),
    [
        ({"kind": "slack", "secret": "http://hooks.slack.com/services/x"}, "secret"),
        ({"kind": "discord", "secret": "https://10.0.0.5/api/webhooks/1/x"}, "secret"),
        ({"kind": "teams", "secret": "https://api/webhooks/1"}, "secret"),
        ({"kind": "slack"}, "secret"),
        ({"kind": "pagerduty", "secret": "too-short"}, "secret"),
        ({"kind": "webhook", "config": {"url": "https://169.254.169.254/latest"}}, "config.url"),
        ({"kind": "webhook", "config": {"url": "http://hooks.example.test/n"}}, "config.url"),
        ({"kind": "email", "config": {"addresses": ["not-an-address"]}}, "config.addresses"),
        ({"kind": "email", "config": {"addresses": ["a@example.test"]}, "secret": "x"}, "secret"),
    ],
)
async def test_a_channel_that_cannot_work_is_refused_with_its_field_named(
    db: AsyncSession, tenant: ProvisionedTenant, request_fields: dict[str, Any], field: str
) -> None:
    with pytest.raises(InvalidRequestError) as refused:
        await _create(db, tenant, name="broken", **request_fields)
    assert [problem["field"] for problem in refused.value.extensions["errors"]] == [field]


async def test_email_channels_need_a_mail_provider(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    with pytest.raises(DependencyUnavailableError, match="EMAIL_PROVIDER"):
        await _create(
            db,
            tenant,
            email_configured=False,
            kind="email",
            name="On call",
            config={"addresses": ["oncall@example.test"]},
        )


async def test_the_owners_channel_can_be_muted_but_not_deleted_or_reconfigured(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    listed = await service.list_channels(db, org_id=tenant.organization.id)
    [owners] = [channel for channel in listed.channels if channel.kind == "owners"]
    assert [rule.name for rule in owners.rules] == ["Any check down"]

    with pytest.raises(ConflictError):
        await service.delete(
            db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, channel_id=owners.id
        )
    with pytest.raises(InvalidRequestError):
        await service.update(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            channel_id=owners.id,
            request=UpdateChannelRequest(config={"addresses": ["x@example.test"]}),
            secret_key=KEY,
        )
    until = datetime.now(UTC) + timedelta(hours=1)
    muted = await service.mute(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        channel_id=owners.id,
        until=until,
    )
    assert (muted.muted_until, muted.muted_by) == (until, tenant.user.id)
    unmuted = await service.mute(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        channel_id=owners.id,
        until=None,
    )
    assert (unmuted.muted_until, unmuted.muted_by) == (None, None)


async def test_replacing_a_secret_moves_the_host_and_never_returns_the_old_one(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    created = await _create(
        db,
        tenant,
        kind="discord",
        name="#ops",
        secret="https://discord.com/api/webhooks/1/old-t0ken",
    )
    updated = await service.update(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        channel_id=created.id,
        request=UpdateChannelRequest(
            name="#ops-alerts", secret="https://discordapp.com/api/webhooks/2/new-t0ken"
        ),
        secret_key=KEY,
    )
    assert (updated.name, updated.destination) == ("#ops-alerts", "discordapp.com")
    assert "t0ken" not in updated.model_dump_json()


async def test_deleting_a_channel_takes_it_off_its_rules_and_keeps_the_rules(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR25: the rule is left notifying nobody — visibly — not deleted with it."""
    created = await _create(db, tenant, kind="pagerduty", name="PagerDuty", secret=ROUTING_KEY)
    listed = await service.list_channels(db, org_id=tenant.organization.id)
    [owners] = [channel for channel in listed.channels if channel.kind == "owners"]
    rule_id = owners.rules[0].id
    db.add(AlertRuleChannel(org_id=tenant.organization.id, rule_id=rule_id, channel_id=created.id))
    await db.flush()
    shown = await service.get(db, org_id=tenant.organization.id, channel_id=created.id)
    assert [rule.id for rule in shown.rules] == [rule_id]

    await service.delete(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, channel_id=created.id
    )

    remaining = await db.scalars(
        select(AlertRuleChannel.channel_id).where(AlertRuleChannel.rule_id == rule_id)
    )
    assert list(remaining) == [owners.id]


async def test_another_organizations_channel_does_not_exist(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    created = await _create(db, tenant, kind="slack", name="#incidents", secret=SLACK_URL)
    other = await provision_tenant(
        db,
        organization_name="Other",
        email=f"other-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )
    with pytest.raises(NotFoundError):
        await service.get(db, org_id=other.organization.id, channel_id=created.id)
    with pytest.raises(NotFoundError):
        await service.delete(
            db, org_id=other.organization.id, actor_user_id=other.user.id, channel_id=created.id
        )


async def test_every_change_is_audited_and_no_audit_row_holds_a_secret(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC26, AC2."""
    created = await _create(db, tenant, kind="slack", name="#incidents", secret=SLACK_URL)
    await service.update(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        channel_id=created.id,
        request=UpdateChannelRequest(secret=SLACK_URL + "2"),
        secret_key=KEY,
    )
    await service.mute(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        channel_id=created.id,
        until=datetime.now(UTC) + timedelta(hours=1),
    )
    await service.delete(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, channel_id=created.id
    )

    trail = await _audit_text(db, tenant)
    for action in ("created", "updated", "muted", "deleted"):
        assert f"notification_channel.{action} notification_channel:{created.id}" in trail
    assert "sl4ck" not in trail


async def test_the_delivery_log_says_what_each_attempt_was_about(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    created = await _create(db, tenant, kind="slack", name="#incidents", secret=SLACK_URL)
    db.add(
        NotificationAttempt(
            org_id=tenant.organization.id,
            channel_id=created.id,
            batch_id=uuid.uuid4(),
            outcome="failed",
            http_status=404,
            reason="the receiver answered 404: no_service",
        )
    )
    await db.flush()

    log = await service.delivery_log(db, org_id=tenant.organization.id, channel_id=created.id)
    [attempt] = log.attempts
    assert (attempt.outcome, attempt.http_status, attempt.reason) == (
        "failed",
        404,
        "the receiver answered 404: no_service",
    )


async def test_quiet_hours_are_validated_and_kept_for_channels_people_read(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR15."""
    created = await _create(db, tenant, kind="slack", name="#incidents", secret=SLACK_URL)
    hours = {"timezone": "Asia/Kolkata", "days": [0, 1, 2, 3, 4], "start": "22:00", "end": "07:00"}
    saved = await service.set_quiet_hours(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        channel_id=created.id,
        quiet_hours=hours,
    )
    assert saved.quiet_hours == {
        "timezone": "Asia/Kolkata",
        "days": [0, 1, 2, 3, 4],
        "start": "22:00:00",
        "end": "07:00:00",
    }
    with pytest.raises(InvalidRequestError):
        await service.set_quiet_hours(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            channel_id=created.id,
            quiet_hours=hours | {"timezone": "Mars/Olympus"},
        )
    pager = await _create(db, tenant, kind="pagerduty", name="PD", secret=ROUTING_KEY)
    with pytest.raises(InvalidRequestError, match="PagerDuty"):
        await service.set_quiet_hours(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            channel_id=pager.id,
            quiet_hours=hours,
        )


class _RecordingTester:
    """Stands in for the broker: records what was dispatched, answers what it holds."""

    def __init__(self) -> None:
        self.dispatched: list[dict[str, str]] = []
        self.outcomes: dict[tuple[str, str], SendTestResult] = {}

    async def dispatch(self, *, org_id: str, channel_id: str, request_id: str) -> None:
        self.dispatched.append({"org_id": org_id, "channel_id": channel_id})
        self.outcomes[(org_id, request_id)] = SendTestResult(pending=False, delivered=True)

    async def collect(self, *, org_id: str, request_id: str) -> SendTestResult | None:
        return self.outcomes.get((org_id, request_id))

    async def close(self) -> None:
        return None


async def test_a_send_test_is_dispatched_only_for_the_callers_own_channel(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR3: a test makes our infrastructure contact the channel's address, so another
    organization naming this channel must reach nothing."""
    created = await _create(db, tenant, kind="slack", name="#incidents", secret=SLACK_URL)
    other = await provision_tenant(
        db,
        organization_name="Other",
        email=f"other-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )
    tester = _RecordingTester()

    with pytest.raises(NotFoundError):
        await service.dispatch_test(db, tester, org_id=other.organization.id, channel_id=created.id)
    assert tester.dispatched == []

    accepted = await service.dispatch_test(
        db, tester, org_id=tenant.organization.id, channel_id=created.id
    )
    assert tester.dispatched == [
        {"org_id": str(tenant.organization.id), "channel_id": str(created.id)}
    ]
    mine = await service.test_result(
        tester, org_id=tenant.organization.id, request_id=accepted.request_id, wait_seconds=0
    )
    assert mine.delivered
    # The request id alone does not reach another organization's outcome.
    theirs = await service.test_result(
        tester, org_id=other.organization.id, request_id=accepted.request_id, wait_seconds=0
    )
    assert theirs.pending


async def test_a_send_test_says_so_when_no_worker_can_be_reached(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    created = await _create(db, tenant, kind="slack", name="#incidents", secret=SLACK_URL)
    with pytest.raises(DependencyUnavailableError):
        await service.dispatch_test(db, None, org_id=tenant.organization.id, channel_id=created.id)
