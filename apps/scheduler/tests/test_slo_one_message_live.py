"""What a reliability target's burn may withhold, against real Postgres (plan T5.3 to T5.6;
spec FR20 to FR26; design *What a burn may withhold*).

On a channel people read, a burn and the subject beside it, the same news, send one
message, in either order and in one claim (AC24, AC99); a higher severity always goes
(AC25); an ending goes wherever news of its subject did (AC26); a burn with nothing to
join is not delayed (AC31). When the told one ends, or fails, or its burn freezes, what
it held back goes in one message (AC52, AC53, AC57). Age, project and type each keep
news apart (AC54 to AC56). A service's snooze holds a burn's messages and lets them go
once (AC59). On PagerDuty one problem is one alert (AC27, AC30), and a webhook gets
everything (AC28).

Rows are decided and claimed a month from now, as the alert suites do, so the stack's
own deliveries worker never takes them; AC53 runs the real job on real minutes instead.
This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from alert_fixtures import (
    AlertTenant,
    add_channel,
    add_finding,
    add_rule,
    anomaly_rule,
    future,
    rows,
)
from clickhouse_connect.driver.client import Client
from live import require_stack
from nexovue_platform.alerts.model import check_subject, service_subject
from slo_rows import minutes, write_service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import incidents, outbox
from nexovue_scheduler.alerts.outbox import SendResult
from nexovue_scheduler.alerts.rules import Rule
from nexovue_scheduler.detection import notify
from nexovue_scheduler.detection.project_job import _link_incidents
from nexovue_scheduler.slos import job
from nexovue_scheduler.slos.store import SloStore, connect_store

READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)


def _reachable(url: str, port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or port), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(READER_DSN, 8123) and _reachable(POSTGRES_DSN, 5432),
    suite="F19's one-message suite",
    remedy="start the dev stack with `just dev`",
)

MINUTE: Final = timedelta(minutes=1)
SERVICE: Final = "checkout"
ERROR_RATE: Final = {"kind": "service_error_rate", "threshold_percent": 5}
LATENCY: Final = {"kind": "service_latency_p95", "threshold_ms": 500}
BURN: Final = {"kind": "slo_burn", "slo_ids": None, "minimum_severity": "warning"}

# What a delivered notice was about, in words a failing assertion reads plainly.
_SAID: Final = {
    "slo_burn": "burn",
    "anomaly_finding": "finding",
    "service_error_rate": "threshold",
    "service_latency_p95": "threshold",
    "service_silent": "silent",
    "check_state": "check",
}


@dataclass(frozen=True, slots=True)
class World:
    """One tenant, one channel people read, and a rule of each kind sending to it."""

    engine: AsyncEngine
    tenant: AlertTenant
    channel: uuid.UUID
    burns: Rule
    errors: Rule
    latency: Rule

    async def burn(
        self,
        at: datetime,
        *,
        severity: str = "critical",
        signal: str = "availability",
        service: str | None = SERVICE,
        endpoint: str | None = None,
        check_id: uuid.UUID | None = None,
        target: uuid.UUID | None = None,
        rule: Rule | None = None,
        judged_at: datetime | None = None,
    ) -> incidents.Subject:
        """A target's burn opens or escalates, judged by a tier at `judged_at` (`at`), as
        the job writes it: keyed by its target, about its service or check."""
        rule = rule or self.burns
        about = service_subject(service) if service else check_subject(check_id or uuid.uuid4())
        subject = incidents.Subject(
            key=f"slo:{target or uuid.uuid4()}",
            label=f"{service} {signal}, 99.9% over 28 days",
            about_key=about,
            about_signal=signal,
            about_endpoint=endpoint,
            about_check_id=check_id,
        )
        async with self.engine.begin() as connection:
            await incidents.fire(connection, rule, subject, severity=severity, at=at, detail={})
            await connection.execute(
                text(
                    """
                    INSERT INTO alert_evaluations (org_id, project_id, rule_id, subject_key,
                        phase, phase_since, evaluated_at)
                    VALUES (:org_id, :project_id, :rule_id, :key, 'firing', :at, :judged)
                    ON CONFLICT (rule_id, subject_key) DO UPDATE SET evaluated_at = :judged
                    """
                ),
                {
                    "org_id": rule.org_id,
                    "project_id": rule.project_id,
                    "rule_id": rule.id,
                    "key": subject.key,
                    "at": at,
                    "judged": judged_at or at,
                },
            )
        return subject

    async def end(self, subject: incidents.Subject, at: datetime, rule: Rule | None = None) -> None:
        async with self.engine.begin() as connection:
            await incidents.clear(connection, rule or self.burns, subject, at=at)

    async def threshold(
        self,
        at: datetime,
        *,
        rule: Rule | None = None,
        severity: str = "critical",
        subject: incidents.Subject | None = None,
    ) -> incidents.Subject:
        subject = subject or incidents.Subject(key=service_subject(SERVICE), label=SERVICE)
        async with self.engine.begin() as connection:
            await incidents.fire(
                connection, rule or self.errors, subject, severity=severity, at=at, detail={}
            )
        return subject

    async def finding(
        self,
        at: datetime,
        *,
        signals: Sequence[str] = ("failing",),
        severity: str = "critical",
        tenant: AlertTenant | None = None,
    ) -> uuid.UUID:
        """A finding on checkout opens, holding `signals`, linked by the project job to
        any open burn or threshold incident there (FR20)."""
        tenant = tenant or self.tenant
        async with self.engine.begin() as connection:
            finding = await add_finding(connection, tenant, started_at=at, severity=severity)
            await connection.execute(
                text("UPDATE findings SET signals = CAST(:signals AS jsonb) WHERE id = :id"),
                {"id": finding, "signals": _signals(signals)},
            )
            await _link_incidents(
                connection,
                {
                    "org_id": tenant.org_id,
                    "project_id": tenant.project_id,
                    "service_name": SERVICE,
                },
                finding,
            )
            await notify.finding_opened(
                connection, finding, org_id=tenant.org_id, severity=severity, at=at
            )
        return finding

    async def close_finding(self, finding: uuid.UUID, at: datetime, *, tell: bool = True) -> None:
        """The finding closes; `tell=False` is a person closing it with no ending written
        to this channel."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE findings SET state = 'closed', closed_reason = 'recovered', "
                    "closed_at = :at WHERE id = :id"
                ),
                {"at": at, "id": finding},
            )
            if tell:
                await notify.finding_closed(
                    connection, finding, org_id=self.tenant.org_id, severity="warning", at=at
                )

    async def claim(self, now: datetime) -> list[outbox.Batch]:
        async with self.engine.begin() as connection:
            claimed = await outbox.claim(connection, now=now, limit=100, org_id=self.tenant.org_id)
        return [batch for batch in claimed.batches if batch.channel.org_id == self.tenant.org_id]

    async def record(self, batch: outbox.Batch, result: SendResult, now: datetime) -> None:
        async with self.engine.begin() as connection:
            await outbox.record(connection, batch, result, now=now)

    async def deliver(self, now: datetime) -> list[list[str]]:
        """Everything due at `now`, each batch delivered, as the words each message said."""
        said = []
        while batches := await self.claim(now):
            for batch in batches:
                said.append([_words(notice) for notice in batch.notices])
                await self.record(batch, SendResult(outcome="delivered"), now)
        return said

    async def deliver_to(self, now: datetime) -> dict[str, list[list[tuple[str, str]]]]:
        """Everything due at `now`, delivered, by channel kind: each message's words and
        the PagerDuty alert each went to, `own` or `noted` on another's."""
        said: dict[str, list[list[tuple[str, str]]]] = {}
        while batches := await self.claim(now):
            for batch in batches:
                said.setdefault(batch.channel.kind, []).append(
                    [
                        (_words(notice), "noted" if notice.noted else "own")
                        for notice in batch.notices
                    ]
                )
                await self.record(batch, SendResult(outcome="delivered"), now)
        return said

    async def keys(self) -> dict[str, str]:
        """Each delivered PagerDuty row's alert, by what it said."""
        found = await rows(
            self.engine,
            """
            SELECT notifications.kind, notifications.dedup_key,
                   coalesce(incidents.condition_kind, 'anomaly_finding') AS condition_kind,
                   coalesce('incident:' || notifications.incident_id,
                            'finding:' || notifications.finding_id) AS own_key
            FROM notifications
            JOIN notification_channels ON notification_channels.id = notifications.channel_id
            LEFT JOIN incidents ON incidents.id = notifications.incident_id
            WHERE notifications.org_id = :org_id AND notification_channels.kind = 'pagerduty'
              AND notifications.dedup_key IS NOT NULL
            ORDER BY notifications.created_at, notifications.id
            """,
            org_id=self.tenant.org_id,
        )
        return {f"{_SAID[row.condition_kind]} {row.kind}": row.dedup_key for row in found}

    async def snooze(self, until: datetime, *, mute: bool = False) -> None:
        """Snooze, or mute, checkout's detection until `until` (F6's controls)."""
        column = "muted_until" if mute else "snoozed_until"
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    INSERT INTO service_detection_settings
                        (org_id, project_id, service_name, {column})
                    VALUES (:org_id, :project_id, :service, :until)
                    ON CONFLICT (org_id, project_id, service_name)
                    DO UPDATE SET {column} = :until
                    """
                ),
                {
                    "org_id": self.tenant.org_id,
                    "project_id": self.tenant.project_id,
                    "service": SERVICE,
                    "until": until,
                },
            )

    async def withheld(self) -> list[tuple[str, str | None, str]]:
        """Every row not sent: what it was, why in structure, and why in words."""
        found = await rows(
            self.engine,
            """
            SELECT notifications.kind, notifications.withheld_why, notifications.outcome_reason,
                   coalesce(incidents.condition_kind, 'anomaly_finding') AS condition_kind
            FROM notifications
            LEFT JOIN incidents ON incidents.id = notifications.incident_id
            WHERE notifications.org_id = :org_id AND notifications.state = 'dropped'
            ORDER BY notifications.created_at, notifications.id
            """,
            org_id=self.tenant.org_id,
        )
        return [
            (f"{_SAID[row.condition_kind]} {row.kind}", row.withheld_why, row.outcome_reason)
            for row in found
        ]


def _signals(names: Sequence[str]) -> str:
    return json.dumps([{"signal": name, "key": name} for name in names])


def _words(notice: outbox.Notice) -> str:
    return f"{_SAID[notice.subject.condition_kind]} {notice.kind}"


async def _rule(
    engine: AsyncEngine, tenant: AlertTenant, condition: dict[str, Any], *channels: uuid.UUID
) -> Rule:
    name = f"{condition['kind']} {uuid.uuid4().hex[:6]}"
    async with engine.begin() as connection:
        rule_id = await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name=name,
            condition=condition,
            channel_ids=list(channels),
        )
    return Rule(
        id=rule_id,
        org_id=tenant.org_id,
        project_id=tenant.project_id,
        name=name,
        condition_kind=str(condition["kind"]),
        condition=condition,
        muted_until=None,
        channel_ids=channels,
    )


async def _world(engine: AsyncEngine, tenant: AlertTenant, *channels: uuid.UUID) -> World:
    async with engine.begin() as connection:
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Findings",
            condition=anomaly_rule(),
            channel_ids=list(channels),
        )
    return World(
        engine=engine,
        tenant=tenant,
        channel=channels[0],
        burns=await _rule(engine, tenant, BURN, *channels),
        errors=await _rule(engine, tenant, ERROR_RATE, *channels),
        latency=await _rule(engine, tenant, LATENCY, *channels),
    )


@pytest.fixture
async def people(engine: AsyncEngine, tenant: AlertTenant) -> World:
    """Every rule sends to the owners, a channel people read."""
    return await _world(engine, tenant, tenant.owners_channel_id)


SAME_NEWS: Final = ("same_news", outbox.SAME_NEWS_REASON)


# ------------------------------------------------------------- AC24, AC99: one message


async def test_ac24_burn_then_finding_in_separate_claims_send_one_message(people: World) -> None:
    at = future()
    await people.burn(at)
    assert await people.deliver(at + MINUTE) == [["burn opened"]]

    await people.finding(at + 2 * MINUTE)

    assert await people.deliver(at + 3 * MINUTE) == []
    assert await people.withheld() == [("finding opened", *SAME_NEWS)]


async def test_ac24_finding_then_burn_in_separate_claims_send_one_message(people: World) -> None:
    at = future()
    await people.finding(at)
    assert await people.deliver(at + MINUTE) == [["finding opened"]]

    await people.burn(at + 2 * MINUTE)

    assert await people.deliver(at + 3 * MINUTE) == []
    assert await people.withheld() == [("burn opened", *SAME_NEWS)]


@pytest.mark.parametrize("burn_first", [True, False], ids=["burn first", "finding first"])
async def test_ac24_a_burn_and_a_finding_due_in_one_claim_send_the_finding(
    people: World, burn_first: bool
) -> None:
    """The tie: the finding has the richer page, so the burn's row is the one withheld."""
    at = future()
    if burn_first:
        await people.burn(at)
        await people.finding(at)
    else:
        await people.finding(at)
        await people.burn(at)

    assert await people.deliver(at + MINUTE) == [["finding opened"]]
    assert await people.withheld() == [("burn opened", *SAME_NEWS)]


async def test_ac24_threshold_then_finding_then_burn_send_two_and_drop_the_burn(
    people: World,
) -> None:
    at = future()
    await people.threshold(at)
    assert await people.deliver(at + MINUTE) == [["threshold opened"]]
    await people.finding(at + 2 * MINUTE)
    # No burn involved: F6's and F21's two messages stay (AC29).
    assert await people.deliver(at + 3 * MINUTE) == [["finding opened"]]

    await people.burn(at + 4 * MINUTE)

    assert await people.deliver(at + 5 * MINUTE) == []
    assert await people.withheld() == [("burn opened", *SAME_NEWS)]


async def test_ac24_burn_then_finding_then_threshold_send_one_and_drop_two(people: World) -> None:
    at = future()
    await people.burn(at)
    assert await people.deliver(at + MINUTE) == [["burn opened"]]
    await people.finding(at + 2 * MINUTE)
    assert await people.deliver(at + 3 * MINUTE) == []

    await people.threshold(at + 4 * MINUTE)

    assert await people.deliver(at + 5 * MINUTE) == []
    assert await people.withheld() == [
        ("finding opened", *SAME_NEWS),
        ("threshold opened", *SAME_NEWS),
    ]


@pytest.mark.parametrize("burn_first", [True, False], ids=["burn first", "threshold first"])
@pytest.mark.parametrize("signal", ["availability", "latency"])
async def test_ac99_a_burn_and_a_threshold_of_its_type_are_the_same_news(
    people: World, burn_first: bool, signal: str
) -> None:
    rule = people.errors if signal == "availability" else people.latency
    at = future()
    if burn_first:
        await people.burn(at, signal=signal)
    else:
        await people.threshold(at, rule=rule)
    first = "burn opened" if burn_first else "threshold opened"
    assert await people.deliver(at + MINUTE) == [[first]]

    if burn_first:
        await people.threshold(at + 2 * MINUTE, rule=rule)
    else:
        await people.burn(at + 2 * MINUTE, signal=signal)

    assert await people.deliver(at + 3 * MINUTE) == []
    second = "threshold opened" if burn_first else "burn opened"
    assert await people.withheld() == [(second, *SAME_NEWS)]


async def test_ac99_two_burns_on_one_service_and_type_are_the_same_news(people: World) -> None:
    at = future()
    await people.burn(at)
    assert await people.deliver(at + MINUTE) == [["burn opened"]]

    await people.burn(at + 2 * MINUTE, endpoint="/pay")

    assert await people.deliver(at + 3 * MINUTE) == []
    assert await people.withheld() == [("burn opened", *SAME_NEWS)]


async def test_ac99_one_target_under_two_rules_is_the_same_news(people: World) -> None:
    target = uuid.uuid4()
    other_rule = await _rule(people.engine, people.tenant, BURN, people.channel)
    at = future()
    await people.burn(at, target=target)
    assert await people.deliver(at + MINUTE) == [["burn opened"]]

    await people.burn(at + 2 * MINUTE, target=target, rule=other_rule)

    assert await people.deliver(at + 3 * MINUTE) == []
    assert await people.withheld() == [("burn opened", *SAME_NEWS)]


async def test_ac30_a_check_burn_beside_its_told_check_incident_sends_nothing_more(
    people: World,
) -> None:
    check = people.tenant.check_id
    assert check is not None
    checks = await _rule(
        people.engine,
        people.tenant,
        {"kind": "check_state", "states": {"down": "critical"}},
        people.channel,
    )
    at = future()
    await people.threshold(
        at, rule=checks, subject=incidents.Subject(key=check_subject(check), label="Checkout API")
    )
    assert await people.deliver(at + MINUTE) == [["check opened"]]

    await people.burn(at + 2 * MINUTE, service=None, check_id=check)

    assert await people.deliver(at + 3 * MINUTE) == []
    assert await people.withheld() == [("burn opened", *SAME_NEWS)]


async def test_ac30_a_check_incident_opening_after_its_check_s_burn_is_always_sent(
    people: World,
) -> None:
    check = people.tenant.check_id
    assert check is not None
    checks = await _rule(
        people.engine,
        people.tenant,
        {"kind": "check_state", "states": {"down": "critical"}},
        people.channel,
    )
    at = future()
    await people.burn(at, service=None, check_id=check)
    assert await people.deliver(at + MINUTE) == [["burn opened"]]

    await people.threshold(
        at + 2 * MINUTE,
        rule=checks,
        subject=incidents.Subject(key=check_subject(check), label="Checkout API"),
    )

    assert await people.deliver(at + 4 * MINUTE) == [["check opened"]]
    assert await people.withheld() == []


# ------------------------------------------------------------- never the same news


async def test_ac55_a_latency_burn_then_an_availability_burn_both_go(people: World) -> None:
    at = future()
    await people.burn(at, signal="latency")
    await people.deliver(at + MINUTE)
    await people.burn(at + 2 * MINUTE)

    assert await people.deliver(at + 3 * MINUTE) == [["burn opened"]]
    assert await people.withheld() == []


async def test_ac55_two_availability_burns_on_different_endpoints_both_go(people: World) -> None:
    at = future()
    await people.burn(at, endpoint="/pay")
    await people.deliver(at + MINUTE)
    await people.burn(at + 2 * MINUTE, endpoint="/cart")

    assert await people.deliver(at + 3 * MINUTE) == [["burn opened"]]


@pytest.mark.parametrize(
    "signals", [("slower",), ("traffic",)], ids=["slower only", "traffic only"]
)
async def test_ac55_an_availability_burn_and_a_finding_without_failing_both_go(
    people: World, signals: tuple[str, ...]
) -> None:
    at = future()
    await people.burn(at)
    await people.deliver(at + MINUTE)
    await people.finding(at + 2 * MINUTE, signals=signals)

    assert await people.deliver(at + 3 * MINUTE) == [["finding opened"]]


@pytest.mark.parametrize("burn_first", [True, False], ids=["burn first", "silence first"])
async def test_fr21_a_service_going_silent_is_never_the_same_news_as_a_burn(
    people: World, burn_first: bool
) -> None:
    silent = await _rule(people.engine, people.tenant, {"kind": "service_silent"}, people.channel)
    at = future()
    if burn_first:
        await people.burn(at)
    else:
        await people.threshold(at, rule=silent)
    await people.deliver(at + MINUTE)

    if burn_first:
        await people.threshold(at + 2 * MINUTE, rule=silent)
    else:
        await people.burn(at + 2 * MINUTE)

    second = "silent opened" if burn_first else "burn opened"
    assert await people.deliver(at + 3 * MINUTE) == [[second]]


async def test_ac56_a_burn_and_a_finding_in_two_projects_both_go(
    engine: AsyncEngine, tenant: AlertTenant, people: World
) -> None:
    other = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO projects (id, org_id, slug, name) "
                "VALUES (:id, :org_id, 'staging', 'Staging')"
            ),
            {"id": other, "org_id": tenant.org_id},
        )
        await add_rule(
            connection,
            tenant.org_id,
            other,
            name="Staging findings",
            condition=anomaly_rule(),
            channel_ids=[people.channel],
        )
    staging = AlertTenant(
        org_id=tenant.org_id,
        project_id=other,
        owner_email=tenant.owner_email,
        check_id=None,
        owners_channel_id=tenant.owners_channel_id,
        default_rule_id=tenant.default_rule_id,
    )
    staging_errors = await _rule(engine, staging, ERROR_RATE, people.channel)
    at = future()
    await people.burn(at)
    await people.deliver(at + MINUTE)
    await people.finding(at + 2 * MINUTE, tenant=staging)
    # A threshold incident needs no link to be the same news, so only the project parts it.
    await people.threshold(at + 2 * MINUTE, rule=staging_errors)

    assert await people.deliver(at + 3 * MINUTE) == [["finding opened", "threshold opened"]]


# --------------------------------------------------------------------- AC25, AC54


@pytest.mark.parametrize("burn_first", [True, False], ids=["burn first", "finding first"])
async def test_ac25_a_higher_severity_always_goes(people: World, burn_first: bool) -> None:
    at = future()
    if burn_first:
        await people.burn(at, severity="warning")
    else:
        await people.finding(at, severity="warning")
    await people.deliver(at + MINUTE)

    if burn_first:
        await people.finding(at + 2 * MINUTE, severity="critical")
    else:
        await people.burn(at + 2 * MINUTE, severity="critical")

    second = "finding opened" if burn_first else "burn opened"
    assert await people.deliver(at + 3 * MINUTE) == [[second]]


@pytest.mark.parametrize(
    ("told_ago", "withheld"),
    [(timedelta(days=3), False), (timedelta(minutes=59), True), (timedelta(minutes=61), False)],
    ids=["3 days", "59 minutes", "61 minutes"],
)
async def test_ac54_a_finding_withholds_a_burn_only_within_the_hour_of_telling(
    people: World, told_ago: timedelta, withheld: bool
) -> None:
    at = future()
    await people.finding(at - told_ago)
    assert await people.deliver(at - told_ago) == [["finding opened"]]

    await people.burn(at)

    assert await people.deliver(at) == ([] if withheld else [["burn opened"]])


# --------------------------------------------------------------------- AC26, AC31


async def test_ac26_a_withheld_burn_s_escalation_and_ending_both_arrive(people: World) -> None:
    at = future()
    await people.finding(at, severity="warning")
    await people.deliver(at + MINUTE)
    burn = await people.burn(at + 2 * MINUTE, severity="warning")
    assert await people.deliver(at + 3 * MINUTE) == []

    await people.burn(at + 4 * MINUTE, severity="critical", target=_target_of(burn))
    assert await people.deliver(at + 5 * MINUTE) == [["burn escalated"]]
    await people.end(burn, at + 6 * MINUTE)

    assert await people.deliver(at + 7 * MINUTE) == [["burn resolved"]]


async def test_ac26_a_withheld_finding_s_escalation_and_ending_both_arrive(people: World) -> None:
    at = future()
    await people.burn(at, severity="warning")
    await people.deliver(at + MINUTE)
    finding = await people.finding(at + 2 * MINUTE, severity="warning")
    assert await people.deliver(at + 3 * MINUTE) == []

    async with people.engine.begin() as connection:
        await notify.finding_escalated(
            connection, finding, org_id=people.tenant.org_id, at=at + 4 * MINUTE
        )
    assert await people.deliver(at + 5 * MINUTE) == [["finding escalated"]]
    await people.close_finding(finding, at + 6 * MINUTE)

    assert await people.deliver(at + 7 * MINUTE) == [["finding resolved"]]


async def test_ac26_an_ending_of_news_that_never_reached_the_channel_is_withheld(
    people: World,
) -> None:
    at = future()
    await people.burn(at)
    await people.deliver(at + MINUTE)
    finding = await people.finding(at + 2 * MINUTE)
    await people.deliver(at + 3 * MINUTE)

    await people.close_finding(finding, at + 4 * MINUTE)

    assert await people.deliver(at + 5 * MINUTE) == []
    assert await people.withheld() == [
        ("finding opened", *SAME_NEWS),
        ("finding resolved", None, outbox.NOTHING_TOLD_REASON),
    ]


async def test_ac26_a_withheld_opening_tells_nothing_so_withholds_nothing(people: World) -> None:
    """A burn whose own opening was dropped never reached the channel, so it cannot hold
    back a threshold incident beside it."""
    at = future()
    await people.finding(at)
    await people.deliver(at + MINUTE)
    await people.burn(at + 2 * MINUTE)
    assert await people.deliver(at + 3 * MINUTE) == []

    await people.threshold(at + 4 * MINUTE)

    assert await people.deliver(at + 5 * MINUTE) == [["threshold opened"]]


async def test_ac31_a_burn_with_nothing_to_join_goes_at_its_first_claim(people: World) -> None:
    at = future()
    await people.burn(at)

    assert await people.deliver(at) == [["burn opened"]]


# ------------------------------------------------------------- AC52, AC57: release


async def test_ac52_the_told_finding_ends_and_the_burn_goes_with_its_ending(people: World) -> None:
    at = future()
    finding = await people.finding(at)
    await people.deliver(at + MINUTE)
    await people.burn(at + 2 * MINUTE)
    assert await people.deliver(at + 3 * MINUTE) == []

    await people.close_finding(finding, at + 4 * MINUTE)

    assert await people.deliver(at + 5 * MINUTE) == [["finding resolved", "burn opened"]]
    # Released once: nothing more goes.
    assert await people.deliver(at + 6 * MINUTE) == []


async def test_ac52_the_told_burn_ends_and_the_finding_goes_with_its_ending(people: World) -> None:
    at = future()
    burn = await people.burn(at)
    await people.deliver(at + MINUTE)
    await people.finding(at + 2 * MINUTE)
    assert await people.deliver(at + 3 * MINUTE) == []

    await people.end(burn, at + 4 * MINUTE)

    assert await people.deliver(at + 5 * MINUTE) == [["burn resolved", "finding opened"]]


async def test_ac52_a_person_closing_the_finding_releases_by_the_next_sweep(people: World) -> None:
    at = future()
    finding = await people.finding(at)
    await people.deliver(at + MINUTE)
    await people.burn(at + 2 * MINUTE)
    assert await people.deliver(at + 3 * MINUTE) == []

    await people.close_finding(finding, at + 4 * MINUTE, tell=False)
    async with people.engine.begin() as connection:
        released = await outbox.release_everywhere(connection, now=at + 5 * MINUTE)

    assert released == 1
    assert await people.deliver(at + 5 * MINUTE) == [["burn opened"]]


async def test_ac52_a_withheld_subject_whose_news_since_reached_the_channel_gets_no_more(
    people: World,
) -> None:
    """Released once, and only where it is owed: an escalation that went through already
    told the channel, so the told one ending sends its ending alone."""
    at = future()
    finding = await people.finding(at, severity="warning")
    await people.deliver(at + MINUTE)
    burn = await people.burn(at + 2 * MINUTE, severity="warning")
    assert await people.deliver(at + 3 * MINUTE) == []
    await people.burn(at + 4 * MINUTE, severity="critical", target=_target_of(burn))
    assert await people.deliver(at + 5 * MINUTE) == [["burn escalated"]]

    await people.close_finding(finding, at + 6 * MINUTE)

    assert await people.deliver(at + 7 * MINUTE) == [["finding resolved"]]


async def test_ac57_two_burns_due_in_one_claim_send_the_older(people: World) -> None:
    at = future()
    await people.burn(at)
    await people.burn(at + timedelta(seconds=1), endpoint="/pay")

    assert await people.deliver(at + MINUTE) == [["burn opened"]]
    [(said, why, _)] = await people.withheld()
    assert (said, why) == ("burn opened", "same_news")
    [dropped] = await rows(
        people.engine,
        "SELECT incidents.about_endpoint FROM notifications "
        "JOIN incidents ON incidents.id = notifications.incident_id "
        "WHERE notifications.org_id = :org_id AND notifications.state = 'dropped'",
        org_id=people.tenant.org_id,
    )
    assert dropped.about_endpoint == "/pay"


async def test_ac57_a_retrying_first_row_still_counts_with_no_second_message(
    people: World,
) -> None:
    at = future()
    await people.burn(at)
    [first] = await people.claim(at + MINUTE)
    await people.record(first, SendResult(outcome="retry", reason="503"), at + MINUTE)

    # Before the retry, which waits at least 24 seconds.
    soon = at + MINUTE + timedelta(seconds=10)
    await people.burn(soon, endpoint="/pay")
    assert await people.deliver(soon) == []

    assert await people.deliver(at + 5 * MINUTE) == [["burn opened"]]
    assert [said for said, _, _ in await people.withheld()] == ["burn opened"]


async def test_ac57_a_first_row_that_fails_for_good_releases_the_withheld_one(
    people: World,
) -> None:
    at = future()
    await people.burn(at)
    [first] = await people.claim(at + MINUTE)
    await people.record(first, SendResult(outcome="retry", reason="503"), at + MINUTE)
    # Before the retry, which waits at least 24 seconds.
    soon = at + MINUTE + timedelta(seconds=10)
    await people.burn(soon, endpoint="/pay")
    assert await people.deliver(soon) == []

    [again] = await people.claim(at + 5 * MINUTE)
    await people.record(again, SendResult(outcome="failed", reason="410"), at + 5 * MINUTE)
    # Nothing else is due on the channel, so the delivery sweep's release finds it.
    async with people.engine.begin() as connection:
        await outbox.release_everywhere(connection, now=at + 6 * MINUTE)

    assert await people.deliver(at + 6 * MINUTE) == [["burn opened"]]


async def test_ac57_a_row_already_in_a_batch_is_never_withheld(people: World) -> None:
    at = future()
    await people.burn(at, severity="warning")
    [first] = await people.claim(at + MINUTE)
    await people.record(first, SendResult(outcome="retry", reason="503"), at + MINUTE)
    # Higher than the retrying burn, so it goes, and is then told at critical.
    soon = at + MINUTE + timedelta(seconds=10)
    await people.threshold(soon)
    assert await people.deliver(soon) == [["threshold opened"]]

    assert await people.deliver(at + 5 * MINUTE) == [["burn opened"]]
    assert await people.withheld() == []


def _target_of(subject: incidents.Subject) -> uuid.UUID:
    return uuid.UUID(subject.key.removeprefix("slo:"))


# ------------------------------------------------------- AC59: a service's snooze


@pytest.fixture
async def everyone(engine: AsyncEngine, tenant: AlertTenant) -> World:
    """Every rule sends to the owners, a PagerDuty service and a webhook."""
    async with engine.begin() as connection:
        pager = await add_channel(connection, tenant.org_id, kind="pagerduty", name="Pager")
        hook = await add_channel(connection, tenant.org_id, kind="webhook", name="Hook")
    return await _world(engine, tenant, tenant.owners_channel_id, pager, hook)


SNOOZED: Final = ("service_snoozed", outbox.SNOOZED_REASON)


async def _muted_incidents(world: World) -> list[bool]:
    found = await rows(
        world.engine,
        "SELECT muted FROM incidents WHERE org_id = :org_id",
        org_id=world.tenant.org_id,
    )
    return [row.muted for row in found]


async def test_ac59_a_snooze_before_opening_records_the_burn_and_tells_only_machines_its_news(
    everyone: World,
) -> None:
    at = future()
    await everyone.snooze(at + timedelta(hours=4))

    await everyone.burn(at)

    assert await everyone.deliver_to(at + MINUTE) == {"webhook": [[("burn opened", "own")]]}
    assert await everyone.withheld() == [("burn opened", *SNOOZED), ("burn opened", *SNOOZED)]
    assert await _muted_incidents(everyone) == [False]


async def test_ac59_a_snooze_after_opening_holds_news_and_people_s_ending_not_a_machine_s(
    everyone: World,
) -> None:
    at = future()
    burn = await everyone.burn(at, severity="warning")
    await everyone.deliver_to(at + MINUTE)
    await everyone.snooze(at + timedelta(hours=4))

    await everyone.burn(at + 2 * MINUTE, severity="critical", target=_target_of(burn))
    assert await everyone.deliver_to(at + 3 * MINUTE) == {"webhook": [[("burn escalated", "own")]]}
    await everyone.end(burn, at + 4 * MINUTE)

    assert await everyone.deliver_to(at + 5 * MINUTE) == {
        "pagerduty": [[("burn resolved", "own")]],
        "webhook": [[("burn resolved", "own")]],
    }
    assert await _muted_incidents(everyone) == [False]


async def test_ac59_a_snooze_ending_while_the_burn_holds_sends_exactly_one_message(
    everyone: World,
) -> None:
    at = future()
    await everyone.snooze(at + timedelta(hours=1))
    burn = await everyone.burn(at)
    await everyone.burn(at + 30 * MINUTE, target=_target_of(burn))
    await everyone.deliver_to(at + 31 * MINUTE)

    after = at + timedelta(hours=1, minutes=1)
    async with everyone.engine.begin() as connection:
        await outbox.release_everywhere(connection, now=after)

    assert await everyone.deliver_to(after) == {
        "owners": [[("burn opened", "own")]],
        "pagerduty": [[("burn opened", "own")]],
    }
    async with everyone.engine.begin() as connection:
        assert await outbox.release_everywhere(connection, now=after + MINUTE) == 0
    assert await everyone.deliver_to(after + MINUTE) == {}


async def test_ac59_a_burn_that_ended_during_the_snooze_tells_its_ending_once_where_told(
    everyone: World,
) -> None:
    at = future()
    burn = await everyone.burn(at)
    await everyone.deliver_to(at + MINUTE)
    await everyone.snooze(at + timedelta(hours=1))
    await everyone.end(burn, at + 2 * MINUTE)
    told = await everyone.deliver_to(at + 3 * MINUTE)
    assert "owners" not in told

    after = at + timedelta(hours=1, minutes=1)
    async with everyone.engine.begin() as connection:
        await outbox.release_everywhere(connection, now=after)

    assert await everyone.deliver_to(after) == {"owners": [[("burn resolved", "own")]]}
    async with everyone.engine.begin() as connection:
        await outbox.release_everywhere(connection, now=after + MINUTE)
    assert await everyone.deliver_to(after + MINUTE) == {}


@pytest.mark.parametrize("before_opening", [True, False], ids=["before", "after"])
async def test_ac59_a_service_mute_stops_no_burn_message(
    everyone: World, before_opening: bool
) -> None:
    at = future()
    if before_opening:
        await everyone.snooze(at + timedelta(days=30), mute=True)
    burn = await everyone.burn(at, severity="warning")
    first = await everyone.deliver_to(at + MINUTE)
    if not before_opening:
        await everyone.snooze(at + timedelta(days=30), mute=True)
    await everyone.burn(at + 2 * MINUTE, severity="critical", target=_target_of(burn))

    assert first["owners"] == [[("burn opened", "own")]]
    assert (await everyone.deliver_to(at + 3 * MINUTE))["owners"] == [[("burn escalated", "own")]]
    assert await everyone.withheld() == []
    assert await _muted_incidents(everyone) == [False]


# --------------------------------------------- AC27, AC28, AC30: PagerDuty and webhooks


async def test_ac27_a_burn_then_its_linked_finding_make_one_alert_the_last_to_end_resolves(
    everyone: World,
) -> None:
    at = future()
    burn = await everyone.burn(at)
    await everyone.deliver_to(at + MINUTE)
    finding = await everyone.finding(at + 2 * MINUTE)
    told = await everyone.deliver_to(at + 3 * MINUTE)
    assert told["pagerduty"] == [[("finding opened", "noted")]]
    assert told["webhook"] == [[("finding opened", "own")]]  # AC28

    await everyone.end(burn, at + 4 * MINUTE)
    told = await everyone.deliver_to(at + 5 * MINUTE)
    assert "pagerduty" not in told  # the finding still holds the alert open
    await everyone.close_finding(finding, at + 6 * MINUTE)
    told = await everyone.deliver_to(at + 7 * MINUTE)

    assert told["pagerduty"] == [[("finding resolved", "noted")]]
    keys = await everyone.keys()
    assert len(set(keys.values())) == 1
    assert keys["finding resolved"] == f"incident:{await _incident_id(everyone, burn)}"


async def test_ac27_a_finding_then_its_burn_make_one_alert(everyone: World) -> None:
    at = future()
    await everyone.finding(at)
    await everyone.deliver_to(at + MINUTE)
    await everyone.burn(at + 2 * MINUTE)

    told = await everyone.deliver_to(at + 3 * MINUTE)

    assert told["pagerduty"] == [[("burn opened", "noted")]]
    assert told["webhook"] == [[("burn opened", "own")]]


@pytest.mark.parametrize("second", ["burn", "threshold"])
async def test_ac27_a_burn_shares_with_a_burn_or_a_threshold_on_its_service_with_no_finding(
    everyone: World, second: str
) -> None:
    at = future()
    await everyone.burn(at)
    await everyone.deliver_to(at + MINUTE)
    if second == "burn":
        await everyone.burn(at + 2 * MINUTE, signal="latency")
    else:
        await everyone.threshold(at + 2 * MINUTE)

    told = await everyone.deliver_to(at + 3 * MINUTE)

    assert told["pagerduty"] == [[(f"{second} opened", "noted")]]


async def test_ac27_a_threshold_then_a_burn_on_its_service_share_its_alert(
    everyone: World,
) -> None:
    at = future()
    await everyone.threshold(at)
    await everyone.deliver_to(at + MINUTE)
    await everyone.burn(at + 2 * MINUTE)

    assert (await everyone.deliver_to(at + 3 * MINUTE))["pagerduty"] == [[("burn opened", "noted")]]


async def test_ac27_a_service_going_silent_makes_its_own_alert(everyone: World) -> None:
    silent = await _rule(
        everyone.engine, everyone.tenant, {"kind": "service_silent"}, *_channels(everyone)
    )
    at = future()
    await everyone.burn(at)
    await everyone.deliver_to(at + MINUTE)
    await everyone.threshold(at + 2 * MINUTE, rule=silent)

    assert (await everyone.deliver_to(at + 3 * MINUTE))["pagerduty"] == [[("silent opened", "own")]]


async def test_ac27_a_critical_burn_after_a_warning_alert_pages(everyone: World) -> None:
    at = future()
    await everyone.finding(at, severity="warning")
    await everyone.deliver_to(at + MINUTE)
    await everyone.burn(at + 2 * MINUTE, severity="critical")

    assert (await everyone.deliver_to(at + 3 * MINUTE))["pagerduty"] == [[("burn opened", "own")]]


async def test_ac30_a_check_burn_is_a_note_on_its_check_s_alert(everyone: World) -> None:
    check = everyone.tenant.check_id
    assert check is not None
    checks = await _rule(
        everyone.engine,
        everyone.tenant,
        {"kind": "check_state", "states": {"down": "critical"}},
        *_channels(everyone),
    )
    at = future()
    await everyone.threshold(
        at, rule=checks, subject=incidents.Subject(key=check_subject(check), label="Checkout API")
    )
    await everyone.deliver_to(at + MINUTE)
    await everyone.burn(at + 2 * MINUTE, service=None, check_id=check)

    told = await everyone.deliver_to(at + 3 * MINUTE)

    assert told["pagerduty"] == [[("burn opened", "noted")]]
    assert told["webhook"] == [[("burn opened", "own")]]
    assert "owners" not in told


def _channels(world: World) -> tuple[uuid.UUID, ...]:
    return world.burns.channel_ids


async def _incident_id(world: World, subject: incidents.Subject) -> uuid.UUID:
    [found] = await rows(
        world.engine,
        "SELECT id FROM incidents WHERE org_id = :org_id AND subject_key = :key",
        org_id=world.tenant.org_id,
        key=subject.key,
    )
    return uuid.UUID(str(found.id))


async def test_ac94_a_burn_and_a_finding_sharing_an_alert_resolve_it_when_the_burn_closes(
    everyone: World,
) -> None:
    """The service stops and the finding closes: the alert stays open for the burn, and
    resolves when the burn is closed for a silent source (FR17)."""
    at = future()
    burn = await everyone.burn(at)
    await everyone.deliver_to(at + MINUTE)
    finding = await everyone.finding(at + 2 * MINUTE)
    await everyone.deliver_to(at + 3 * MINUTE)
    await everyone.close_finding(finding, at + timedelta(hours=1))
    assert "pagerduty" not in await everyone.deliver_to(at + timedelta(hours=1, minutes=1))

    closed_at = at + timedelta(hours=24)
    async with everyone.engine.begin() as connection:
        await incidents.close_unjudged(
            connection, everyone.burns, burn, at=closed_at, detail={"closed_why": "source_silent"}
        )
    told = await everyone.deliver_to(closed_at + MINUTE)

    assert told["pagerduty"] == [[("burn closed", "own")]]
    assert told["owners"] == [[("burn closed", "own")]]


# ------------------------------------------------- AC53: errors, then silence


@pytest.fixture
def slo_store() -> SloStore:
    return connect_store(READER_DSN)


async def _evaluated_at(engine: AsyncEngine, rule: Rule) -> datetime:
    [found] = await rows(
        engine,
        "SELECT evaluated_at FROM alert_evaluations WHERE org_id = :org_id AND rule_id = :rule_id",
        org_id=rule.org_id,
        rule_id=rule.id,
    )
    stamp: datetime = found.evaluated_at
    return stamp


async def test_ac53_a_frozen_burn_withholds_nothing_and_releases_what_it_held(
    engine: AsyncEngine, administrator: Client, project: AlertTenant, slo_store: SloStore
) -> None:
    """The real `judge_slos` through a silence: a critical burn, the service stops, and the
    burn's `evaluated_at` stops moving once no tier can judge, while the job goes on. Then
    it withholds nothing, and what it held back goes."""
    from test_slo_job_live import BUSY, _counted_target

    stopped = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=3)
    counts = dict.fromkeys(minutes(stopped - timedelta(hours=25), 25 * 60 + 1), (BUSY, 0))
    # The last hour half failing: every tier holds, the slow one for 2 hours after.
    for minute in minutes(stopped - timedelta(minutes=59), 60):
        counts[minute] = (BUSY, BUSY // 2)
    write_service(administrator, project, SERVICE, counts)
    world = await _world(engine, project, project.owners_channel_id)
    silent = await _rule(engine, project, {"kind": "service_silent"}, project.owners_channel_id)
    await _counted_target(engine, project)

    await job.judge_slos(engine, slo_store, project.project_id, stopped)
    assert await world.deliver(stopped) == [["burn opened"]]
    await world.threshold(stopped + 15 * MINUTE, rule=silent)
    assert await world.deliver(stopped + 15 * MINUTE) == [["silent opened"]]
    await job.judge_slos(engine, slo_store, project.project_id, stopped + 20 * MINUTE)
    await world.finding(stopped + 20 * MINUTE)
    assert await world.deliver(stopped + 20 * MINUTE) == []

    judged = {}
    for offset in (60, 119, 120, 121, 125, 140):
        await job.judge_slos(engine, slo_store, project.project_id, stopped + offset * MINUTE)
        judged[offset] = await _evaluated_at(engine, world.burns)
    frozen = judged[121]
    assert frozen <= stopped + timedelta(hours=2)
    assert judged[125] == judged[140] == frozen
    [status] = await rows(
        engine,
        "SELECT evaluated_minute FROM slo_status WHERE org_id = :org_id",
        org_id=project.org_id,
    )
    assert status.evaluated_minute == stopped + 140 * MINUTE  # the job went on running

    later = stopped + 140 * MINUTE
    await world.threshold(later)
    [message] = await world.deliver(later)

    assert sorted(message) == ["finding opened", "threshold opened"]
