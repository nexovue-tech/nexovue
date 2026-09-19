"""Step 1 end to end, below Celery: a check goes down, and its owner gets an email;
it recovers, and the owner is told how long it lasted (AC5, AC21).

The mail port is a recorder, so what is asserted is exactly what the provider
would have been handed. This suite fails rather than skips when the stack is down;
see tests/helpers/live.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from alert_fixtures import (
    AlertTenant,
    add_channel,
    add_rule,
    drop_tenant,
    future,
    make_tenant,
    postgres_reachable,
    rows,
)
from live import require_stack
from nexovue_platform.email import Delivery, Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts.delivery import deliver_due
from nexovue_scheduler.alerts.dispatch import Outbound
from nexovue_scheduler.alerts.incidents import GROUPING_WAIT_SECONDS
from nexovue_scheduler.alerts.senders import email
from nexovue_scheduler.probes import state_store

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_alert_delivery_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

BASE_URL = "https://app.nexovue.test"
GROUPED = timedelta(seconds=GROUPING_WAIT_SECONDS + 1)


class _Mailbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Message, Mapping[str, Any]]] = []

    async def send(
        self,
        *,
        to: str,
        message: Message,
        data: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> Delivery:
        self.sent.append((to, message, data))
        return Delivery(provider_message_id="recorded")

    def to(self, address: str) -> list[Mapping[str, Any]]:
        return [data for recipient, _, data in self.sent if recipient == address]


def _outbound(mailbox: _Mailbox) -> Outbound:
    return Outbound(email_sender=mailbox, secret_box=None, app_base_url=BASE_URL)


async def _record(engine: AsyncEngine, tenant: AlertTenant, outcome: str, at: datetime) -> None:
    await state_store.record(
        engine,
        state_store.RecordedOutcome(
            check_id=str(tenant.check_id),
            org_id=str(tenant.org_id),
            project_id=str(tenant.project_id),
            outcome=outcome,
            run_id="run-1",
            location="local",
            observed_at=at,
            reason=None,
            alert_reason="expected status code less than 400, got 503",
        ),
    )


async def test_the_owner_is_emailed_when_a_check_goes_down_and_when_it_recovers(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    mailbox = _Mailbox()
    down_at = future()

    await _record(engine, tenant, "down", down_at)
    # After the grouping wait a check's opening takes on channels people read (FR13).
    await deliver_due(
        engine, _outbound(mailbox), now=lambda: down_at + GROUPED, org_id=tenant.org_id
    )
    await _record(engine, tenant, "up", down_at + timedelta(minutes=12))
    await deliver_due(
        engine,
        _outbound(mailbox),
        now=lambda: down_at + timedelta(minutes=12) + GROUPED,
        org_id=tenant.org_id,
    )

    opened, resolved = mailbox.to(tenant.owner_email)
    assert opened["subject"] == "[critical] Checkout API is down"
    assert "Why: expected status code less than 400, got 503." in opened["body"]
    # One incident, so one page: the opening and the recovery link to the same place.
    assert re.fullmatch(rf"{re.escape(BASE_URL)}/alerts/incidents/[0-9a-f-]{{36}}", opened["link"])
    assert resolved["link"] == opened["link"]
    assert opened["rule_name"] == "Any check down"
    assert opened["manage_link"] == f"{BASE_URL}/alerts/rules/{tenant.default_rule_id}"
    assert resolved["subject"] == "[resolved] Checkout API recovered"
    assert "Lasted: 12 min." in resolved["body"]

    delivered = await rows(
        engine,
        "SELECT kind, state, attempts FROM notifications WHERE org_id = :org_id "
        "ORDER BY created_at",
        org_id=tenant.org_id,
    )
    assert [(row.kind, row.state, row.attempts) for row in delivered] == [
        ("opened", "delivered", 1),
        ("resolved", "delivered", 1),
    ]
    [channel] = await rows(
        engine,
        "SELECT health, last_delivered_at FROM notification_channels WHERE id = :id",
        id=tenant.owners_channel_id,
    )
    assert channel.health == "healthy"
    assert channel.last_delivered_at == down_at + timedelta(minutes=12) + GROUPED


async def test_an_owner_without_a_verified_address_is_a_recorded_failure_not_a_silence(
    engine: AsyncEngine,
) -> None:
    unverified = await make_tenant(engine, verified=False)
    try:
        mailbox = _Mailbox()
        at = future()
        await _record(engine, unverified, "down", at)
        await deliver_due(
            engine, _outbound(mailbox), now=lambda: at + GROUPED, org_id=unverified.org_id
        )

        assert mailbox.to(unverified.owner_email) == []
        [attempt] = await rows(
            engine,
            "SELECT outcome, reason FROM notification_attempts WHERE org_id = :org_id",
            org_id=unverified.org_id,
        )
        assert (attempt.outcome, attempt.reason) == ("failed", email.NO_VERIFIED_OWNER)
    finally:
        await drop_tenant(engine, unverified)


async def test_a_muted_channel_claimed_first_does_not_end_the_run_for_the_others(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """One channel per claim: a claim that only drops a mute's rows found work, and
    the run must go on to the next channel rather than wait for the next sweep."""
    down_at = future()
    async with engine.begin() as connection:
        muted = await add_channel(
            connection,
            tenant.org_id,
            kind="email",
            name="Muted",
            config='{"addresses": ["m@example.test"]}',
        )
        # Sorted before the owners channel, so it is the one claimed first.
        first = uuid.UUID(f"00000000-0000-0000-0000-{muted.hex[-12:]}")
        await connection.execute(
            text(
                "UPDATE notification_channels SET id = :first, muted_until = :until WHERE id = :id"
            ),
            {"first": first, "until": down_at + timedelta(days=1), "id": muted},
        )
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Also to the muted channel",
            condition={"kind": "check_state", "states": {"down": "critical"}},
            channel_ids=[first],
        )

    await _record(engine, tenant, "down", down_at)
    mailbox = _Mailbox()
    report = await deliver_due(
        engine, _outbound(mailbox), now=lambda: down_at + GROUPED, org_id=tenant.org_id
    )

    assert report.dropped == 1
    assert len(mailbox.to(tenant.owner_email)) == 1
