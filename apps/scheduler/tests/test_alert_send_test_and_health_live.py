"""*Send test* (FR3) and a failing channel's report (FR21, AC18), against real
Postgres and a real receiver.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from alert_fixtures import AlertTenant, add_channel, future, postgres_reachable, rows
from live import require_stack
from nexovue_platform.checks.sealing import (
    NOTIFICATION_CHANNEL_PURPOSE,
    Keyring,
    SealedBox,
    channel_secret_owner,
)
from nexovue_platform.email import Delivery, Message
from recording_site import RecordingSite, Reply
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import health
from nexovue_scheduler.alerts.dispatch import Outbound
from nexovue_scheduler.alerts.send_test import NOT_FOUND, send_test

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_alert_send_test_and_health_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

BOX = SealedBox(Keyring(os.urandom(32)), purpose=NOTIFICATION_CHANNEL_PURPOSE)
BASE = "https://app.nexovue.test"


class _Mailbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Message, Mapping[str, Any]]] = []
        self.keys: list[str | None] = []

    async def send(
        self,
        *,
        to: str,
        message: Message,
        data: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> Delivery:
        self.keys.append(idempotency_key)
        self.sent.append((to, message, data))
        return Delivery(provider_message_id="recorded")


async def _slack_channel(engine: AsyncEngine, tenant: AlertTenant, url: str) -> uuid.UUID:
    async with engine.begin() as connection:
        channel_id = await add_channel(connection, tenant.org_id, kind="slack", name="#incidents")
        await connection.execute(
            text("UPDATE notification_channels SET sealed_secret = :sealed WHERE id = :id"),
            {
                "sealed": BOX.seal(
                    url, owner=channel_secret_owner(str(tenant.org_id), str(channel_id))
                ),
                "id": channel_id,
            },
        )
    return channel_id


def _outbound(mailbox: _Mailbox | None = None) -> Outbound:
    return Outbound(email_sender=mailbox, secret_box=BOX, app_base_url=BASE, private_allowed=True)


async def test_a_test_reaches_the_receiver_labelled_as_a_test(
    engine: AsyncEngine, tenant: AlertTenant, recording_site: RecordingSite
) -> None:
    channel_id = await _slack_channel(engine, tenant, recording_site.url("/slack/x"))

    outcome = await send_test(
        engine,
        _outbound(),
        org_id=tenant.org_id,
        channel_id=channel_id,
        request_id="r1",
        now=datetime.now(UTC),
    )

    assert (outcome.delivered, outcome.http_status) == (True, 200)
    [request] = recording_site.requests
    assert "[test] A test message from nexovue" in request.body.decode()


async def test_a_test_is_written_to_the_delivery_log_as_a_test(
    engine: AsyncEngine, tenant: AlertTenant, recording_site: RecordingSite
) -> None:
    """D2 (`docs/changes/console-review-fixes/`): the channel counted a test in
    "last delivered" while the log that promises every attempt showed none, so a
    customer who pressed Send test found nothing where they went to look."""
    channel_id = await _slack_channel(engine, tenant, recording_site.url("/slack/x"))

    await send_test(
        engine,
        _outbound(),
        org_id=tenant.org_id,
        channel_id=channel_id,
        request_id="r1",
        now=datetime.now(UTC),
    )

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT outcome, http_status, kind FROM notification_attempts "
                    "WHERE org_id = :org AND channel_id = :channel"
                ),
                {"org": tenant.org_id, "channel": channel_id},
            )
        ).all()
    assert [tuple(row) for row in rows] == [("delivered", 200, "test")]


async def test_a_failed_test_is_in_the_log_too(
    engine: AsyncEngine, tenant: AlertTenant, recording_site: RecordingSite
) -> None:
    """The more useful half: a test that did not arrive is exactly what somebody
    opens the log to find."""
    recording_site.replies["/slack/x"] = Reply(status=404, body=b"no_service")
    channel_id = await _slack_channel(engine, tenant, recording_site.url("/slack/x"))

    await send_test(
        engine,
        _outbound(),
        org_id=tenant.org_id,
        channel_id=channel_id,
        request_id="r1",
        now=datetime.now(UTC),
    )

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT outcome, kind, reason FROM notification_attempts "
                    "WHERE org_id = :org AND channel_id = :channel"
                ),
                {"org": tenant.org_id, "channel": channel_id},
            )
        ).one()
    assert (row.outcome, row.kind) == ("failed", "test")
    assert row.reason == "the receiver answered 404: no_service"


async def test_a_failed_test_says_why(
    engine: AsyncEngine, tenant: AlertTenant, recording_site: RecordingSite
) -> None:
    recording_site.replies["/slack/x"] = Reply(status=404, body=b"no_service")
    channel_id = await _slack_channel(engine, tenant, recording_site.url("/slack/x"))

    outcome = await send_test(
        engine,
        _outbound(),
        org_id=tenant.org_id,
        channel_id=channel_id,
        request_id="r1",
        now=datetime.now(UTC),
    )
    assert (outcome.delivered, outcome.reason) == (False, "the receiver answered 404: no_service")


async def test_another_organizations_channel_is_not_found(
    engine: AsyncEngine, tenant: AlertTenant, recording_site: RecordingSite
) -> None:
    channel_id = await _slack_channel(engine, tenant, recording_site.url("/slack/x"))
    outcome = await send_test(
        engine,
        _outbound(),
        org_id=uuid.uuid4(),
        channel_id=channel_id,
        request_id="r1",
        now=datetime.now(UTC),
    )
    assert (outcome.delivered, outcome.reason) == (False, NOT_FOUND)
    assert recording_site.requests == []


async def _fail_since(
    engine: AsyncEngine, channel_id: uuid.UUID, org_id: uuid.UUID, since: datetime
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE notification_channels SET failing_since = :since WHERE id = :id"),
            {"since": since, "id": channel_id},
        )
        await connection.execute(
            text(
                "INSERT INTO notification_attempts (org_id, channel_id, batch_id, attempted_at, "
                "outcome, http_status, reason) VALUES (:org, :id, :batch, :at, 'retrying', 503, "
                "'the receiver answered 503')"
            ),
            {"org": org_id, "id": channel_id, "batch": uuid.uuid4(), "at": since},
        )


async def test_an_hour_of_failure_marks_the_channel_and_tells_the_owners_once(
    engine: AsyncEngine, tenant: AlertTenant, recording_site: RecordingSite
) -> None:
    """AC18, first half: through email, never through the channel that is broken."""
    channel_id = await _slack_channel(engine, tenant, recording_site.url("/slack/x"))
    now = future()
    await _fail_since(engine, channel_id, tenant.org_id, now - timedelta(minutes=61))
    mailbox = _Mailbox()

    first = await health.report_failing_channels(
        engine, email_sender=mailbox, app_base_url=BASE, now=now, org_id=tenant.org_id
    )
    second = await health.report_failing_channels(
        engine, email_sender=mailbox, app_base_url=BASE, now=now, org_id=tenant.org_id
    )

    assert (first, second) == (1, 0)
    [(_, message, data)] = [sent for sent in mailbox.sent if sent[0] == tenant.owner_email]
    assert message is Message.CHANNEL_FAILING
    assert data["channel_name"] == "#incidents"
    assert data["channel_kind"] == "Slack"
    assert data["failing_for"] == "1 h 1 min"
    assert data["reason"] == "the receiver answered 503"
    assert recording_site.requests == []
    [channel] = await rows(
        engine, "SELECT health FROM notification_channels WHERE id = :id", id=channel_id
    )
    assert channel.health == "failing"


async def test_a_report_with_nowhere_to_go_is_given_up_once_not_every_sweep(
    engine: AsyncEngine, tenant: AlertTenant, recording_site: RecordingSite
) -> None:
    """No mail provider: the console still shows the channel failing, and the sweep
    must not look at it again every 15 seconds."""
    channel_id = await _slack_channel(engine, tenant, recording_site.url("/slack/x"))
    now = future()
    await _fail_since(engine, channel_id, tenant.org_id, now - timedelta(minutes=61))

    await health.report_failing_channels(
        engine, email_sender=None, app_base_url=BASE, now=now, org_id=tenant.org_id
    )
    [channel] = await rows(
        engine,
        "SELECT health, failing_reported_at FROM notification_channels WHERE id = :id",
        id=channel_id,
    )
    assert (channel.health, channel.failing_reported_at) == ("failing", now)


async def test_a_report_retried_carries_the_same_key_per_owner(
    engine: AsyncEngine, tenant: AlertTenant, recording_site: RecordingSite
) -> None:
    """A retry after one owner's address was refused must not mail the others again."""
    channel_id = await _slack_channel(engine, tenant, recording_site.url("/slack/x"))
    now = future()
    await _fail_since(engine, channel_id, tenant.org_id, now - timedelta(minutes=61))
    first, again = _Mailbox(), _Mailbox()
    await health.report_failing_channels(
        engine, email_sender=first, app_base_url=BASE, now=now, org_id=tenant.org_id
    )
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE notification_channels SET failing_reported_at = NULL WHERE id = :id"),
            {"id": channel_id},
        )
    await health.report_failing_channels(
        engine, email_sender=again, app_base_url=BASE, now=now, org_id=tenant.org_id
    )
    assert first.keys == again.keys
    assert all(key is not None for key in first.keys)


async def test_under_an_hour_is_not_yet_failing(
    engine: AsyncEngine, tenant: AlertTenant, recording_site: RecordingSite
) -> None:
    channel_id = await _slack_channel(engine, tenant, recording_site.url("/slack/x"))
    now = future()
    await _fail_since(engine, channel_id, tenant.org_id, now - timedelta(minutes=59))

    await health.report_failing_channels(
        engine, email_sender=_Mailbox(), app_base_url=BASE, now=now, org_id=tenant.org_id
    )
    [channel] = await rows(
        engine, "SELECT health FROM notification_channels WHERE id = :id", id=channel_id
    )
    assert channel.health == "healthy"


async def test_the_next_success_makes_a_failing_channel_healthy(
    engine: AsyncEngine, tenant: AlertTenant, recording_site: RecordingSite
) -> None:
    """AC18, second half."""
    channel_id = await _slack_channel(engine, tenant, recording_site.url("/slack/x"))
    now = future()
    await _fail_since(engine, channel_id, tenant.org_id, now - timedelta(hours=2))
    await health.report_failing_channels(
        engine, email_sender=_Mailbox(), app_base_url=BASE, now=now, org_id=tenant.org_id
    )

    await send_test(
        engine, _outbound(), org_id=tenant.org_id, channel_id=channel_id, request_id="r1", now=now
    )

    [channel] = await rows(
        engine,
        "SELECT health, failing_since, failing_reported_at FROM notification_channels "
        "WHERE id = :id",
        id=channel_id,
    )
    assert (channel.health, channel.failing_since, channel.failing_reported_at) == (
        "healthy",
        None,
        None,
    )
