"""A customer's code never leaks out of the pack and the stored suggestion (F5 plan T4.9; spec
NFR5).

A marker planted in a file the job sends goes to the provider, inside its fence, and
nowhere else Nexovue writes: not into a log line of the proposal job, the sweep or the
notifier, not into `ai_runs`, the audit log, the timeline or a job row. The same holds for
the person's hint. The rows are the test's own tenant's; logs are captured in-process.
"""

from __future__ import annotations

from datetime import datetime

import structlog
from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from propose_fix_fixtures import (
    PLANTED,
    a_proposing_fix,
    edits_answer,
    run_propose,
)
from recording_site import RecordingSite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import CHAT_PATH, connect, finding, openai_reply

from nexovue_scheduler.connections import fix_sweep
from nexovue_scheduler.fixes import notify

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_source_never_leaks_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

FILE_MARKER = "PLANTED_FILE_INSTRUCTION_MARKER"
HINT_MARKER = "PERSON_HINT_MARKER"


async def test_nfr5_a_file_s_text_and_the_hint_reach_the_provider_and_nothing_else(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    assert FILE_MARKER in PLANTED
    connected = await connect(ai_install, ai_tenant, provider_site, now=now)
    made = await a_proposing_fix(
        ai_install, ai_tenant, await finding(ai_install, ai_tenant, now=now), at=now
    )
    provider_site.replies[CHAT_PATH] = openai_reply(edits_answer())

    with structlog.testing.capture_logs() as logged:
        assert await run_propose(ai_engine, connected, made.fix_id, at=now) == "proposed"
        await fix_sweep.sweep(ai_install, clock=lambda: now, only_org=str(ai_tenant.org_id))
        await notify.notify(
            ai_install,
            sender=None,
            app_base_url="https://c.example",
            only_org=str(ai_tenant.org_id),
        )

    [sent] = provider_site.requests
    assert FILE_MARKER.encode() in sent.body and HINT_MARKER.encode() in sent.body
    assert logged, "the job logged nothing, so this proves nothing"
    for entry in logged:
        assert FILE_MARKER not in repr(entry) and HINT_MARKER not in repr(entry), entry
    async with ai_install.connect() as connection:
        for table in ("ai_runs", "audit_log", "finding_events", "jobs"):
            for marker in (FILE_MARKER, HINT_MARKER):
                found = await connection.scalar(
                    text(f"SELECT count(*) FROM {table} t WHERE t::text LIKE :marker"),
                    {"marker": f"%{marker}%"},
                )
                assert found == 0, (table, marker)
