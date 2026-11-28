"""A written suggestion, read through rung 3's route, against real Postgres (F5 plan T4.7,
T4.12; spec AC10, AC10a's read half, AC11, AC19's read half, AC55, AC56, AC57's view half).

Each test builds its own tenant inside a transaction rolled back afterwards. The stored
`edits` are made by the job's own pure code (`fixes.proposal.stored_edits`) from a file
the test holds, so a read must rebuild the same lines from display facts alone. **No
provider and no GitHub is reached:** the check route adds a job, with ids only.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import general_fix_rows as rows
import source_fix_rows as source
from general_fix_rows import POSTGRES_DSN, REAL_NAME, reachable
from live import require_stack
from nexovue_platform.fixes.checks import Edit, PackFile
from nexovue_platform.fixes.policy import CHECKS_VERSION
from nexovue_platform.fixes.proposal import stored_edits
from nexovue_platform.fixes.records import CHECK_FIX_TASK, FIX_CODE_VIEWED
from nexovue_platform.fixes.sentences import (
    OWNER_LIMITS_CODE,
    PROPOSED_VIEWER,
    REASON_SENTENCES,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    AuditLogEntry,
    FindingFix,
    FindingFixCheck,
    FixSourceConsent,
    GitHubUserLink,
)

db = rows.db
now = rows.now

require_stack(
    reachable=reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_proposal_routes_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

PATH: Final = f"{source.FOLDER}/app/handler.py"
CONTENT: Final = (
    "import json\n"
    "\n"
    "def charge(order, gateway):\n"
    "    amount = order.total\n"
    "    if amount < 0:\n"
    "        raise ValueError('negative')\n"
    "    return gateway.charge(amount)\n"
)
OLD: Final = "    if amount < 0:\n        raise ValueError('negative')\n"
NEW: Final = "    import requests\n"


def _edits() -> list[dict[str, Any]]:
    edit = Edit("file_1", OLD, NEW, "service_1 fails after version_1.", ("e1",))
    return list(stored_edits([edit], {"file_1": PackFile("file_1", PATH, CONTENT)}))


async def _proposed(db: AsyncSession, finding: Any, tenant: Any, **changes: Any) -> FindingFix:
    repository = await db.scalar(
        text("SELECT id FROM connection_repositories WHERE org_id = :org"),
        {"org": tenant.organization.id},
    )
    row = FindingFix(
        id=uuid.uuid4(),
        org_id=finding.org_id,
        project_id=finding.project_id,
        finding_id=finding.id,
        kind="source",
        explanation_version=1,
        state="proposed",
        state_changed_at=rows_now(),
        connection_repository_id=repository,
        folder=source.FOLDER,
        basis="likely_cause",
        files_read={
            "commit": "c" * 40,
            "commit_known": True,
            "files": [
                {
                    "ref": "file_1",
                    "path": PATH,
                    "blob_sha": "a" * 40,
                    "mode": "100644",
                    "role": "stack",
                    "why": {"kind": "stack", "line": 6},
                    "bytes": len(CONTENT),
                    "lines_sent": None,
                    "moved_on_default": False,
                }
            ],
            "left_out": [],
        },
        checks_version=CHECKS_VERSION,
        edits=_edits(),
        evidence_ids=["e1"],
        tokens_used=1234,
        requested_by=tenant.user.id,
        requested_at=rows_now(),
        finished_at=rows_now(),
    )
    for name, value in changes.items():
        setattr(row, name, value)
    db.add(row)
    await db.flush()
    return row


def rows_now() -> datetime:
    return datetime.now(UTC)


async def _read(db: AsyncSession, identity: Any, finding: Any) -> Any:
    async with source.client(db, identity) as http:
        response = await http.get(source.path(finding, "/fixes/source"))
    assert response.status_code == 200, response.text
    return response.json()["latest"]


async def test_ac19_a_requester_reads_the_suggestion_rebuilt_from_display_facts(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    await _proposed(db, finding, tenant)

    latest = await _read(db, rows.person(tenant), finding)

    proposal = latest["proposal"]
    assert latest["heading"] == "Suggested fix" and latest["code_withheld"] is None
    assert proposal["lead"].startswith("Suggested fix: 1 file, 3 lines, in services/shop.")
    [file] = proposal["files"]
    assert file["path"] == PATH and (file["added"], file["removed"]) == (1, 2)
    [hunk] = file["hunks"]
    assert hunk["header"] == "@@ -2,6 +2,5 @@"
    marked = [(line["marker"], line["old"], line["new"]) for line in hunk["lines"]]
    assert ("-", 5, None) in marked and ("+", None, 5) in marked
    added = next(line for line in hunk["lines"] if line["marker"] == "+")
    assert added["text"] == "    import requests" and "new import" in added["flags"]
    assert {flag["kind"] for flag in proposal["flags"]} >= {"new_import", "removed_check"}
    # AC56: stand-ins restored as name segments, never markup.
    segments = file["explanations"][0]["segments"]
    assert {"text": None, "name": REAL_NAME, "at": None} in segments
    assert proposal["files_read"][0]["github_url"].endswith(f"/blob/{'c' * 40}/{PATH}")
    assert proposal["tokens"] == 1234 and proposal["commit"] == "ccccccc"


async def test_ac57_a_person_s_first_read_of_code_is_audited_once(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(db, finding, tenant)

    await _read(db, rows.person(tenant), finding)
    await _read(db, rows.person(tenant), finding)

    views = (
        await db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.org_id == tenant.organization.id,
                AuditLogEntry.action == FIX_CODE_VIEWED,
            )
        )
    ).all()
    assert [view.detail for view in views] == [{"fix_id": str(row.id)}]


async def test_ac10_a_viewer_and_a_support_view_read_counts_and_no_code(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    await _proposed(db, finding, tenant)

    viewer = await _read(db, rows.person(tenant, "viewer"), finding)
    support = await _read(db, rows.support_view(tenant, now), finding)

    for other in (viewer, support):
        assert other["proposal"] is None and other["sentence"] == PROPOSED_VIEWER
        assert (other["file_count"], other["line_count"]) == (1, 3)
        assert "requests" not in json.dumps(other) and "handler" not in json.dumps(other)


async def test_fr21_another_checks_version_reads_out_of_date_with_no_code(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    await _proposed(db, finding, tenant, checks_version="2020-01-01.1")

    latest = await _read(db, rows.person(tenant), finding)

    assert latest["heading"] == "Out of date" and latest["proposal"] is None
    assert latest["sentence"] == REASON_SENTENCES["rules_changed"].requester


async def test_ac55_a_stored_edit_the_rules_refuse_at_read_shows_no_code(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    tampered = _edits()
    tampered[0]["new_text"] = "    token = 'ghp_" + "a1B2c3D4e5" * 4 + "'\n"
    await _proposed(db, finding, tenant, edits=tampered)

    latest = await _read(db, rows.person(tenant), finding)

    assert latest["proposal"] is None and latest["heading"] == "Not available"
    assert "ghp_" not in json.dumps(latest)


async def test_ac10a_under_the_owner_s_requirement_code_waits_for_this_person_s_own_check(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    consent = await db.scalar(
        select(FixSourceConsent).where(FixSourceConsent.org_id == tenant.organization.id)
    )
    assert consent is not None
    consent.require_github_access = True
    row = await _proposed(db, finding, tenant)

    withheld = await _read(db, rows.person(tenant), finding)
    assert withheld["proposal"] is None and withheld["code_withheld"] == OWNER_LIMITS_CODE
    assert withheld["check_action"] == "Check my access"

    db.add(
        FindingFixCheck(
            id=uuid.uuid4(),
            org_id=row.org_id,
            fix_id=row.id,
            requested_by=tenant.user.id,
            purpose="read",
            status="unchanged",
            requested_at=now,
            finished_at=now,
            expires_at=rows_now() + timedelta(hours=1),
        )
    )
    await db.flush()
    shown = await _read(db, rows.person(tenant), finding)
    assert shown["proposal"] is not None and shown["check"]["passed"] is True


async def test_t4_12_check_my_access_adds_one_job_and_a_repeat_returns_the_running_check(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    consent = await db.scalar(
        select(FixSourceConsent).where(FixSourceConsent.org_id == tenant.organization.id)
    )
    assert consent is not None
    consent.require_github_access = True
    row = await _proposed(db, finding, tenant)
    path = source.path(finding, f"/fixes/{row.id}/check")

    async with source.client(db, rows.person(tenant)) as http:
        unlinked = await http.post(path, json={"for": "read"})
    assert unlinked.status_code == 409 and unlinked.json()["code"] == "not_linked"

    db.add(
        GitHubUserLink(
            user_id=tenant.user.id,
            org_id=tenant.organization.id,
            github_user_id=77,
            github_login="reader",
        )
    )
    await db.flush()
    async with source.client(db, rows.person(tenant)) as http:
        first = await http.post(path, json={"for": "read"})
        again = await http.post(path, json={"for": "read"})

    assert first.status_code == 202 and first.json()["status"] == "running"
    assert again.json()["id"] == first.json()["id"]
    jobs = (
        await db.execute(
            text("SELECT task, kwargs FROM jobs WHERE org_id = :org"),
            {"org": tenant.organization.id},
        )
    ).all()
    assert [job.task for job in jobs] == [CHECK_FIX_TASK]
    assert set(jobs[0].kwargs) == {"check_id", "org_id"}


async def test_t4_12_with_no_owner_requirement_there_is_nothing_to_check(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(db, finding, tenant)

    async with source.client(db, rows.person(tenant)) as http:
        refused = await http.post(source.path(finding, f"/fixes/{row.id}/check"))

    assert refused.status_code == 409 and refused.json()["code"] == "not_required"
