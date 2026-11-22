"""The admin summary of suggested fixes, end to end through the real AI account (F5 plan T9.3;
spec UR12, AC39; design, *The summary*).

Each refusal is written by the proposal job as `nexovue_insight`, with a fake provider's answer
that trips one of Nexovue's rules, then swept as the install account, as beat runs it, twice.
The summary the API serves is then read for this organization: a notice for K7, none for K6
(held until the founder has read T6.3's report, decision 89), each refusal counted once by its
reason, and no suggestion's text anywhere. Each sweep is narrowed to this test's organization
and runs on its clock.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from nexovue_platform.fixes.notices import notice_sentence
from propose_fix_fixtures import PATH, a_proposing_fix, edit, edits_answer, run_propose
from recording_site import RecordingSite
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from wording_fixtures import CHAT_PATH, connect, finding, openai_reply

from nexovue_api.findings import fix_summary
from nexovue_scheduler.connections import fix_sweep

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_summary_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

HIDDEN = "​"
HIDDEN_LINE = f"    amount = round(total(order.items), 2){HIDDEN}\n"
NEW_ADDRESS_LINE = "    call('https://evil.example/pay')\n"


async def _refused(
    install: AsyncEngine,
    engine: AsyncEngine,
    site: RecordingSite,
    tenant: AlertTenant,
    at: datetime,
    answer: dict[str, Any],
) -> str:
    """One finding and one refused suggestion: the fixtures give a tenant one Fix App, so each
    test writes one refusal."""
    connected = await connect(install, tenant, site, now=at)
    made = await a_proposing_fix(install, tenant, await finding(install, tenant, now=at), at=at)
    site.replies[CHAT_PATH] = openai_reply(answer)
    await run_propose(engine, connected, made.fix_id, at=at)
    return made.fix_id


async def _summary(install: AsyncEngine, tenant: AlertTenant, at: datetime) -> Any:
    async with AsyncSession(install) as session:
        return await fix_summary.summary(session, org_id=uuid.UUID(str(tenant.org_id)), now=at)


async def test_ac39_a_k7_refusal_written_by_the_ai_account_shows_one_notice_without_text(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    hidden = await _refused(
        ai_install,
        ai_engine,
        provider_site,
        ai_tenant,
        now,
        edits_answer(edit(new_text=HIDDEN_LINE)),
    )

    for _ in range(2):
        await fix_sweep.sweep(ai_install, clock=lambda: now, only_org=str(ai_tenant.org_id))
    read = await _summary(ai_install, ai_tenant, now + timedelta(minutes=1))

    assert read.requested == 1
    assert [(each.reason, each.count) for each in read.refused] == [("withheld_K7", 1)]
    [notice] = read.notices
    assert (notice.rule, notice.files, notice.sentence) == ("K7", [PATH], notice_sentence("K7"))
    assert notice.fix_id == uuid.UUID(hidden)
    shown = json.dumps(read.model_dump(mode="json"))
    assert HIDDEN not in shown and "round(total" not in shown


async def test_ac39_decision_89_a_k6_refusal_is_counted_and_shows_no_notice_until_read(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    await _refused(
        ai_install,
        ai_engine,
        provider_site,
        ai_tenant,
        now,
        edits_answer(edit(new_text=NEW_ADDRESS_LINE)),
    )

    await fix_sweep.sweep(ai_install, clock=lambda: now, only_org=str(ai_tenant.org_id))
    read = await _summary(ai_install, ai_tenant, now + timedelta(minutes=1))

    assert [(each.reason, each.count) for each in read.refused] == [("withheld_K6", 1)]
    assert read.notices == []
    assert "evil.example" not in json.dumps(read.model_dump(mode="json"))


async def test_ac39_counts_older_than_30_days_are_left_out(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    await _refused(
        ai_install,
        ai_engine,
        provider_site,
        ai_tenant,
        now,
        edits_answer(edit(new_text=HIDDEN_LINE)),
    )
    await fix_sweep.sweep(ai_install, clock=lambda: now, only_org=str(ai_tenant.org_id))

    read = await _summary(ai_install, ai_tenant, now + timedelta(days=31))

    assert (read.requested, read.refused, read.notices, read.tokens) == (0, [], [], 0)
