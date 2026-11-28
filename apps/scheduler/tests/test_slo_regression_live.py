"""The regression gate for what a burn may withhold (plan T5.8; spec AC29, AC95).

The existing outbox and notify suites hold no burn, so they cannot show that the rule
leaves F6's and F21's path alone when one is open. This suite does:

- (a) an open burn on one service leaves a finding and a threshold incident on another
  service in the same project exactly as they are without it;
- (b) an open latency burn leaves an availability finding and an error-rate incident on
  its own service alone, because they are different news;
- (c) a withheld finding's later rows follow FR24 row by row;
- (e) `test_alert_outbox_live.py` and `test_detection_notify_live.py` run again here,
  unedited, against a tenant holding an unrelated open burn, so every claim they make
  runs the withholding statements. (AC95's (d) is `test_detection_coordinator.py`'s.)

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Final

import pytest
from alert_fixtures import (
    AlertTenant,
    add_rule,
    drop_tenant,
    future,
    make_tenant,
    rows,
)
from nexovue_platform.alerts.model import service_subject
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# (e): every test of both suites is collected here a second time, with `tenant` below.
from test_alert_outbox_live import *  # noqa: F403
from test_detection_notify_live import *  # noqa: F403
from test_slo_one_message_live import MINUTE, SAME_NEWS, World, people  # noqa: F401

from nexovue_scheduler.alerts import incidents, outbox
from nexovue_scheduler.alerts.rules import Rule
from nexovue_scheduler.detection import notify

UNRELATED_SERVICE: Final = "inventory"


@pytest.fixture
async def tenant(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    """The alert suites' tenant, holding an open burn on a service none of them uses,
    under a rule that sends nowhere, so it adds no row their assertions could see."""
    made = await make_tenant(engine)
    async with engine.begin() as connection:
        rule_id = await add_rule(
            connection,
            made.org_id,
            made.project_id,
            name="Unrelated reliability target",
            condition={"kind": "slo_burn", "slo_ids": None, "minimum_severity": "warning"},
            channel_ids=[],
        )
        rule = Rule(
            id=rule_id,
            org_id=made.org_id,
            project_id=made.project_id,
            name="Unrelated reliability target",
            condition_kind="slo_burn",
            condition={"kind": "slo_burn"},
            muted_until=None,
            channel_ids=(),
        )
        await incidents.fire(
            connection,
            rule,
            incidents.Subject(
                key=f"slo:{uuid.uuid4()}",
                label=f"{UNRELATED_SERVICE} availability, 99.9% over 28 days",
                about_key=service_subject(UNRELATED_SERVICE),
                about_signal="availability",
            ),
            severity="critical",
            at=datetime.now().astimezone(),
            detail={},
        )
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


async def test_the_unrelated_burn_is_open_beside_every_rerun_test(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    [burn] = await rows(
        engine,
        "SELECT resolved_at, about_key FROM incidents "
        "WHERE org_id = :org_id AND condition_kind = 'slo_burn'",
        org_id=tenant.org_id,
    )
    assert (burn.resolved_at, burn.about_key) == (None, f"service:{UNRELATED_SERVICE}")


async def test_ac95_a_a_burn_on_another_service_changes_nothing(
    people: World,  # noqa: F811
) -> None:
    at = future()
    await people.burn(at, service=UNRELATED_SERVICE)
    assert await people.deliver(at + MINUTE) == [["burn opened"]]

    await people.threshold(at + 2 * MINUTE)
    await people.finding(at + 2 * MINUTE)

    assert await people.deliver(at + 3 * MINUTE) == [["threshold opened", "finding opened"]]
    assert await people.withheld() == []


async def test_ac95_b_a_latency_burn_leaves_availability_news_on_its_service_alone(
    people: World,  # noqa: F811
) -> None:
    at = future()
    await people.burn(at, signal="latency")
    assert await people.deliver(at + MINUTE) == [["burn opened"]]

    await people.threshold(at + 2 * MINUTE)
    await people.finding(at + 2 * MINUTE, signals=("failing",))

    assert await people.deliver(at + 3 * MINUTE) == [["threshold opened", "finding opened"]]
    assert await people.withheld() == []


async def _flapping(world: World, finding: uuid.UUID, at: datetime) -> None:
    """The row the flap guard writes for a finding, on the world's channel."""
    async with world.engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO notifications (org_id, project_id, finding_id, channel_id, kind,
                    sequence, severity, deliver_after, created_at)
                VALUES (:org_id, :project_id, :finding_id, :channel_id, 'flapping', 0,
                    'critical', :at, :at)
                """
            ),
            {
                "org_id": world.tenant.org_id,
                "project_id": world.tenant.project_id,
                "finding_id": finding,
                "channel_id": world.channel,
                "at": at,
            },
        )


async def _finding_rows(world: World) -> list[tuple[str, str, str | None]]:
    found = await rows(
        world.engine,
        "SELECT kind, state, withheld_why FROM notifications "
        "WHERE org_id = :org_id AND finding_id IS NOT NULL ORDER BY created_at, id",
        org_id=world.tenant.org_id,
    )
    return [(row.kind, row.state, row.withheld_why) for row in found]


@pytest.mark.parametrize("burn_severity", ["critical", "warning"])
async def test_ac95_c_a_withheld_finding_s_later_rows_follow_fr24_row_by_row(
    people: World,  # noqa: F811
    burn_severity: str,
) -> None:
    """Under a critical burn every news row is the same news at no higher severity, so
    each is withheld, and so is the ending of news that never arrived. Under a warning
    burn the escalation goes, and so does everything after it, ending included."""
    at = future()
    await people.burn(at, severity=burn_severity)
    await people.deliver(at + MINUTE)
    finding = await people.finding(at + 2 * MINUTE, severity="warning")
    await people.deliver(at + 3 * MINUTE)
    async with people.engine.begin() as connection:
        await notify.finding_joined(
            connection,
            finding,
            org_id=people.tenant.org_id,
            severity="warning",
            keys=2,
            at=at + 4 * MINUTE,
        )
    await people.deliver(at + 5 * MINUTE)
    async with people.engine.begin() as connection:
        await notify.finding_escalated(
            connection, finding, org_id=people.tenant.org_id, at=at + 6 * MINUTE
        )
    await people.deliver(at + 7 * MINUTE)
    await _flapping(people, finding, at + 8 * MINUTE)
    await people.deliver(at + 9 * MINUTE)
    await people.close_finding(finding, at + 10 * MINUTE)
    await people.deliver(at + 11 * MINUTE)

    if burn_severity == "critical":
        expected = [
            ("opened", "dropped", "same_news"),
            ("joined", "dropped", "same_news"),
            ("escalated", "dropped", "same_news"),
            ("flapping", "dropped", "same_news"),
            ("resolved", "dropped", None),
        ]
    else:
        expected = [
            ("opened", "dropped", "same_news"),
            ("joined", "dropped", "same_news"),
            ("escalated", "delivered", None),
            ("flapping", "delivered", None),
            ("resolved", "delivered", None),
        ]
    assert await _finding_rows(people) == expected
    assert SAME_NEWS[1] == outbox.SAME_NEWS_REASON
