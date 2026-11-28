"""F5's audit entries for the draft, read back through the audit log's own read (F5 plan T7.9;
spec FR28a, AC11's audit half, AC31, AC57).

Every repository read names its person and purpose, the draft job's as `draft`; the click's
`fix.pull_request_attempted` names the person, the repository and the suggestion; a support
view reads that a repository was read and why, never which paths (AC11); no entry holds a
GitHub login (AC31). Rows are written by the same shared statement the jobs use
(`RECORD_CASE_AUDIT_SQL`), and the click's by its route with the gate forced on inside the
test. Each test builds its own tenant inside a rolled-back transaction.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Final

import general_fix_rows as rows
import pytest
import source_fix_rows as source
from general_fix_rows import POSTGRES_DSN, reachable
from live import require_stack
from nexovue_platform.cases import RECORD_CASE_AUDIT_SQL, case_audit_parameters
from nexovue_platform.detection import FINDING_AUDIT_TARGET
from nexovue_platform.fixes import write_gate
from nexovue_platform.fixes.records import (
    FIX_BRANCH_DELETED,
    FIX_PULL_REQUEST_ATTEMPTED,
    FIX_PULL_REQUEST_OPENED,
    READ_FOR_DRAFT,
    REPOSITORY_READ,
)
from nexovue_platform.fixes.write_gate import WriteGo
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import read
from nexovue_api.data.postgres.models import (
    Connection,
    FindingFix,
    FindingFixCheck,
    FixRepositorySettings,
    GitHubUserLink,
)

db = rows.db
now = rows.now

require_stack(
    reachable=reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_audit_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

TEST_GO: Final = WriteGo(
    given_on=date(2027, 1, 4),
    safety_gates_passed_on=("a test's own option",),
    buyers_answer="a test's own record",
    terms="a test's own terms",
    drafts_from_stack_alone=False,
    enterprise_server_before_step_7=False,
)
PATH: Final = f"{source.FOLDER}/app/pay.py"
LOGIN: Final = "ada-lovelace"


async def _job_entry(
    db: AsyncSession, tenant: Any, finding: Any, action: str, detail: dict[str, Any]
) -> None:
    """An entry as the scheduler's jobs write it, through the one shared statement."""
    await db.execute(
        text(RECORD_CASE_AUDIT_SQL),
        case_audit_parameters(
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            action=action,
            target=f"{FINDING_AUDIT_TARGET}:{finding.id}",
            detail=detail,
        ),
    )
    await db.flush()


async def _page(db: AsyncSession, tenant: Any, *, support_view: bool) -> dict[str, Any]:
    page = await read.read_page(
        db, org_id=tenant.organization.id, support_view=support_view, limit=50
    )
    return {entry.action: entry for entry in page.entries}


async def test_fr28a_the_draft_job_s_read_names_its_person_purpose_and_paths(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    fix_id = str(uuid.uuid4())
    await _job_entry(
        db,
        tenant,
        finding,
        REPOSITORY_READ,
        {
            "fix_id": fix_id,
            "purpose": READ_FOR_DRAFT,
            "paths": [{"path": PATH, "role": "stack"}],
        },
    )

    entry = (await _page(db, tenant, support_view=False))[REPOSITORY_READ]

    assert entry.actor.kind == "person" and entry.actor.id == tenant.user.id
    assert entry.detail == {
        "fix_id": fix_id,
        "purpose": "draft",
        "paths": [{"path": PATH, "role": "stack"}],
    }


async def test_ac11_a_support_view_reads_the_purpose_and_never_the_paths(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    fix_id = str(uuid.uuid4())
    await _job_entry(
        db,
        tenant,
        finding,
        REPOSITORY_READ,
        {"fix_id": fix_id, "purpose": "draft", "paths": [{"path": PATH, "role": "stack"}]},
    )
    await _job_entry(
        db,
        tenant,
        finding,
        FIX_BRANCH_DELETED,
        {"fix_id": fix_id, "branch": f"nexovue/suggestion-{fix_id}", "head": "a" * 40},
    )

    page = await _page(db, tenant, support_view=True)

    assert page[REPOSITORY_READ].detail == {"fix_id": fix_id, "purpose": "draft"}
    assert page[FIX_BRANCH_DELETED].detail is not None
    assert "paths" not in page[FIX_BRANCH_DELETED].detail


async def test_ac31_the_click_s_entry_names_the_person_and_holds_no_github_login(
    db: AsyncSession, now: datetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(write_gate, "WRITE_ACCESS_GO", TEST_GO)
    tenant, finding, repository = await source.ready(db, now)
    app = await db.get(Connection, repository.connection_id)
    assert app is not None
    app.granted_permissions = {"contents": "write", "pull_requests": "write"}
    db.add(
        FixRepositorySettings(
            org_id=tenant.organization.id,
            connection_repository_id=repository.id,
            drafts_allowed=True,
            set_at=now,
        )
    )
    db.add(
        GitHubUserLink(
            user_id=tenant.user.id,
            org_id=tenant.organization.id,
            github_user_id=9301,
            github_login=LOGIN,
        )
    )
    row = FindingFix(
        id=uuid.uuid4(),
        org_id=finding.org_id,
        project_id=finding.project_id,
        finding_id=finding.id,
        kind="source",
        state="proposed",
        state_changed_at=now,
        connection_repository_id=repository.id,
        folder=source.FOLDER,
        edits=[{"file_ref": "file_1"}],
        files_read={"files": []},
        requested_at=now,
        finished_at=now,
    )
    db.add(row)
    await db.flush()
    db.add(
        FindingFixCheck(
            id=uuid.uuid4(),
            org_id=row.org_id,
            fix_id=row.id,
            requested_by=tenant.user.id,
            purpose="draft",
            status="unchanged",
            digest="b" * 64,
            requested_at=now - timedelta(minutes=1),
            finished_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=14),
        )
    )
    await db.flush()

    async with source.client(db, rows.person(tenant)) as http:
        clicked = await http.post(
            source.path(finding, f"/fixes/{row.id}/draft-pull-request"), json={"digest": "b" * 64}
        )
    await _job_entry(
        db,
        tenant,
        finding,
        FIX_PULL_REQUEST_OPENED,
        {"fix_id": str(row.id), "pull_request_number": 7, "branch": f"nexovue/suggestion-{row.id}"},
    )

    assert clicked.status_code == 202, clicked.text
    page = await _page(db, tenant, support_view=False)
    attempted = page[FIX_PULL_REQUEST_ATTEMPTED]
    assert attempted.actor.id == tenant.user.id
    assert attempted.detail == {
        "fix_id": str(row.id),
        "repository_id": str(repository.id),
        "with_note": False,
    }
    for action in (FIX_PULL_REQUEST_ATTEMPTED, FIX_PULL_REQUEST_OPENED):
        assert LOGIN not in json.dumps(page[action].detail)
        assert "9301" not in json.dumps(page[action].detail)
