"""Rung 2b leaves rungs 1 and 2, detection and alerts alone (F5 plan T2b.11, first version;
spec AC33's AI arms for this rung, NFR4's structural check).

**AC33's AI arms:** with a general suggestion stuck in flight because the provider fails or
`scheduler-insight` is stopped (its job is never claimed), the deploy line and the brief read
exactly as with no suggestion, and rung 2b's own read still answers. The import half is
`test_fix_facts_hold_no_ai.py`.

**NFR4, structurally:** F5's job runs only on `insight-fix`, which only `scheduler-insight`
claims and caps at 2 of its 8 slots, and its sweep on `light`; neither is detection's,
evidence's or deliveries' queue, so a slow provider cannot delay an alert, a probe or a
detection job. NFR4's measured bound comes with Step 3 (T3.14).

The live half's rows are inside a rolled-back transaction; it fails rather than skips when
Postgres is down.
"""

from __future__ import annotations

from datetime import datetime

import general_fix_rows as rows
from general_fix_rows import POSTGRES_DSN
from live import require_stack
from nexovue_platform.alerts.tasks import DELIVERIES_QUEUE
from nexovue_platform.detection import DETECTION_QUEUE
from nexovue_platform.evidence import EVIDENCE_QUEUE, INSIGHT_QUEUE
from nexovue_platform.fixes.records import FIX_CONCURRENCY, FIX_QUEUE, suggest_general_fix_request
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api import roles
from nexovue_api.settings import API_PREFIX

db = rows.db
now = rows.now

require_stack(
    reachable=rows.reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_detection_unaffected_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


async def test_ac33_a_suggestion_stuck_in_flight_leaves_the_deploy_line_and_brief_as_they_were(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding = await rows.ready(db, now)
    async with rows.client(db, rows.person(acme)) as http:
        before = (await http.get(f"{API_PREFIX}/findings/{finding.id}/fixes")).json()
    await rows.fix(db, finding, now, state="proposing", suggestion=None, minutes_ago=60)

    async with rows.client(db, rows.person(acme)) as http:
        during = await http.get(f"{API_PREFIX}/findings/{finding.id}/fixes")
        general = await http.get(f"{API_PREFIX}/findings/{finding.id}/fixes/general")

    assert during.status_code == 200 and during.json() == before
    assert general.status_code == 200 and general.json()["latest"]["state"] == "proposing"


def test_nfr4_f5_s_job_runs_only_on_its_own_capped_queue_in_the_ai_role() -> None:
    from nexovue_scheduler.__main__ import worker_queues
    from nexovue_scheduler.jobs.worker import QUEUE_CAPS

    job = suggest_general_fix_request(fix_id="f", org_id="o", requested_by="u", call_seconds=60)
    light = worker_queues("", region="local")
    insight = roles.command_for("scheduler-insight", [])

    assert job.queue == FIX_QUEUE and QUEUE_CAPS[FIX_QUEUE] == FIX_CONCURRENCY == 2
    assert FIX_QUEUE not in light
    assert FIX_QUEUE not in {DETECTION_QUEUE, EVIDENCE_QUEUE, DELIVERIES_QUEUE, INSIGHT_QUEUE}
    assert insight[insight.index("--queues") + 1].split(",") == ["assistant", "insight", FIX_QUEUE]
