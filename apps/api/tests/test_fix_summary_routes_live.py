"""`GET /ai/fix-summary`, against real Postgres (F5 plan T9.3; spec UR12, AC39, AC11).

Owners and admins read it; members and viewers are refused (the panel's no-permission form).
Counts are true counts: an organization with nothing reads 0 everywhere, every verdict listed;
drafts opened and merged come from the pull request columns. A support view reads a notice's
rule and time but no paths (AC11). Each test's rows are its own, inside a transaction rolled
back at the end; the route reads the real clock, so rows are dated from the suite's `now`.

The notice itself, written by the AI account and recorded by the sweep, is walked end to end
in `apps/scheduler/tests/test_fix_summary_live.py`.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import general_fix_rows as rows
import pytest
import source_fix_rows as source
from general_fix_rows import POSTGRES_DSN
from live import require_stack
from nexovue_platform.detection import FINDING_AUDIT_TARGET
from nexovue_platform.fixes.notices import notice_sentence
from nexovue_platform.fixes.records import FIX_VERDICTS, FIX_WITHHELD, fix_branch
from nexovue_platform.fixes.sentences import VERDICT_LABELS
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import AuditLogEntry, FindingFix
from nexovue_api.findings.fix_summary import SUMMARY_EMPTY
from nexovue_api.settings import API_PREFIX

db = rows.db
now = rows.now

require_stack(
    reachable=rows.reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_summary_routes_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

SUMMARY = f"{API_PREFIX}/ai/fix-summary"


async def _get(db: AsyncSession, identity: Any) -> Any:
    async with rows.client(db, identity) as http:
        return await http.get(SUMMARY)


@pytest.mark.parametrize("role", ["member", "viewer"])
async def test_only_owners_and_admins_read_the_summary(db: AsyncSession, role: str) -> None:
    tenant = await rows.tenant(db)

    response = await _get(db, rows.person(tenant, role))

    assert response.status_code == 403


async def test_ac39_nothing_asked_for_reads_true_zeros_and_every_verdict(db: AsyncSession) -> None:
    tenant = await rows.tenant(db)

    response = await _get(db, rows.person(tenant, "admin"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["empty"] == SUMMARY_EMPTY
    assert (body["requested"], body["refused"], body["notices"]) == (0, [], [])
    assert (body["drafts_opened"], body["drafts_merged"], body["tokens"]) == (0, 0, 0)
    assert body["verdicts"] == [
        {"verdict": verdict, "label": VERDICT_LABELS[verdict], "count": 0}
        for verdict in FIX_VERDICTS
    ]


async def test_ac39_requests_tokens_verdicts_and_refusals_are_counted(
    db: AsyncSession, now: Any
) -> None:
    tenant, finding = await rows.ready(db, now)
    useful = await rows.fix(db, finding, now, minutes_ago=30)
    useful.verdict, useful.verdict_by, useful.verdict_at = (
        "useful_start",
        tenant.user.id,
        now - timedelta(minutes=10),
    )
    other = await rows.finding(db, tenant, now, service_name="payments")
    await rows.version(db, other, now)
    await rows.fix(
        db,
        other,
        now,
        state="refused",
        suggestion=None,
        reason="general_withheld_G4",
        minutes_ago=20,
    )
    await db.flush()

    body = (await _get(db, rows.person(tenant, "owner"))).json()

    assert body["requested"] == 2
    assert body["tokens"] == 2 * 460
    [refused] = body["refused"]
    assert (refused["reason"], refused["count"]) == ("general_withheld_G4", 1)
    assert {each["verdict"]: each["count"] for each in body["verdicts"]} == {
        "merge_as_is": 0,
        "useful_start": 1,
        "wrong": 0,
    }


async def test_ac11_a_support_view_reads_a_notice_without_its_paths(
    db: AsyncSession, now: Any
) -> None:
    tenant, finding = await rows.ready(db, now)
    fix_id = uuid.uuid4()
    db.add(
        AuditLogEntry(
            org_id=tenant.organization.id,
            actor_user_id=None,
            action=FIX_WITHHELD,
            target=f"{FINDING_AUDIT_TARGET}:{finding.id}",
            detail={"fix_id": str(fix_id), "rule": "K9", "files": ["services/shop/app.py"]},
        )
    )
    await db.flush()

    admin = (await _get(db, rows.person(tenant, "admin"))).json()
    support = (await _get(db, rows.support_view(tenant, now))).json()

    [notice] = admin["notices"]
    assert (notice["rule"], notice["sentence"]) == ("K9", notice_sentence("K9"))
    assert notice["files"] == ["services/shop/app.py"]
    assert notice["finding_id"] == str(finding.id) and notice["fix_id"] == str(fix_id)
    [hidden] = support["notices"]
    assert hidden["rule"] == "K9" and hidden["files"] is None


async def test_ac39_drafts_opened_and_merged_in_the_window_are_counted(
    db: AsyncSession, now: Any
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    for minutes, number, state, state_ago in (
        (60, 7, "merged", 30),
        (90, 8, "open", 90),
        (60 * 24 * 40, 9, "merged", 60 * 24 * 39),
    ):
        fix_id = uuid.uuid4()
        db.add(
            FindingFix(
                id=fix_id,
                org_id=finding.org_id,
                project_id=finding.project_id,
                finding_id=finding.id,
                kind="source",
                state="opened",
                state_changed_at=now - timedelta(minutes=minutes),
                connection_repository_id=repository.id,
                folder=source.FOLDER,
                requested_at=now - timedelta(minutes=minutes),
                approved_by=tenant.user.id,
                approved_at=now - timedelta(minutes=minutes),
                approved_digest="d" * 64,
                pull_request_number=number,
                pull_request_url=f"https://github.com/acme/shop/pull/{number}",
                pull_request_branch=fix_branch(fix_id),
                pull_request_state=state,
                pull_request_state_at=now - timedelta(minutes=state_ago),
            )
        )
        await db.flush()

    body = (await _get(db, rows.person(tenant, "admin"))).json()

    assert (body["drafts_opened"], body["drafts_merged"]) == (2, 1)
