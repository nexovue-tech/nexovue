"""The outbox, against real Postgres: claims, leases, retries and the delivery log
(ADR-013, FR19, FR20, AC17, NFR2).

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

from alert_fixtures import (
    AlertTenant,
    add_channel,
    add_finding,
    add_rule,
    anomaly_rule,
    future,
    postgres_reachable,
    rows,
)
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import incidents, outbox
from nexovue_scheduler.alerts.incidents import GROUPING_WAIT_SECONDS
from nexovue_scheduler.alerts.outbox import SendResult
from nexovue_scheduler.alerts.rules import Rule
from nexovue_scheduler.detection import notify
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
    assert notice.subject.kind == "incident"
    assert notice.pagerduty_key == f"incident:{notice.subject.id}"
    assert (notice.kind, notice.subject.label, notice.severity) == (
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
    assert sorted(notice.subject.label for notice in together.notices) == [
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


# ------------------------------------------- F6: findings and projects (ADR-022)


async def _finding_on(
    engine: AsyncEngine, tenant: AlertTenant, channel_kind: str, at: datetime
) -> tuple[uuid.UUID, uuid.UUID]:
    """A channel, a rule sending every finding to it, and a finding told to it."""
    async with engine.begin() as connection:
        channel = await add_channel(connection, tenant.org_id, kind=channel_kind, name="Ops")
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Findings",
            condition=anomaly_rule(),
            channel_ids=[channel],
        )
        finding = await add_finding(connection, tenant, started_at=at - timedelta(minutes=5))
        await notify.finding_opened(
            connection, finding, org_id=tenant.org_id, severity="warning", at=at
        )
    return channel, finding


async def test_a_finding_s_notification_is_claimed_with_the_finding_as_its_subject(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """ADR-022: the same claim, a second subject, a prefixed PagerDuty key."""
    now = future()
    _, finding = await _finding_on(engine, tenant, "pagerduty", now)

    [batch] = _mine(await _claim(engine, tenant, now), tenant)
    [notice] = batch.notices
    subject = notice.subject
    assert (subject.kind, subject.id, subject.label) == ("finding", finding, "checkout")
    assert (subject.rule_name, subject.condition_kind) == ("Findings", "anomaly_finding")
    assert subject.detail["title"].startswith("checkout is 4x slower")
    assert notice.pagerduty_key == f"finding:{finding}"
    assert not notice.noted


async def test_a_finding_s_close_waits_for_its_opening_s_retry(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """The in-order rule over the second subject: *resolved* never overtakes *opened*."""
    now = future()
    _, finding = await _finding_on(engine, tenant, "webhook", now)
    [opening] = _mine(await _claim(engine, tenant, now), tenant)
    unavailable = SendResult(outcome="retry", http_status=503, reason="the receiver answered 503")
    await _record(engine, opening, unavailable, now)
    async with engine.begin() as connection:
        await notify.finding_closed(
            connection,
            finding,
            org_id=tenant.org_id,
            severity="warning",
            at=now + timedelta(seconds=5),
        )

    assert _mine(await _claim(engine, tenant, now + timedelta(seconds=10)), tenant) == []
    retry_due = now + timedelta(seconds=outbox.LONGEST_RETRY_SECONDS * 1.3)
    [first] = _mine(await _claim(engine, tenant, retry_due), tenant)
    assert [notice.kind for notice in first.notices] == ["opened"]


async def test_detection_stopping_is_claimed_with_the_project_as_its_subject(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """D24: a notice naming neither an incident nor a finding."""
    now = future()
    async with engine.begin() as connection:
        await notify.running_again(
            connection, org_id=tenant.org_id, project_id=tenant.project_id, at=now
        )

    [batch] = _mine(await _claim(engine, tenant, now), tenant)
    [notice] = batch.notices
    assert (notice.kind, notice.subject.kind, notice.subject.label) == (
        "running_again",
        "project",
        "Production",
    )
    assert notice.subject.id == tenant.project_id


CHECKOUT = incidents.Subject(key="service:checkout", label="checkout")
ERROR_RATE = {"kind": "service_error_rate", "threshold_percent": 5}


async def _threshold_rule(
    engine: AsyncEngine,
    tenant: AlertTenant,
    channel: uuid.UUID,
    condition: dict[str, object] = ERROR_RATE,
) -> Rule:
    name = f"Checkout {condition['kind']}"
    async with engine.begin() as connection:
        rule_id = await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name=name,
            condition=condition,
            channel_ids=[channel],
        )
    return Rule(
        id=rule_id,
        org_id=tenant.org_id,
        project_id=tenant.project_id,
        name=name,
        condition_kind=str(condition["kind"]),
        condition=condition,
        muted_until=None,
        channel_ids=(channel,),
    )


async def _fire(engine: AsyncEngine, rule: Rule, at: datetime) -> uuid.UUID:
    """The rule's incident on checkout opens; linked to an open finding there, if any."""
    async with engine.begin() as connection:
        await incidents.fire(connection, rule, CHECKOUT, severity="critical", at=at, detail={})
        incident_id: uuid.UUID = (
            await connection.execute(
                text("SELECT id FROM incidents WHERE rule_id = :rule_id"), {"rule_id": rule.id}
            )
        ).scalar_one()
    return incident_id


async def _recover(engine: AsyncEngine, rule: Rule, at: datetime) -> None:
    async with engine.begin() as connection:
        await incidents.clear(connection, rule, CHECKOUT, at=at)


async def _incident_told(
    engine: AsyncEngine, tenant: AlertTenant, channel: uuid.UUID, at: datetime
) -> uuid.UUID:
    """A threshold incident on checkout whose opening was written for `channel`."""
    return await _fire(engine, await _threshold_rule(engine, tenant, channel), at)


async def _pager_with_findings(engine: AsyncEngine, tenant: AlertTenant) -> uuid.UUID:
    """A PagerDuty channel that every finding is sent to."""
    async with engine.begin() as connection:
        pagerduty = await add_channel(connection, tenant.org_id, kind="pagerduty", name="Pager")
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Findings",
            condition=anomaly_rule(),
            channel_ids=[pagerduty],
        )
    return pagerduty


async def _open_finding(
    engine: AsyncEngine, tenant: AlertTenant, at: datetime, *, linked_to: uuid.UUID | None = None
) -> uuid.UUID:
    """A finding on checkout opens at `at`; the project job links it to an open
    incident there."""
    async with engine.begin() as connection:
        finding = await add_finding(connection, tenant, started_at=at)
        if linked_to is not None:
            await connection.execute(
                text(
                    "INSERT INTO finding_incidents (org_id, finding_id, incident_id) "
                    "VALUES (:org_id, :finding_id, :incident_id)"
                ),
                {"org_id": tenant.org_id, "finding_id": finding, "incident_id": linked_to},
            )
        await notify.finding_opened(
            connection, finding, org_id=tenant.org_id, severity="warning", at=at
        )
    return finding


async def _close_finding(
    engine: AsyncEngine, tenant: AlertTenant, finding: uuid.UUID, at: datetime
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE findings SET state = 'closed', closed_reason = 'recovered', "
                "closed_at = :at WHERE id = :id"
            ),
            {"at": at, "id": finding},
        )
        await notify.finding_closed(
            connection, finding, org_id=tenant.org_id, severity="warning", at=at
        )


async def _deliver(engine: AsyncEngine, tenant: AlertTenant, now: datetime) -> list[outbox.Notice]:
    """Everything due at `now`, each batch delivered, in the order it was claimed."""
    taken: list[outbox.Notice] = []
    while batches := _mine(await _claim(engine, tenant, now), tenant):
        for batch in batches:
            taken.extend(batch.notices)
            await _record(engine, batch, SendResult(outcome="delivered"), now)
    return taken


def _keys(taken: list[outbox.Notice]) -> list[tuple[str, str, str]]:
    return [(notice.subject.kind, notice.kind, notice.pagerduty_key) for notice in taken]


async def _withheld(engine: AsyncEngine, tenant: AlertTenant) -> list[tuple[str, str]]:
    """The rows not sent because their PagerDuty alert carries another open subject."""
    found = await rows(
        engine,
        "SELECT kind, outcome_reason, incident_id FROM notifications "
        "WHERE org_id = :org_id AND state = 'dropped' ORDER BY created_at",
        org_id=tenant.org_id,
    )
    return [("incident" if row.incident_id else "finding", row.outcome_reason) for row in found]


async def test_a_finding_linked_to_an_incident_already_paged_adds_to_its_alert(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """D29, AC45: the incident's PagerDuty alert, not a second page. The finding
    closing while the incident is still open resolves nothing: the alert is still
    the incident's."""
    now = future()
    pagerduty = await _pager_with_findings(engine, tenant)
    incident = await _incident_told(engine, tenant, pagerduty, now - timedelta(minutes=10))
    assert _keys(await _deliver(engine, tenant, now - timedelta(minutes=5))) == [
        ("incident", "opened", f"incident:{incident}")
    ]

    finding = await _open_finding(engine, tenant, now, linked_to=incident)
    await _close_finding(engine, tenant, finding, now + timedelta(minutes=1))
    taken = await _deliver(engine, tenant, now + timedelta(minutes=2))

    assert _keys(taken) == [("finding", "opened", f"incident:{incident}")]
    assert taken[0].noted
    assert await _withheld(engine, tenant) == [("finding", outbox.ALERT_STILL_OPEN_REASON)]


async def test_an_incident_linked_to_a_finding_already_paged_adds_to_its_alert(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """D35, AC45: the mirror. The finding paged first; a threshold incident on the
    same service opens later and is a note on the finding's alert, not a page. Its
    recovery leaves the alert open while the finding is; the finding's close
    resolves it."""
    now = future()
    pagerduty = await _pager_with_findings(engine, tenant)
    finding = await _open_finding(engine, tenant, now - timedelta(minutes=10))
    assert _keys(await _deliver(engine, tenant, now - timedelta(minutes=9))) == [
        ("finding", "opened", f"finding:{finding}")
    ]

    rule = await _threshold_rule(engine, tenant, pagerduty)
    incident = await _fire(engine, rule, now - timedelta(minutes=5))
    opened = await _deliver(engine, tenant, now - timedelta(minutes=4))
    assert _keys(opened) == [("incident", "opened", f"finding:{finding}")]
    assert opened[0].noted

    await _recover(engine, rule, now - timedelta(minutes=3))
    assert await _deliver(engine, tenant, now - timedelta(minutes=2)) == []
    assert await _withheld(engine, tenant) == [("incident", outbox.ALERT_STILL_OPEN_REASON)]

    await _close_finding(engine, tenant, finding, now)
    assert _keys(await _deliver(engine, tenant, now + timedelta(minutes=1))) == [
        ("finding", "resolved", f"finding:{finding}")
    ]
    assert incident is not None


async def test_a_finding_noted_on_an_incident_keeps_that_alert_after_the_incident_recovers(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """D29 in its first order, with the same rule: the incident's recovery leaves its
    alert open while the finding noted on it is open, the finding's later news stays
    on that alert, and the finding's close resolves it."""
    now = future()
    pagerduty = await _pager_with_findings(engine, tenant)
    rule = await _threshold_rule(engine, tenant, pagerduty)
    incident = await _fire(engine, rule, now - timedelta(minutes=10))
    finding = await _open_finding(engine, tenant, now - timedelta(minutes=8), linked_to=incident)
    assert _keys(await _deliver(engine, tenant, now - timedelta(minutes=7))) == [
        ("incident", "opened", f"incident:{incident}"),
        ("finding", "opened", f"incident:{incident}"),
    ]

    await _recover(engine, rule, now - timedelta(minutes=5))
    assert await _deliver(engine, tenant, now - timedelta(minutes=4)) == []
    assert await _withheld(engine, tenant) == [("incident", outbox.ALERT_STILL_OPEN_REASON)]

    async with engine.begin() as connection:
        await notify.finding_escalated(
            connection, finding, org_id=tenant.org_id, at=now - timedelta(minutes=3)
        )
    await _close_finding(engine, tenant, finding, now)
    assert _keys(await _deliver(engine, tenant, now + timedelta(minutes=1))) == [
        ("finding", "escalated", f"incident:{incident}"),
        ("finding", "resolved", f"incident:{incident}"),
    ]


async def test_a_second_incident_linked_through_the_finding_joins_the_first_alert(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """One problem pages once (D35): an error-rate incident, the finding noted on it,
    then a latency incident linked to the finding are all one PagerDuty alert."""
    now = future()
    pagerduty = await _pager_with_findings(engine, tenant)
    first = await _incident_told(engine, tenant, pagerduty, now - timedelta(minutes=10))
    await _open_finding(engine, tenant, now - timedelta(minutes=8), linked_to=first)
    await _deliver(engine, tenant, now - timedelta(minutes=7))

    latency = {"kind": "service_latency_p95", "threshold_ms": 500}
    second = await _fire(
        engine,
        await _threshold_rule(engine, tenant, pagerduty, latency),
        now - timedelta(minutes=5),
    )

    assert _keys(await _deliver(engine, tenant, now - timedelta(minutes=4))) == [
        ("incident", "opened", f"incident:{first}")
    ]
    assert second != first


async def test_a_linked_finding_s_webhook_keeps_its_own_subject(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """Only PagerDuty shares an alert: a webhook receiver keys on each subject."""
    now = future()
    async with engine.begin() as connection:
        hook = await add_channel(connection, tenant.org_id, kind="webhook", name="Hook")
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Findings",
            condition=anomaly_rule(),
            channel_ids=[hook],
        )
    rule = await _threshold_rule(engine, tenant, hook)
    incident = await _fire(engine, rule, now - timedelta(minutes=10))
    finding = await _open_finding(engine, tenant, now - timedelta(minutes=8), linked_to=incident)
    await _recover(engine, rule, now - timedelta(minutes=5))

    taken = await _deliver(engine, tenant, now)

    assert [(n.subject.kind, n.kind) for n in taken] == [
        ("incident", "opened"),
        ("finding", "opened"),
        ("incident", "resolved"),
    ]
    assert not any(notice.noted for notice in taken)
    assert finding is not None


async def test_a_finding_linked_to_an_incident_that_has_recovered_pages_on_its_own_key(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """A resolved PagerDuty alert would open afresh under the incident's key, and no
    close of the finding would ever reach it."""
    now = future()
    async with engine.begin() as connection:
        pagerduty = await add_channel(connection, tenant.org_id, kind="pagerduty", name="Pager")
    incident = await _incident_told(engine, tenant, pagerduty, now - timedelta(minutes=10))
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE incidents SET resolved_at = :at WHERE id = :id"),
            {"at": now - timedelta(minutes=1), "id": incident},
        )
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Findings",
            condition=anomaly_rule(),
            channel_ids=[pagerduty],
        )
        finding = await add_finding(connection, tenant, started_at=now - timedelta(minutes=5))
        await connection.execute(
            text(
                "INSERT INTO finding_incidents (org_id, finding_id, incident_id) "
                "VALUES (:org_id, :finding_id, :incident_id)"
            ),
            {"org_id": tenant.org_id, "finding_id": finding, "incident_id": incident},
        )
        await notify.finding_opened(
            connection, finding, org_id=tenant.org_id, severity="warning", at=now
        )

    taken: list[outbox.Notice] = []
    for _ in range(2):
        for batch in _mine(await _claim(engine, tenant, now), tenant):
            taken.extend(notice for notice in batch.notices if notice.subject.kind == "finding")
            await _record(engine, batch, SendResult(outcome="delivered"), now)
    assert [notice.pagerduty_key for notice in taken] == [f"finding:{finding}"]
