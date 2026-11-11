"""When nexovue goes blind, the delivery sweep says so (plan T4.8; spec FR13b, D58).

AC91's logic: with judging stopped for a project's targets, `notices.report_blind` opens
one critical incident for the project, filed under no rule, sent to the union of its
`slo_burn` rules' channels or the owners, and one "judging again" when it returns. Two
sweeps, or two at once, open one row. A thin target never starts it. Every tier short of
coverage for 15 minutes starts it too. And deleting two rules with open burns on one
target still succeeds beside the new index.

Against real Postgres; fails rather than skips when it is down (tests/helpers/live).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from alert_fixtures import AlertTenant, add_rule, postgres_reachable, rows
from live import require_stack
from nexovue_platform import slo
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.slos import notices

require_stack(
    reachable=postgres_reachable(),
    suite="F19's blind-notice suite",
    remedy="start the dev stack with `just dev`",
)

MINUTE = timedelta(minutes=1)


async def _target(
    engine: AsyncEngine, tenant: AlertTenant, *, judged: datetime | None, **status: Any
) -> uuid.UUID:
    async with engine.begin() as connection:
        target = await connection.scalar(
            text(
                """
                INSERT INTO slo_targets (org_id, project_id, name, kind, service_name,
                    objective_percent, window_days, upstream_sampling, definition_key,
                    created_at)
                VALUES (:org_id, :project_id, :name, 'service_availability', 'checkout',
                    99.9, 28, 'no', :key, now() - interval '1 day')
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "name": f"checkout {uuid.uuid4()}",
                "key": str(uuid.uuid4()),
            },
        )
        if judged is not None:
            await connection.execute(
                text(
                    """
                    INSERT INTO slo_status (slo_id, org_id, project_id, evaluated_minute,
                                            unjudged_since)
                    VALUES (:id, :org_id, :project_id, :minute, :unjudged)
                    """
                ),
                {
                    "id": target,
                    "org_id": tenant.org_id,
                    "project_id": tenant.project_id,
                    "minute": judged,
                    "unjudged": status.get("unjudged_since"),
                },
            )
    assert isinstance(target, uuid.UUID)
    return target


async def _blind(engine: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    return await rows(
        engine,
        """
        SELECT id, rule_id, rule_name, severity, resolved_at, subject_key, detail
        FROM incidents WHERE org_id = :org_id AND subject_key LIKE 'slo-project:%'
        ORDER BY opened_at
        """,
        org_id=tenant.org_id,
    )


async def _told(engine: AsyncEngine, incident_id: uuid.UUID) -> list[tuple[str, uuid.UUID]]:
    found = await rows(
        engine,
        "SELECT kind, channel_id FROM notifications WHERE incident_id = :id ORDER BY created_at",
        id=incident_id,
    )
    return [(row.kind, row.channel_id) for row in found]


def _now() -> datetime:
    return datetime.now(UTC).replace(second=0, microsecond=0)


async def test_ac91_stale_status_opens_one_critical_notice_to_the_owners_and_resolves(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = _now()
    target = await _target(engine, tenant, judged=now - 16 * MINUTE)

    await notices.report_blind(engine, now=now)
    await notices.report_blind(engine, now=now + MINUTE)
    [opened] = await _blind(engine, tenant)
    assert opened.rule_id is None
    assert opened.rule_name == slo.BLIND_RULE_NAME
    assert opened.severity == "critical"
    assert opened.subject_key == f"slo-project:{tenant.project_id}:blind"
    assert await _told(engine, opened.id) == [("opened", tenant.owners_channel_id)]

    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE slo_status SET evaluated_minute = :now WHERE slo_id = :id"),
            {"now": now + 2 * MINUTE, "id": target},
        )
    await notices.report_blind(engine, now=now + 2 * MINUTE)
    [resolved] = await _blind(engine, tenant)
    assert resolved.resolved_at == now + 2 * MINUTE
    assert await _told(engine, opened.id) == [
        ("opened", tenant.owners_channel_id),
        ("resolved", tenant.owners_channel_id),
    ]


async def test_two_sweeps_at_once_open_one_notice(engine: AsyncEngine, tenant: AlertTenant) -> None:
    now = _now()
    await _target(engine, tenant, judged=now - 20 * MINUTE)
    await asyncio.gather(
        notices.report_blind(engine, now=now), notices.report_blind(engine, now=now)
    )
    [opened] = await _blind(engine, tenant)
    assert len(await _told(engine, opened.id)) == 1


async def test_it_goes_to_the_union_of_the_burn_rules_channels(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = _now()
    async with engine.begin() as connection:
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Targets",
            condition={"kind": "slo_burn"},
            channel_ids=[tenant.owners_channel_id],
        )
    await _target(engine, tenant, judged=now - 16 * MINUTE)
    await notices.report_blind(engine, now=now)
    [opened] = await _blind(engine, tenant)
    assert await _told(engine, opened.id) == [("opened", tenant.owners_channel_id)]


async def test_fresh_status_opens_nothing_and_a_thin_target_never_starts_it(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = _now()
    await _target(engine, tenant, judged=now - 2 * MINUTE)
    await notices.report_blind(engine, now=now)
    assert await _blind(engine, tenant) == []


async def test_every_tier_short_of_coverage_for_15_minutes_starts_it(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = _now()
    await _target(engine, tenant, judged=now - MINUTE, unjudged_since=now - 16 * MINUTE)
    await notices.report_blind(engine, now=now)
    [opened] = await _blind(engine, tenant)
    assert opened.detail["since"] == (now - 16 * MINUTE).isoformat()


async def test_deleting_two_rules_with_open_burns_on_one_target_still_succeeds(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """A rule's deletion sets `rule_id` null on its incidents; the index ignores them."""
    target = await _target(engine, tenant, judged=_now())
    async with engine.begin() as connection:
        for name in ("A", "B"):
            rule = await add_rule(
                connection,
                tenant.org_id,
                tenant.project_id,
                name=name,
                condition={"kind": "slo_burn"},
                channel_ids=[],
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO incidents (org_id, project_id, rule_id, rule_name,
                        condition_kind, subject_key, subject_label, severity, opened_at)
                    VALUES (:org_id, :project_id, :rule, :name, 'slo_burn', :key,
                        'checkout availability, 99.9% over 28 days', 'critical', now())
                    """
                ),
                {
                    "org_id": tenant.org_id,
                    "project_id": tenant.project_id,
                    "rule": rule,
                    "name": name,
                    "key": f"slo:{target}",
                },
            )
        await connection.execute(
            text("DELETE FROM alert_rules WHERE org_id = :org_id AND condition_kind = 'slo_burn'"),
            {"org_id": tenant.org_id},
        )
    left = await rows(
        engine,
        "SELECT count(*) AS n FROM incidents WHERE org_id = :org_id AND rule_id IS NULL",
        org_id=tenant.org_id,
    )
    assert left[0].n == 2
