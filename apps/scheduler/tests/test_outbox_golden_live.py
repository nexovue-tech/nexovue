"""What the outbox sends for every kind that existed before F3's `explained`, byte for
byte, through the real claim and `_TAKEN` (F3 plan T2.8; AC78).

F3 changes a path every alert uses: `_TAKEN` reads the follow-up's cause, and
`compose_batch` keeps `explained` out of its count. So each existing kind, on each kind of
channel, is composed and rendered as `dispatch.send` would, and compared with
`fixtures/outbox_golden.json`, which was written from the code **before** Step 2 changed
it. A difference is a change to an alert a customer already receives.

Rows are dated in 2099 so the stack's own delivery sweep, which CI runs beside this
suite, never finds them due. Ids are replaced by their order of first appearance.

To rewrite the fixture after an intended change: `NEXOVUE_WRITE_OUTBOX_GOLDEN=1`.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from alert_fixtures import (
    AlertTenant,
    add_channel,
    add_finding,
    drop_tenant,
    make_tenant,
    postgres_reachable,
)
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import delivery, messages, outbox, payloads
from nexovue_scheduler.alerts.dispatch import message_for
from nexovue_scheduler.alerts.outbox import SendResult

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_outbox_golden_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

GOLDEN: Final = Path(__file__).parent / "fixtures" / "outbox_golden.json"
WRITE_VARIABLE: Final = "NEXOVUE_WRITE_OUTBOX_GOLDEN"
BASE_URL: Final = "https://console.example.test"
# Far enough ahead that no running delivery sweep ever finds these rows due.
START: Final = datetime(2099, 1, 5, 10, 0, tzinfo=UTC)
CLAIMED_AT: Final = START + timedelta(hours=1)
CHANNEL_KINDS: Final = ("owners", "slack", "discord", "teams", "pagerduty", "webhook")
SIGNALS: Final = [
    {"signal": "slower", "key": "slower", "normal": 110.0, "worst": 412.0},
    {"signal": "failing", "key": "failing:/pay", "endpoint": "/pay", "normal": 0.4, "worst": 6.0},
]
# Every kind before F3, by its subject.
FINDING_KINDS: Final = ("opened", "escalated", "joined", "flapping", "adopted", "resolved")
INCIDENT_KINDS: Final = ("opened", "repeated", "escalated", "resolved", "closed")
PROJECT_KINDS: Final = ("stopped", "running_again")
_UUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.fixture
async def acme(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await make_tenant(engine, with_check=False)
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


async def _subjects(engine: AsyncEngine, tenant: AlertTenant) -> dict[str, uuid.UUID]:
    async with engine.begin() as connection:
        finding = await add_finding(
            connection, tenant, started_at=START, closed_at=START + timedelta(minutes=42)
        )
        await connection.execute(
            text("UPDATE findings SET signals = CAST(:signals AS jsonb) WHERE id = :id"),
            {"signals": json.dumps(SIGNALS), "id": finding},
        )
        incident = await connection.scalar(
            text(
                """
                INSERT INTO incidents (org_id, project_id, rule_name, condition_kind,
                                       subject_key, subject_label, severity, opened_at,
                                       resolved_at, detail)
                VALUES (:org_id, :project_id, 'Checkout API down', 'check_state',
                        'check:golden', 'Checkout API', 'critical', :opened, :resolved,
                        CAST(:detail AS jsonb))
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "opened": START,
                "resolved": START + timedelta(minutes=17),
                "detail": json.dumps(
                    {"state": "down", "reason": "connection timed out", "location": "local"}
                ),
            },
        )
    return {"finding": finding, "incident": incident}


async def _notify(
    engine: AsyncEngine,
    tenant: AlertTenant,
    channel_id: uuid.UUID,
    subject: str,
    subject_id: uuid.UUID | None,
    kind: str,
) -> None:
    severity = "critical" if kind == "escalated" or subject == "incident" else "warning"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO notifications (org_id, project_id, incident_id, finding_id,
                                           channel_id, kind, sequence, severity,
                                           deliver_after, created_at)
                VALUES (:org_id, :project_id, :incident_id, :finding_id, :channel_id, :kind,
                        0, :severity, :at, :at)
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "incident_id": subject_id if subject == "incident" else None,
                "finding_id": subject_id if subject == "finding" else None,
                "channel_id": channel_id,
                "kind": kind,
                "severity": severity,
                "at": START + timedelta(minutes=1),
            },
        )


def _rendered(batch: outbox.Batch) -> dict[str, Any]:
    """What `dispatch.send` would put on the wire for this channel kind."""
    kind = batch.channel.kind
    message = message_for(kind, messages.compose_batch(batch.notices, app_base_url=BASE_URL))
    notice = batch.notices[0]
    if kind == "owners":
        return {
            "subject": message.summary,
            "body": "\n".join(message.details),
            "link": message.link,
            "rule_name": message.rule_name,
            "manage_link": message.manage_link,
        }
    if kind == "slack":
        body = payloads.slack(message)
    elif kind == "discord":
        body = payloads.discord(message)
    elif kind == "teams":
        body = payloads.teams(message)
    elif kind == "pagerduty":
        body = payloads.pagerduty(
            message,
            routing_key="r" * 32,
            dedup_key=notice.pagerduty_key,
            subject=notice.subject.label,
            app_base_url=BASE_URL,
            noted=notice.noted,
        )
    else:
        body = payloads.webhook(
            message,
            subject_kind=notice.subject.kind,
            subject_id=str(notice.subject.id),
            subject=notice.subject.label,
            opened_at=notice.subject.opened_at,
            resolved_at=notice.subject.closed_at,
            sent_at=CLAIMED_AT,
        )
    rendered: dict[str, Any] = json.loads(body)
    return rendered


def _normalised(value: Any) -> Any:
    """Ids in their order of first appearance, so the fixture outlives a run."""
    seen: dict[str, str] = {}

    def name(found: re.Match[str]) -> str:
        return seen.setdefault(found.group(0), f"<id-{len(seen) + 1}>")

    return json.loads(_UUID.sub(name, json.dumps(value, sort_keys=True)))


async def test_every_existing_kind_is_sent_as_it_was_before_f3(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC78: identical bytes for each existing kind and channel."""
    subjects = await _subjects(engine, acme)
    channels: dict[str, uuid.UUID] = {"owners": acme.owners_channel_id}
    async with engine.begin() as connection:
        for kind in CHANNEL_KINDS[1:]:
            channels[kind] = await add_channel(connection, acme.org_id, kind=kind, name=kind)
    cases = [
        *(("finding", kind) for kind in FINDING_KINDS),
        *(("incident", kind) for kind in INCIDENT_KINDS),
        *(("project", kind) for kind in PROJECT_KINDS),
    ]

    sent: dict[str, Any] = {}
    for number, (subject, notification) in enumerate(cases):
        # A minute apart, past the burst cap's window (FR12).
        at = CLAIMED_AT + timedelta(minutes=2 * number)
        for channel_kind in CHANNEL_KINDS:
            await _notify(
                engine,
                acme,
                channels[channel_kind],
                subject,
                subjects.get(subject),
                notification,
            )
            async with engine.begin() as connection:
                claimed = await outbox.claim(connection, now=at, limit=100, org_id=acme.org_id)
            [batch] = claimed.batches
            sent[f"{subject}.{notification}.{channel_kind}"] = _rendered(batch)
            async with engine.begin() as connection:
                await outbox.record(connection, batch, SendResult(outcome="delivered"), now=at)

    found = _normalised(sent)
    if os.environ.get(WRITE_VARIABLE) == "1":
        GOLDEN.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert found == json.loads(GOLDEN.read_text(encoding="utf-8"))


async def _open_finding(engine: AsyncEngine, tenant: AlertTenant) -> uuid.UUID:
    """A finding still open, as a follow-up needs (FR21)."""
    async with engine.begin() as connection:
        return await add_finding(connection, tenant, started_at=START)


async def _claim_one(engine: AsyncEngine, tenant: AlertTenant, at: datetime) -> outbox.Batch:
    async with engine.begin() as connection:
        claimed = await outbox.claim(connection, now=at, limit=100, org_id=tenant.org_id)
    [batch] = claimed.batches
    return batch


async def _states(engine: AsyncEngine, tenant: AlertTenant) -> dict[str, tuple[str, str | None]]:
    async with engine.connect() as connection:
        found = await connection.execute(
            text(
                "SELECT kind, state, outcome_reason FROM notifications "
                "WHERE org_id = :org_id AND finding_id IS NOT NULL"
            ),
            {"org_id": tenant.org_id},
        )
        return {row.kind: (row.state, row.outcome_reason) for row in found}


async def test_a_follow_up_with_no_cause_at_delivery_is_dropped_alone(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC27, AC78: no qualifying version when it is sent: that row is dropped with its
    reason, and the opening beside it in the same message still goes."""
    finding = await _open_finding(engine, acme)
    for kind in ("opened", "explained"):
        await _notify(engine, acme, acme.owners_channel_id, "finding", finding, kind)

    batch = await _claim_one(engine, acme, CLAIMED_AT)
    kept = await delivery._without_unwritable(engine, batch, BASE_URL)

    assert kept is not None and [notice.kind for notice in kept.notices] == ["opened"]
    assert (await _states(engine, acme))["explained"] == ("dropped", outbox.UNWRITABLE_REASON)


async def _explanation(
    engine: AsyncEngine,
    tenant: AlertTenant,
    finding: uuid.UUID,
    version: int,
    cause: dict[str, Any],
) -> None:
    item = {
        "id": "deploy:golden",
        "kind": "deploy",
        "basis": "verified",
        "sentence": [],
        "values": {
            "service": "checkout",
            "version": f"v2.{version}",
            "at": (START - timedelta(minutes=4)).isoformat(),
            "deploys_last_7_days": 3,
            "days_of_records": 21,
        },
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO finding_explanations (org_id, project_id, finding_id, version,
                    fingerprint, evidence, aliases, likely_cause, summary)
                VALUES (:org_id, :project_id, :finding_id, :version, 'f',
                        CAST(:evidence AS jsonb), '{}', CAST(:cause AS jsonb), '{}')
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "finding_id": finding,
                "version": version,
                "evidence": json.dumps({"items": [item]}),
                "cause": json.dumps({"evidence_id": "deploy:golden", **cause}),
            },
        )


async def test_a_follow_up_names_the_newest_likely_signed_cause_as_it_is_sent(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """Design, *Data path*: read at the claim, from the newest version whose cause is
    likely and verified; a later version naming a weaker cause does not displace it."""
    finding = await _open_finding(engine, acme)
    likely = {"rule": "signed_deploy", "strength": "likely", "basis": "verified"}
    await _explanation(engine, acme, finding, 1, likely)
    await _explanation(engine, acme, finding, 2, likely)
    await _explanation(engine, acme, finding, 3, {**likely, "strength": "possible"})
    await _notify(engine, acme, acme.owners_channel_id, "finding", finding, "explained")

    batch = await _claim_one(engine, acme, CLAIMED_AT)
    message = messages.compose_batch(batch.notices, app_base_url=BASE_URL)

    assert message.details[0] == (
        "A likely place to look on checkout: the deploy of checkout v2.2, 4 min before this "
        "(3 deploys in 7 days)."
    )


async def test_a_composer_that_raises_drops_only_its_own_row(
    engine: AsyncEngine, acme: AlertTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC78: one notice's composer failing never holds back the rest of its batch."""
    finding = (await _subjects(engine, acme))["finding"]
    for kind in ("opened", "joined"):
        await _notify(engine, acme, acme.owners_channel_id, "finding", finding, kind)
    real = messages.compose

    def failing_for_joined(notice: outbox.Notice, *, app_base_url: str) -> messages.AlertMessage:
        if notice.kind == "joined":
            raise RuntimeError("a composer bug")
        return real(notice, app_base_url=app_base_url)

    monkeypatch.setattr(messages, "compose", failing_for_joined)
    batch = await _claim_one(engine, acme, CLAIMED_AT)
    kept = await delivery._without_unwritable(engine, batch, BASE_URL)

    assert kept is not None and [notice.kind for notice in kept.notices] == ["opened"]
    assert (await _states(engine, acme))["joined"] == ("dropped", outbox.UNWRITABLE_REASON)


@pytest.mark.parametrize("news_reached", [True, False])
async def test_a_follow_up_goes_only_where_news_of_its_finding_reached(
    engine: AsyncEngine, acme: AlertTenant, news_reached: bool
) -> None:
    """F19's AC58, held for F3's kind: a channel whose opening of this finding was withheld
    for the same news, and never reached it, gets no follow-up either."""
    finding = (await _subjects(engine, acme))["finding"]
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO notifications (org_id, project_id, finding_id, channel_id, kind,
                                           sequence, severity, deliver_after, created_at,
                                           state, withheld_why, delivered_at)
                VALUES (:org_id, :project_id, :finding_id, :channel_id, 'opened', 0, 'warning',
                        :at, :at, :state, :why, :delivered)
                """
            ),
            {
                "org_id": acme.org_id,
                "project_id": acme.project_id,
                "finding_id": finding,
                "channel_id": acme.owners_channel_id,
                "at": START,
                "state": "delivered" if news_reached else "dropped",
                "why": None if news_reached else "same_news",
                "delivered": START if news_reached else None,
            },
        )
        if news_reached:
            # Some other row of this finding was withheld here, so the rule looks at it.
            await connection.execute(
                text(
                    """
                    INSERT INTO notifications (org_id, project_id, finding_id, channel_id,
                                               kind, sequence, severity, deliver_after,
                                               created_at, state, withheld_why)
                    VALUES (:org_id, :project_id, :finding_id, :channel_id, 'joined', 1,
                            'warning', :at, :at, 'dropped', 'same_news')
                    """
                ),
                {
                    "org_id": acme.org_id,
                    "project_id": acme.project_id,
                    "finding_id": finding,
                    "channel_id": acme.owners_channel_id,
                    "at": START,
                },
            )
    await _notify(engine, acme, acme.owners_channel_id, "finding", finding, "explained")

    async with engine.begin() as connection:
        claimed = await outbox.claim(connection, now=CLAIMED_AT, limit=100, org_id=acme.org_id)

    taken = [notice.kind for batch in claimed.batches for notice in batch.notices]
    if news_reached:
        assert taken == ["explained"]
    else:
        assert taken == []
        assert (await _states(engine, acme))["explained"] == (
            "dropped",
            outbox.NOTHING_TOLD_REASON,
        )


def test_postgres_is_reachable() -> None:
    assert postgres_reachable()
