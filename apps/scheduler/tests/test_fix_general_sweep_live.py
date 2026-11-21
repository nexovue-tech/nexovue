"""Rung 2b's sweep against real Postgres (F5 plan T2b.9; design, *Rung 2b* step 4; AC73's
no-notification half).

A row left `proposing` past any attempt's life ends `failed`, `timed_out`; a younger one is
left alone. Each ended row's result is recorded once: `fix_proposed` on the timeline for a
suggestion, `fix.withheld` with the rule alone for a rule's refusal, and nothing more for any
other end. No notification, email or outbox row is ever written for a general row.

The tenant is this test's own and dropped at the end; the clock is the test's own.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from alert_fixtures import AlertTenant, postgres_reachable
from general_fix_fixtures import a_general_fix
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import finding

from nexovue_scheduler.fixes import sweep

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_general_sweep_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

SUGGESTION = {
    "outcome": "suggestion",
    "abstain_reason": None,
    "summary": "s",
    "steps": ["one", "two"],
    "example": {"language": "python", "code": "x = 1\n"},
    "evidence_ids": ["e1"],
}


async def _ended(
    install: AsyncEngine,
    tenant: AlertTenant,
    at: datetime,
    *,
    state: str,
    reason: str | None = None,
    changed_at: datetime | None = None,
) -> str:
    fix = await a_general_fix(install, tenant, await finding(install, tenant, now=at), at=at)
    async with install.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE finding_fixes
                SET state = :state, reason = :reason, state_changed_at = :changed_at,
                    general_suggestion = CAST(:suggestion AS jsonb)
                WHERE id = :id
                """
            ),
            {
                "id": fix,
                "state": state,
                "reason": reason,
                "changed_at": changed_at or at,
                "suggestion": json.dumps(SUGGESTION) if state == "proposed" else None,
            },
        )
    return fix


async def _row(install: AsyncEngine, fix: str) -> Any:
    async with install.connect() as connection:
        return (
            await connection.execute(
                text("SELECT state, reason, result_recorded_at FROM finding_fixes WHERE id = :id"),
                {"id": fix},
            )
        ).one()


async def _events(install: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    async with install.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT kind, actor_kind, detail FROM finding_events "
                    "WHERE org_id = :org AND kind = 'fix_proposed'"
                ),
                {"org": tenant.org_id},
            )
        )


async def _audit(install: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    async with install.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT actor_user_id, detail FROM audit_log "
                    "WHERE org_id = :org AND action = 'fix.withheld'"
                ),
                {"org": tenant.org_id},
            )
        )


# The job's 15 minutes in the queue, F3's longest call of 300 s with 40 s to write, and the
# lease's 30 s grace (design, step 4: job expiry plus hard limit).
EVERY_ATTEMPT_OVER = timedelta(seconds=15 * 60 + 300 + 40 + 30)


async def test_a_row_stuck_past_every_attempt_s_life_ends_timed_out_and_a_young_one_stays(
    ai_install: AsyncEngine, ai_tenant: AlertTenant, now: datetime
) -> None:
    stuck = await a_general_fix(
        ai_install,
        ai_tenant,
        await finding(ai_install, ai_tenant, now=now),
        at=now - EVERY_ATTEMPT_OVER - timedelta(seconds=1),
    )
    young = await a_general_fix(
        ai_install,
        ai_tenant,
        await finding(ai_install, ai_tenant, now=now),
        at=now - EVERY_ATTEMPT_OVER + timedelta(seconds=1),
    )

    await sweep.sweep(ai_install, clock=lambda: now)

    assert (await _row(ai_install, stuck))[:2] == ("failed", "timed_out")
    assert (await _row(ai_install, young))[:2] == ("proposing", None)


async def test_a_suggestion_is_recorded_on_the_timeline_once_with_ids_and_counts(
    ai_install: AsyncEngine, ai_tenant: AlertTenant, now: datetime
) -> None:
    fix = await _ended(ai_install, ai_tenant, now, state="proposed")

    await sweep.sweep(ai_install, clock=lambda: now)
    await sweep.sweep(ai_install, clock=lambda: now + timedelta(minutes=1))

    [event] = await _events(ai_install, ai_tenant)
    assert (event.kind, event.actor_kind) == ("fix_proposed", "system")
    assert event.detail == {"fix_id": fix, "fix_kind": "general", "step_count": 2}
    assert (await _row(ai_install, fix)).result_recorded_at == now
    assert await _audit(ai_install, ai_tenant) == []


async def test_a_rule_s_refusal_is_audited_once_with_the_rule_alone(
    ai_install: AsyncEngine, ai_tenant: AlertTenant, now: datetime
) -> None:
    fix = await _ended(ai_install, ai_tenant, now, state="refused", reason="general_withheld_G4")

    await sweep.sweep(ai_install, clock=lambda: now)
    await sweep.sweep(ai_install, clock=lambda: now)

    [entry] = await _audit(ai_install, ai_tenant)
    assert entry.actor_user_id is None
    assert entry.detail == {"fix_id": fix, "rule": "G4"}
    assert await _events(ai_install, ai_tenant) == []


async def test_any_other_end_is_marked_recorded_and_writes_nothing_else(
    ai_install: AsyncEngine, ai_tenant: AlertTenant, now: datetime
) -> None:
    abstained = await _ended(
        ai_install, ai_tenant, now, state="refused", reason="evidence_too_thin"
    )
    failed = await _ended(ai_install, ai_tenant, now, state="failed", reason="provider_unavailable")

    await sweep.sweep(ai_install, clock=lambda: now)

    for fix in (abstained, failed):
        assert (await _row(ai_install, fix)).result_recorded_at == now
    assert await _events(ai_install, ai_tenant) == []
    assert await _audit(ai_install, ai_tenant) == []


async def test_ac73_no_general_row_ever_writes_a_notification_an_email_or_an_outbox_row(
    ai_install: AsyncEngine, ai_tenant: AlertTenant, now: datetime
) -> None:
    for state, reason in (("proposed", None), ("refused", "general_withheld_G5")):
        await _ended(ai_install, ai_tenant, now, state=state, reason=reason)

    await sweep.sweep(ai_install, clock=lambda: now)

    async with ai_install.connect() as connection:
        for table in ("notifications", "finding_emails_owed"):
            count = await connection.scalar(
                text(f"SELECT count(*) FROM {table} WHERE org_id = :org"),
                {"org": ai_tenant.org_id},
            )
            assert count == 0, table
