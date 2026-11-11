"""F3's follow-up message, `explained`, as the gather job writes it (plan T2.7; AC25,
AC26, AC28, AC84's channel case, FR48, FR50).

At most one per finding per channel at any sequence, only to channels people read that
were told the finding and have not turned it off, never to PagerDuty or a webhook, and
at most `PER_CHANNEL` a channel in `CAP_WINDOW`, also under two gathers at once.

Every row here is due a month from now (`alert_fixtures.future`), so the stack's own
delivery sweep, which CI runs beside these suites, never takes one.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from alert_fixtures import (
    AlertTenant,
    add_channel,
    add_finding,
    drop_tenant,
    future,
    make_tenant,
    postgres_reachable,
)
from live import require_stack
from nexovue_platform.alerts.model import EXPLAINED
from nexovue_platform.detection import FINDING_TOLD_KINDS, RESOLVE_FINDING_SQL, resolve_parameters
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.evidence import explained
from nexovue_scheduler.evidence.explained import (
    CAP_WINDOW,
    LAST_SEQUENCE,
    PER_CHANNEL,
    after_settling,
    sends_for,
)
from nexovue_scheduler.evidence.gather import gather

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_evidence_explained_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

SERVICE = "checkout"
START = future()
SETTLED = START + timedelta(minutes=6)
LIKELY_SIGNED = {
    "evidence_id": "deploy:1",
    "rule": "signed_deploy",
    "strength": "likely",
    "basis": "verified",
}


@pytest.fixture
async def acme(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await make_tenant(engine, with_check=False)
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


async def open_finding(engine: AsyncEngine, tenant: AlertTenant, **changes: Any) -> uuid.UUID:
    async with engine.begin() as connection:
        return await add_finding(connection, tenant, started_at=START, **changes)


async def channel(engine: AsyncEngine, tenant: AlertTenant, kind: str, **changes: Any) -> uuid.UUID:
    async with engine.begin() as connection:
        made = await add_channel(
            connection, tenant.org_id, kind=kind, name=f"{kind} {uuid.uuid4()}"
        )
        if changes.get("sends_explained") is False:
            await connection.execute(
                text("UPDATE notification_channels SET sends_explained = false WHERE id = :id"),
                {"id": made},
            )
        return made


async def tell(
    engine: AsyncEngine,
    tenant: AlertTenant,
    finding_id: uuid.UUID,
    channel_id: uuid.UUID,
    kind: str = "opened",
    sequence: int = 0,
    at: datetime = START,
) -> None:
    """A notification as detection writes one, due when these suites decide."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO notifications (org_id, project_id, finding_id, channel_id, kind,
                                           sequence, severity, deliver_after, created_at)
                VALUES (:org_id, :project_id, :finding_id, :channel_id, :kind, :sequence,
                        'warning', :at, :at)
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "finding_id": finding_id,
                "channel_id": channel_id,
                "kind": kind,
                "sequence": sequence,
                "at": at,
            },
        )


async def follow_ups(engine: AsyncEngine, tenant: AlertTenant, **where: Any) -> list[Any]:
    clauses = "".join(f" AND {column} = :{column}" for column in where)
    async with engine.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT finding_id, channel_id, sequence, deliver_after FROM notifications "
                    f"WHERE org_id = :org_id AND kind = 'explained'{clauses} ORDER BY created_at"
                ),
                {"org_id": tenant.org_id, **where},
            )
        )


async def write(engine: AsyncEngine, tenant: AlertTenant, finding_id: uuid.UUID, **at: Any) -> int:
    async with engine.begin() as connection:
        return await explained.write(
            connection, org_id=tenant.org_id, finding_id=finding_id, now=at.get("now", SETTLED)
        )


# ------------------------------------------------------------- the decision, pure


@pytest.mark.parametrize(
    ("cause", "sends"),
    [
        (None, False),
        (LIKELY_SIGNED, True),
        ({**LIKELY_SIGNED, "strength": "possible", "rule": "signed_deploy_weak"}, False),
        ({**LIKELY_SIGNED, "basis": "telemetry", "rule": "dependency_finding"}, False),
        ({**LIKELY_SIGNED, "basis": "telemetry_text"}, False),
    ],
    ids=["no cause", "likely signed", "possible", "telemetry basis", "telemetry text"],
)
def test_only_a_likely_cause_on_a_verified_record_sends(
    cause: dict[str, Any] | None, sends: bool
) -> None:
    """AC25's cause table, with the override standing in for the founder's bar."""
    assert sends_for(cause, show_held_causes=True) is sends


def test_a_held_cause_sends_nothing() -> None:
    """FR50: until the bar is recorded and met, production sends no `explained`."""
    assert sends_for(LIKELY_SIGNED, show_held_causes=False) is False


@pytest.mark.parametrize(
    ("reason", "age", "due"),
    [
        ("opened", timedelta(minutes=10), False),
        ("settled", timedelta(minutes=5), True),
        ("deploy", timedelta(minutes=4, seconds=59), False),
        ("changed", timedelta(hours=1), True),
        ("person", timedelta(minutes=6), True),
    ],
)
def test_only_a_read_after_the_settled_time_counts(reason: str, age: timedelta, due: bool) -> None:
    """AC25: the `opened` read never sends; any read after settling may."""
    assert after_settling(reason, START, START + age) is due


def test_explained_never_tells_news() -> None:
    """AC28, FR22: closure is unchanged, because `explained` tells no news."""
    assert EXPLAINED not in FINDING_TOLD_KINDS


# ------------------------------------------------------------- the writer


async def test_only_told_people_read_channels_that_send_it_get_one(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC25, AC26, AC84's channel case: told email and owners get one; PagerDuty, a
    webhook, a channel with it off, and a channel never told, none."""
    finding_id = await open_finding(engine, acme)
    email = await channel(engine, acme, "email")
    pagerduty = await channel(engine, acme, "pagerduty")
    webhook = await channel(engine, acme, "webhook")
    off = await channel(engine, acme, "slack", sends_explained=False)
    untold = await channel(engine, acme, "slack")
    for told in (acme.owners_channel_id, email, pagerduty, webhook, off):
        await tell(engine, acme, finding_id, told)

    assert await write(engine, acme, finding_id) == 2

    sent_to = {row.channel_id for row in await follow_ups(engine, acme)}
    assert sent_to == {acme.owners_channel_id, email}
    assert not sent_to & {pagerduty, webhook, off, untold}


@pytest.mark.parametrize(
    "state",
    ["muted", "flapping", "closed", "quiet"],
)
async def test_a_muted_flapping_closed_or_quiet_finding_sends_none(
    engine: AsyncEngine, acme: AlertTenant, state: str
) -> None:
    """AC25's finding table."""
    changes: dict[str, Any] = {}
    if state in ("muted", "flapping"):
        changes[state] = True
    if state == "closed":
        changes["closed_at"] = START + timedelta(minutes=3)
    finding_id = await open_finding(engine, acme, **changes)
    if state == "quiet":
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO service_detection_settings
                        (org_id, project_id, service_name, snoozed_until)
                    VALUES (:org_id, :project_id, :service, :until)
                    """
                ),
                {
                    "org_id": acme.org_id,
                    "project_id": acme.project_id,
                    "service": SERVICE,
                    "until": SETTLED + timedelta(hours=1),
                },
            )
    await tell(engine, acme, finding_id, acme.owners_channel_id)

    assert await write(engine, acme, finding_id) == 0
    assert await follow_ups(engine, acme) == []


async def test_at_most_one_per_channel_at_any_sequence(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC25: a finding already told, a later qualifying read, and a channel whose only
    follow-up was its window's last (sequence 1) still leave one row."""
    finding_id = await open_finding(engine, acme)
    await tell(engine, acme, finding_id, acme.owners_channel_id)
    await tell(
        engine, acme, finding_id, acme.owners_channel_id, kind=EXPLAINED, sequence=LAST_SEQUENCE
    )

    assert await write(engine, acme, finding_id) == 0
    assert await write(engine, acme, finding_id, now=SETTLED + timedelta(hours=2)) == 0
    assert len(await follow_ups(engine, acme)) == 1


async def test_the_window_s_last_follow_up_says_so_and_then_none(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC25's cap: with 4 already in the window the fifth is written as the window's
    last; with 5, none."""
    findings = [await open_finding(engine, acme, service=f"svc-{i}") for i in range(6)]
    for finding_id in findings:
        await tell(engine, acme, finding_id, acme.owners_channel_id)
    for finding_id in findings[: PER_CHANNEL - 1]:
        await tell(engine, acme, finding_id, acme.owners_channel_id, kind=EXPLAINED, at=SETTLED)

    assert await write(engine, acme, findings[PER_CHANNEL - 1]) == 1
    assert await write(engine, acme, findings[PER_CHANNEL]) == 0

    fifth = await follow_ups(engine, acme, finding_id=findings[PER_CHANNEL - 1])
    assert [row.sequence for row in fifth] == [LAST_SEQUENCE]
    later = SETTLED + CAP_WINDOW + timedelta(seconds=1)
    assert await write(engine, acme, findings[PER_CHANNEL], now=later) == 1


@pytest.mark.parametrize("already", [PER_CHANNEL - 2, PER_CHANNEL - 1])
async def test_two_gathers_at_once_never_pass_the_cap(
    engine: AsyncEngine, acme: AlertTenant, already: int
) -> None:
    """AC25: two writers at a count of 3 or 4; the channel's lock keeps the window at 5."""
    earlier = [await open_finding(engine, acme, service=f"earlier-{i}") for i in range(already)]
    racing = [await open_finding(engine, acme, service=f"racing-{i}") for i in range(2)]
    for finding_id in (*earlier, *racing):
        await tell(engine, acme, finding_id, acme.owners_channel_id)
    for finding_id in earlier:
        await tell(engine, acme, finding_id, acme.owners_channel_id, kind=EXPLAINED, at=SETTLED)

    # The first writer holds its transaction open while the second starts: without the
    # channel's lock, the second would count the window before the first committed.
    async with engine.connect() as first:
        await first.begin()
        await explained.write(first, org_id=acme.org_id, finding_id=racing[0], now=SETTLED)
        second = asyncio.create_task(write(engine, acme, racing[1]))
        await asyncio.sleep(0.5)
        assert not second.done()
        await first.commit()
    await second

    rows = await follow_ups(engine, acme)
    assert len(rows) == min(already + 2, PER_CHANNEL)
    assert [row.sequence for row in rows].count(LAST_SEQUENCE) == 1


async def test_a_hundred_findings_on_one_channel_write_five_in_ten_minutes(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC25: a wide outage tells a channel at most five times in a window."""
    findings = [await open_finding(engine, acme, service=f"svc-{i}") for i in range(100)]
    for finding_id in findings:
        await tell(engine, acme, finding_id, acme.owners_channel_id)

    for index, finding_id in enumerate(findings):
        await write(engine, acme, finding_id, now=SETTLED + timedelta(seconds=5 * index))

    rows = await follow_ups(engine, acme)
    assert len(rows) == PER_CHANNEL


async def test_a_resolved_message_is_the_same_whether_or_not_one_was_sent(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC28: closing writes *resolved* to the channels told news, and no more."""
    told_only = await open_finding(engine, acme, service="told-only")
    followed_up = await open_finding(engine, acme, service="followed-up")
    for finding_id in (told_only, followed_up):
        await tell(engine, acme, finding_id, acme.owners_channel_id)
    await write(engine, acme, followed_up)

    resolved: dict[uuid.UUID, list[Any]] = {}
    for finding_id in (told_only, followed_up):
        async with engine.begin() as connection:
            await connection.execute(
                text(RESOLVE_FINDING_SQL),
                resolve_parameters(
                    org_id=acme.org_id,
                    finding_id=finding_id,
                    severity="warning",
                    at=SETTLED,
                    quiet=False,
                    skip=[],
                ),
            )
            resolved[finding_id] = list(
                await connection.execute(
                    text(
                        "SELECT channel_id, kind, sequence FROM notifications "
                        "WHERE org_id = :org_id AND finding_id = :id AND kind = 'resolved'"
                    ),
                    {"org_id": acme.org_id, "id": finding_id},
                )
            )

    assert resolved[told_only] == resolved[followed_up]
    assert [row.channel_id for row in resolved[followed_up]] == [acme.owners_channel_id]


# ------------------------------------------------------------- through the gather


async def signed_deploy(engine: AsyncEngine, tenant: AlertTenant, *, at: datetime) -> None:
    """A signed deploy with three weeks of records behind it, so its rule is `likely`."""
    async with engine.begin() as connection:
        release_id = await connection.scalar(
            text(
                """
                INSERT INTO releases (org_id, project_id, service_name, version, first_reported_at,
                                      last_reported_at, report_count, last_state, last_activity_at)
                VALUES (:org_id, :project_id, :service, 'v2.1', :first, :at, 1, 'finished', :at)
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "service": SERVICE,
                "first": at - timedelta(days=21),
                "at": at,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO deploy_events (org_id, project_id, version, service_name, deployed_at,
                                           source, delivery_id, metadata, state, release_id)
                VALUES (:org_id, :project_id, 'v2.1', :service, :at, 'ci', :delivery, '{}',
                        'finished', :release_id)
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "service": SERVICE,
                "at": at,
                "delivery": str(uuid.uuid4()),
                "release_id": release_id,
            },
        )


async def read(
    engine: AsyncEngine, tenant: AlertTenant, finding_id: uuid.UUID, reason: str, now: datetime
) -> Any:
    return await gather(
        engine,
        None,
        finding_id=finding_id,
        org_id=tenant.org_id,
        reason=reason,  # type: ignore[arg-type]
        deployment="self-hosted",
        now=now,
        show_held_causes=True,
    )


async def test_a_deploy_known_at_opening_sends_exactly_one_on_the_settled_read(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC25's headline case: the opening's read names the deploy and sends nothing; the
    settled read writes no version and sends one; a later read sends no second."""
    await signed_deploy(engine, acme, at=START - timedelta(minutes=4))
    finding_id = await open_finding(engine, acme)
    await tell(engine, acme, finding_id, acme.owners_channel_id)

    opened = await read(engine, acme, finding_id, "opened", START + timedelta(seconds=30))
    assert opened.likely_rule == "signed_deploy"
    assert await follow_ups(engine, acme) == []

    settled = await read(engine, acme, finding_id, "settled", SETTLED)
    again = await read(engine, acme, finding_id, "deploy", SETTLED + timedelta(minutes=2))

    assert settled.version is None and again.version is None
    rows = await follow_ups(engine, acme)
    assert [(row.finding_id, row.channel_id) for row in rows] == [
        (finding_id, acme.owners_channel_id)
    ]


async def test_a_stopped_rule_names_no_cause_and_sends_nothing_in_that_organization_only(
    engine: AsyncEngine, acme: AlertTenant
) -> None:
    """AC99 (plan T2.12): the stopped rule's facts stay, it names no cause there, and
    another organization's identical finding is unaffected."""
    other = await make_tenant(engine, with_check=False)
    try:
        findings = {}
        for tenant in (acme, other):
            await signed_deploy(engine, tenant, at=START - timedelta(minutes=4))
            findings[tenant.org_id] = await open_finding(engine, tenant)
            await tell(engine, tenant, findings[tenant.org_id], tenant.owners_channel_id)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO explanation_stopped_rules (org_id, rule) "
                    "VALUES (:org_id, 'signed_deploy')"
                ),
                {"org_id": acme.org_id},
            )

        stopped = await read(engine, acme, findings[acme.org_id], "settled", SETTLED)
        running = await read(engine, other, findings[other.org_id], "settled", SETTLED)

        assert stopped.likely_rule is None and running.likely_rule == "signed_deploy"
        assert await follow_ups(engine, acme) == []
        assert len(await follow_ups(engine, other)) == 1
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT evidence, likely_cause FROM finding_explanations "
                        "WHERE org_id = :org_id AND finding_id = :id"
                    ),
                    {"org_id": acme.org_id, "id": findings[acme.org_id]},
                )
            ).one()
        assert row.likely_cause is None
        assert any(
            item["kind"] == "deploy" and "role" not in item for item in row.evidence["items"]
        )
    finally:
        await drop_tenant(engine, other)


def test_postgres_is_reachable() -> None:
    assert postgres_reachable()
