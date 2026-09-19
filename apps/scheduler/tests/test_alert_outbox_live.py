"""The outbox, against real Postgres: claims, leases, retries and the delivery log
(ADR-013, FR19, FR20, AC17, NFR2).

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from alert_fixtures import AlertTenant, future, postgres_reachable, rows
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import outbox
from nexovue_scheduler.alerts.incidents import GROUPING_WAIT_SECONDS
from nexovue_scheduler.alerts.outbox import SendResult
from nexovue_scheduler.probes import state_store

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_alert_outbox_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


# The owners channel is read by people, so a check's opening waits this long to be
# grouped (FR13). The suite decides that long before it claims.
DECIDED_EARLIER = timedelta(seconds=GROUPING_WAIT_SECONDS + 1)


async def _down(engine: AsyncEngine, tenant: AlertTenant, at: datetime) -> None:
    """A check confirmed down long enough ago that its notification is due at `at`."""
    await state_store.record(
        engine,
        state_store.RecordedOutcome(
            check_id=str(tenant.check_id),
            org_id=str(tenant.org_id),
            project_id=str(tenant.project_id),
            outcome="down",
            run_id="run-1",
            location="local",
            observed_at=at - DECIDED_EARLIER,
            reason=None,
        ),
    )


async def _claim(engine: AsyncEngine, tenant: AlertTenant, now: datetime) -> outbox.Claim:
    """This tenant's due work only: the database is shared with the running stack."""
    async with engine.begin() as connection:
        return await outbox.claim(connection, now=now, limit=100, org_id=tenant.org_id)


async def _record(
    engine: AsyncEngine, batch: outbox.Batch, result: SendResult, now: datetime
) -> str:
    async with engine.begin() as connection:
        return await outbox.record(connection, batch, result, now=now)


def _mine(claimed: outbox.Claim, tenant: AlertTenant) -> list[outbox.Batch]:
    """Other suites' tenants may share the database; each test judges only its own."""
    return [batch for batch in claimed.batches if batch.channel.org_id == tenant.org_id]


async def test_a_due_notification_is_claimed_once_with_its_incident(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = future()
    await _down(engine, tenant, now)

    [batch] = _mine(await _claim(engine, tenant, now), tenant)
    assert batch.channel.kind == "owners"
    [notice] = batch.notices
    assert (notice.kind, notice.subject_label, notice.severity) == (
        "opened",
        "Checkout API",
        "critical",
    )
    # Leased: a second claim inside the lease finds nothing of this tenant's.
    assert _mine(await _claim(engine, tenant, now), tenant) == []


async def test_two_claimers_at_once_never_take_the_same_batch(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = future()
    await _down(engine, tenant, now)

    first, second = await asyncio.gather(_claim(engine, tenant, now), _claim(engine, tenant, now))
    assert len(_mine(first, tenant)) + len(_mine(second, tenant)) == 1


async def test_a_lease_that_ended_is_claimed_again_under_the_same_batch_id(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """A worker that died holding a batch loses it when the lease ends (NFR2), and
    the retry is recognisably the same delivery (FR4)."""
    now = future()
    await _down(engine, tenant, now)
    [taken] = _mine(await _claim(engine, tenant, now), tenant)

    [again] = _mine(await _claim(engine, tenant, now + outbox.LEASE + timedelta(seconds=1)), tenant)
    assert again.id == taken.id
    assert again.ids == taken.ids


async def test_a_worker_whose_lease_ended_cannot_undo_the_delivery_that_replaced_it(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """A slow send outlives its lease; another worker claims and delivers. The late
    answer is logged, but must not put the delivered rows back to be sent again."""
    now = future()
    await _down(engine, tenant, now)
    [late] = _mine(await _claim(engine, tenant, now), tenant)
    later = now + outbox.LEASE + timedelta(seconds=1)
    [replacing] = _mine(await _claim(engine, tenant, later), tenant)
    assert await _record(engine, replacing, SendResult(outcome="delivered"), later) == "delivered"

    timed_out = SendResult(outcome="retry", reason="the receiver did not answer in time")
    await _record(engine, late, timed_out, later)

    [notification] = await rows(
        engine,
        "SELECT state, attempts FROM notifications WHERE org_id = :org_id",
        org_id=tenant.org_id,
    )
    assert (notification.state, notification.attempts) == ("delivered", 1)
    assert _mine(await _claim(engine, tenant, later + timedelta(hours=1)), tenant) == []


async def test_503_twice_then_200_is_one_delivery_and_three_attempts(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC17."""
    now = future()
    await _down(engine, tenant, now)
    outcomes = []
    for status in (503, 503, 200):
        [batch] = _mine(await _claim(engine, tenant, now), tenant)
        result = (
            SendResult(outcome="delivered", http_status=200)
            if status == 200
            else SendResult(
                outcome="retry", http_status=status, reason=f"the receiver answered {status}"
            )
        )
        outcomes.append(await _record(engine, batch, result, now))
        # Past any backoff, so the next claim finds it due.
        now += timedelta(seconds=outbox.LONGEST_RETRY_SECONDS * 1.3)

    assert outcomes == ["retrying", "retrying", "delivered"]
    attempts = await rows(
        engine,
        "SELECT outcome, http_status FROM notification_attempts WHERE org_id = :org_id "
        "ORDER BY attempted_at",
        org_id=tenant.org_id,
    )
    assert [(row.outcome, row.http_status) for row in attempts] == [
        ("retrying", 503),
        ("retrying", 503),
        ("delivered", 200),
    ]
    [notification] = await rows(
        engine,
        "SELECT state, attempts FROM notifications WHERE org_id = :org_id",
        org_id=tenant.org_id,
    )
    assert (notification.state, notification.attempts) == ("delivered", 3)


async def test_a_retry_waits_and_honours_retry_after(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = future()
    await _down(engine, tenant, now)
    [batch] = _mine(await _claim(engine, tenant, now), tenant)
    await _record(
        engine, batch, SendResult(outcome="retry", http_status=429, retry_after_seconds=900), now
    )

    assert _mine(await _claim(engine, tenant, now + timedelta(seconds=899)), tenant) == []
    assert len(_mine(await _claim(engine, tenant, now + timedelta(seconds=901)), tenant)) == 1


async def test_a_refusal_is_final_and_its_reason_recorded(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = future()
    await _down(engine, tenant, now)
    [batch] = _mine(await _claim(engine, tenant, now), tenant)
    await _record(
        engine,
        batch,
        SendResult(outcome="failed", http_status=404, reason="the receiver answered 404"),
        now,
    )

    [notification] = await rows(
        engine,
        "SELECT state, outcome_reason FROM notifications WHERE org_id = :org_id",
        org_id=tenant.org_id,
    )
    assert (notification.state, notification.outcome_reason) == (
        "failed",
        "the receiver answered 404",
    )
    [channel] = await rows(
        engine,
        "SELECT failing_since FROM notification_channels WHERE id = :id",
        id=tenant.owners_channel_id,
    )
    assert channel.failing_since == now


async def test_retrying_stops_an_hour_after_the_first_attempt(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = future()
    await _down(engine, tenant, now)
    unavailable = SendResult(outcome="retry", http_status=503, reason="the receiver answered 503")
    [batch] = _mine(await _claim(engine, tenant, now), tenant)
    assert await _record(engine, batch, unavailable, now) == "retrying"

    later = now + outbox.RETRY_WINDOW + timedelta(seconds=1)
    [again] = _mine(await _claim(engine, tenant, later), tenant)
    assert await _record(engine, again, unavailable, later) == "failed"


async def test_a_notification_held_for_hours_still_gets_its_hour_of_retries(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """A warning held by quiet hours overnight is first tried in the morning; its
    first failure is a retry, not a give-up (FR19)."""
    now = future()
    await _down(engine, tenant, now)
    morning = now + timedelta(hours=8)
    [batch] = _mine(await _claim(engine, tenant, morning), tenant)

    unavailable = SendResult(outcome="retry", http_status=503, reason="the receiver answered 503")
    assert await _record(engine, batch, unavailable, morning) == "retrying"


async def test_a_resolve_waits_for_its_opening_s_retry_and_goes_after_it(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """A receiver must never hear *resolved* before *opened*: PagerDuty drops a
    resolve for an alert it does not have, then keeps the late trigger open."""
    now = future()
    await _down(engine, tenant, now)
    [opening] = _mine(await _claim(engine, tenant, now), tenant)
    unavailable = SendResult(outcome="retry", http_status=503, reason="the receiver answered 503")
    await _record(engine, opening, unavailable, now)
    await state_store.record(
        engine,
        state_store.RecordedOutcome(
            check_id=str(tenant.check_id),
            org_id=str(tenant.org_id),
            project_id=str(tenant.project_id),
            outcome="up",
            run_id="run-2",
            location="local",
            observed_at=now + timedelta(seconds=5),
            reason=None,
        ),
    )

    assert _mine(await _claim(engine, tenant, now + timedelta(seconds=10)), tenant) == []

    retry_due = now + timedelta(seconds=outbox.LONGEST_RETRY_SECONDS * 1.3)
    [first] = _mine(await _claim(engine, tenant, retry_due), tenant)
    assert [notice.kind for notice in first.notices] == ["opened"]
    await _record(engine, first, SendResult(outcome="delivered"), retry_due)
    [second] = _mine(await _claim(engine, tenant, retry_due), tenant)
    assert [notice.kind for notice in second.notices] == ["resolved"]


async def test_a_muted_channel_drops_what_is_due_and_says_why(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = future()
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE notification_channels SET muted_until = :until WHERE id = :id"),
            {"until": now + timedelta(hours=1), "id": tenant.owners_channel_id},
        )
    await _down(engine, tenant, now)

    assert _mine(await _claim(engine, tenant, now), tenant) == []
    [notification] = await rows(
        engine,
        "SELECT state, outcome_reason FROM notifications WHERE org_id = :org_id",
        org_id=tenant.org_id,
    )
    assert (notification.state, notification.outcome_reason) == (
        "dropped",
        outbox.MUTED_CHANNEL_REASON,
    )


async def test_two_checks_failing_together_reach_people_as_one_message(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR13, AC13: two incidents, two notifications, one message — and while it is
    out, nothing else is taken for that channel (NFR5)."""
    now = future()
    async with engine.begin() as connection:
        second_check = await connection.execute(
            text(
                """
                INSERT INTO checks (org_id, project_id, name, type, config, interval_seconds)
                VALUES (:org_id, :project_id, 'Search API', 'tcp',
                        CAST('{"type": "tcp", "host": "example.test", "port": 443}' AS jsonb), 60)
                RETURNING id
                """
            ),
            {"org_id": tenant.org_id, "project_id": tenant.project_id},
        )
        second_id = second_check.scalar_one()
    await _down(engine, tenant, now)
    await state_store.record(
        engine,
        state_store.RecordedOutcome(
            check_id=str(second_id),
            org_id=str(tenant.org_id),
            project_id=str(tenant.project_id),
            outcome="down",
            run_id="run-2",
            location="local",
            observed_at=now - DECIDED_EARLIER,
            reason=None,
        ),
    )

    [together] = _mine(await _claim(engine, tenant, now), tenant)
    assert sorted(notice.subject_label for notice in together.notices) == [
        "Checkout API",
        "Search API",
    ]
    assert _mine(await _claim(engine, tenant, now), tenant) == []


async def test_attempts_older_than_thirty_days_are_pruned(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = future()
    await _down(engine, tenant, now)
    [batch] = _mine(await _claim(engine, tenant, now), tenant)
    await _record(engine, batch, SendResult(outcome="delivered"), now - timedelta(days=31))

    async with engine.begin() as connection:
        await outbox.prune_attempts(connection, before=now - timedelta(days=30))
    assert (
        await rows(
            engine,
            "SELECT 1 FROM notification_attempts WHERE org_id = :org_id",
            org_id=tenant.org_id,
        )
        == []
    )
