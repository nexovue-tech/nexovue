"""Prepare patch, the download, the verdict, and how an ended suggestion reads, through rung
3's routes against real Postgres (F5 plan T5.1's route half, T5.2; spec FR20a, FR21, FR29,
UR6, UR11, AC40, AC47's patch half, BR9's "Code deleted").

Each test builds its own tenant inside a transaction rolled back afterwards; the clock is
the suite's own `now`, taken once per test. A passed check is the job's own output, made
here by the same pure code (`recheck`), so the page must show the very patch served. **No
provider and no GitHub is reached:** the check route adds a job, with ids only.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Final

import general_fix_rows as rows
import source_fix_rows as source
from general_fix_rows import POSTGRES_DSN, reachable
from live import require_stack
from nexovue_platform.fixes.checks import Edit, PackFile
from nexovue_platform.fixes.diff import git_blob_id
from nexovue_platform.fixes.policy import CHECKS_VERSION
from nexovue_platform.fixes.proposal import stored_edits
from nexovue_platform.fixes.recheck import CurrentFile, Rechecked, recheck
from nexovue_platform.fixes.records import CHECK_FIX_TASK, FIX_PATCH_DOWNLOADED
from nexovue_platform.fixes.sentences import (
    CHECK_EXPIRED,
    MOVED_ON,
    PATCH_NOT_PREPARED,
    REASON_SENTENCES,
    VERDICT_RECORDED,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    AuditLogEntry,
    FindingFix,
    FindingFixCheck,
    FixSourceConsent,
)

db = rows.db
now = rows.now

require_stack(
    reachable=reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_patch_routes_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

PATH: Final = f"{source.FOLDER}/app/pay.py"
READ: Final = (
    "import json\n"
    "\n"
    "def charge(order, gateway):\n"
    "    amount = order.total\n"
    "    if amount < 0:\n"
    "        raise ValueError('negative')\n"
    "    return gateway.charge(amount)\n"
)
MOVED: Final = "# changed on the default branch since\n" + READ
OLD: Final = "    return gateway.charge(amount)\n"
NEW: Final = "    return gateway.charge(amount, retries=2)\n"
WHY: Final = "The call needs a retry count."


def _edits() -> list[dict[str, Any]]:
    edit = Edit("file_1", OLD, NEW, WHY, ("file_1",))
    return list(stored_edits([edit], {"file_1": PackFile("file_1", PATH, READ)}))


def _files_read() -> dict[str, Any]:
    return {
        "commit": "c" * 40,
        "commit_known": True,
        "files": [
            {
                "ref": "file_1",
                "path": PATH,
                "blob_sha": git_blob_id(READ),
                "mode": "100644",
                "role": "stack",
                "why": {"kind": "stack", "line": 7},
                "bytes": len(READ),
                "lines_sent": None,
                "moved_on_default": False,
            }
        ],
        "left_out": [],
    }


async def _proposed(
    db: AsyncSession, finding: Any, tenant: Any, now: datetime, **changes: Any
) -> FindingFix:
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
        state="proposed",
        state_changed_at=now,
        connection_repository_id=repository,
        folder=source.FOLDER,
        basis="stack_only",
        files_read=_files_read(),
        checks_version=CHECKS_VERSION,
        edits=_edits(),
        evidence_ids=["file_1"],
        tokens_used=900,
        requested_by=tenant.user.id,
        requested_at=now,
        finished_at=now,
    )
    for name, value in changes.items():
        setattr(row, name, value)
    db.add(row)
    await db.flush()
    return row


def _rechecked(head: str) -> Rechecked:
    outcome = recheck(
        _edits(),
        _files_read(),
        folder=source.FOLDER,
        allowed_evidence={"file_1"},
        aliases=None,
        head={"file_1": CurrentFile(git_blob_id(head), "100644", head)},
        base={"file_1": READ},
    )
    assert isinstance(outcome, Rechecked)
    return outcome


async def _patch_check(
    db: AsyncSession,
    row: FindingFix,
    user_id: uuid.UUID,
    now: datetime,
    *,
    head: str = READ,
    expires_in: timedelta = timedelta(minutes=15),
    **changes: Any,
) -> FindingFixCheck:
    built = _rechecked(head)
    check = FindingFixCheck(
        id=uuid.uuid4(),
        org_id=row.org_id,
        fix_id=row.id,
        requested_by=user_id,
        purpose="patch",
        status=built.status,
        head_sha="d" * 40,
        blob_ids=built.blob_ids,
        moved_refs=list(built.moved_refs),
        patch=built.patch,
        digest=built.digest,
        requested_at=now,
        finished_at=now,
        expires_at=now + expires_in,
    )
    for name, value in changes.items():
        setattr(check, name, value)
    db.add(check)
    await db.flush()
    return check


async def _read(db: AsyncSession, identity: Any, finding: Any) -> Any:
    async with source.client(db, identity) as http:
        response = await http.get(source.path(finding, "/fixes/source"))
    assert response.status_code == 200, response.text
    return response.json()["latest"]


async def _download(db: AsyncSession, identity: Any, finding: Any, row: FindingFix) -> Any:
    async with source.client(db, identity) as http:
        return await http.get(source.path(finding, f"/fixes/{row.id}/patch"))


async def _other_member(db: AsyncSession, tenant: Any) -> uuid.UUID:
    made = await db.scalar(
        text(
            "INSERT INTO users (org_id, email, name, role, password_hash) "
            "VALUES (:org, :email, 'Bea', 'member', 'x') RETURNING id"
        ),
        {"org": tenant.organization.id, "email": f"bea-{uuid.uuid4()}@example.test"},
    )
    assert isinstance(made, uuid.UUID)
    return made


# ------------------------------------------------------------------- Prepare patch


async def test_fr20a_prepare_patch_adds_one_job_and_a_repeat_returns_the_running_check(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(db, finding, tenant, now)
    path = source.path(finding, f"/fixes/{row.id}/check")

    async with source.client(db, rows.person(tenant)) as http:
        first = await http.post(path, json={"for": "patch"})
        again = await http.post(path, json={"for": "patch"})

    assert first.status_code == 202 and first.json()["purpose"] == "patch"
    assert again.json()["id"] == first.json()["id"]
    jobs = (
        await db.execute(
            text("SELECT task, kwargs FROM jobs WHERE org_id = :org"),
            {"org": tenant.organization.id},
        )
    ).all()
    assert [job.task for job in jobs] == [CHECK_FIX_TASK]
    assert set(jobs[0].kwargs) == {"check_id", "org_id"}
    latest = await _read(db, rows.person(tenant), finding)
    assert latest["patch"]["status"] == "running" and latest["patch_action"] is None


async def test_ac10a_under_the_owner_s_requirement_an_unlinked_person_is_sent_to_link_first(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    consent = await db.scalar(
        select(FixSourceConsent).where(FixSourceConsent.org_id == tenant.organization.id)
    )
    assert consent is not None
    consent.require_github_access = True
    row = await _proposed(db, finding, tenant, now)

    async with source.client(db, rows.person(tenant)) as http:
        refused = await http.post(
            source.path(finding, f"/fixes/{row.id}/check"), json={"for": "patch"}
        )

    assert refused.status_code == 409 and refused.json()["code"] == "not_linked"


async def test_a_suggestion_whose_code_is_deleted_cannot_be_prepared(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(
        db,
        finding,
        tenant,
        now,
        edits=None,
        files_read=None,
        code_deleted_at=now,
        code_deleted_reason="consent_withdrawn",
    )

    async with source.client(db, rows.person(tenant)) as http:
        refused = await http.post(
            source.path(finding, f"/fixes/{row.id}/check"), json={"for": "patch"}
        )

    assert refused.status_code == 409 and refused.json()["code"] == "not_proposed"


# ------------------------------------------------------------------- the download


async def test_ur6_the_download_is_the_person_s_own_patch_and_is_audited(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(db, finding, tenant, now)
    check = await _patch_check(db, row, tenant.user.id, now)

    response = await _download(db, rows.person(tenant), finding, row)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-patch")
    assert f"nexovue-suggestion-{row.id}.patch" in response.headers["content-disposition"]
    assert response.text == check.patch
    entries = (
        await db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.org_id == tenant.organization.id,
                AuditLogEntry.action == FIX_PATCH_DOWNLOADED,
            )
        )
    ).all()
    assert [entry.detail for entry in entries] == [
        {"fix_id": str(row.id), "check_id": str(check.id), "head_sha": "d" * 40, "paths": [PATH]}
    ]


async def test_ac40_another_person_s_passed_check_is_never_served(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(db, finding, tenant, now)
    await _patch_check(db, row, await _other_member(db, tenant), now)

    response = await _download(db, rows.person(tenant), finding, row)

    assert response.status_code == 409 and response.json()["code"] == "patch_not_prepared"
    assert response.json()["detail"] == PATCH_NOT_PREPARED


async def test_ac47_a_check_past_its_15_minutes_is_not_served(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(db, finding, tenant, now)
    await _patch_check(db, row, tenant.user.id, now, expires_in=timedelta(seconds=-1))

    response = await _download(db, rows.person(tenant), finding, row)

    assert response.status_code == 409 and response.json()["detail"] == CHECK_EXPIRED


async def test_a_support_view_and_a_viewer_are_refused_the_download(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(db, finding, tenant, now)
    await _patch_check(db, row, tenant.user.id, now)

    support = await _download(db, rows.support_view(tenant, now), finding, row)
    viewer = await _download(db, rows.person(tenant, "viewer"), finding, row)

    assert (support.status_code, viewer.status_code) == (403, 403)
    assert "gateway" not in support.text + viewer.text


# ------------------------------------------------------------- what the page shows


async def test_fr21_a_moved_check_replaces_the_diff_with_the_head_s_and_marks_the_file(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(db, finding, tenant, now)
    await _patch_check(db, row, tenant.user.id, now, head=MOVED)

    latest = await _read(db, rows.person(tenant), finding)

    [file] = latest["proposal"]["files"]
    assert file["moved_note"] == MOVED_ON
    [hunk] = file["hunks"]
    assert hunk["header"].startswith("@@ -5,")  # one line lower than as read
    assert latest["proposal"]["rebuilt_note"] is not None
    assert latest["patch"]["downloadable"] is True and latest["patch"]["heading"] == (
        "Ready to confirm"
    )


async def test_a_refused_patch_check_says_why_and_offers_it_again(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(db, finding, tenant, now)
    await _patch_check(
        db,
        row,
        tenant.user.id,
        now,
        status="refused",
        reason="no_github_access",
        patch=None,
        digest=None,
    )

    latest = await _read(db, rows.person(tenant), finding)

    assert latest["patch"]["downloadable"] is False
    assert latest["patch"]["sentence"] == REASON_SENTENCES["no_github_access"].requester
    assert latest["patch_action"] == "Prepare patch"


async def test_fr21_an_out_of_date_suggestion_shows_its_old_diff_and_no_action(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    await _proposed(db, finding, tenant, now, state="stale", reason="edit_no_longer_applies")

    requester = await _read(db, rows.person(tenant), finding)
    viewer = await _read(db, rows.person(tenant, "viewer"), finding)

    assert requester["heading"] == "Out of date"
    assert requester["sentence"] == REASON_SENTENCES["edit_no_longer_applies"].requester
    assert requester["proposal"]["files"][0]["path"] == PATH
    assert requester["patch_action"] is None
    assert viewer["sentence"] == "Out of date." and viewer["proposal"] is None


async def test_br9_a_suggestion_whose_code_went_says_when_why_and_until_when_backups_hold_it(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    deleted_at = now - timedelta(days=1)
    await _proposed(
        db,
        finding,
        tenant,
        now,
        edits=None,
        files_read=None,
        code_deleted_at=deleted_at,
        code_deleted_reason="consent_withdrawn",
    )

    for identity in (rows.person(tenant), rows.person(tenant, "viewer")):
        latest = await _read(db, identity, finding)
        assert latest["heading"] == "Code deleted" and latest["proposal"] is None
        assert deleted_at.date().isoformat() in latest["sentence"]
        assert "an owner withdrew agreement" in latest["sentence"]
        assert (deleted_at + timedelta(weeks=4)).date().isoformat() in latest["sentence"]


# ------------------------------------------------------------------- the verdict


async def test_fr29_a_verdict_changes_nothing_else(db: AsyncSession, now: datetime) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(db, finding, tenant, now)
    before = dict(
        (await db.execute(text("SELECT * FROM finding_fixes WHERE id = :id"), {"id": row.id}))
        .mappings()
        .one()
    )

    async with source.client(db, rows.person(tenant)) as http:
        answered = await http.post(
            source.path(finding, f"/fixes/{row.id}/verdict"), json={"verdict": "useful_start"}
        )

    assert answered.status_code == 200
    assert answered.json()["chosen"] == "useful_start"
    assert answered.json()["recorded"] == VERDICT_RECORDED
    after = dict(
        (await db.execute(text("SELECT * FROM finding_fixes WHERE id = :id"), {"id": row.id}))
        .mappings()
        .one()
    )
    assert after["verdict"] == "useful_start" and after["verdict_by"] == tenant.user.id
    changed = {key for key in after if after[key] != before[key]}
    assert changed == {"verdict", "verdict_by", "verdict_at"}
    latest = await _read(db, rows.person(tenant), finding)
    assert latest["verdict"]["chosen"] == "useful_start"


async def test_fr29_a_viewer_cannot_answer_and_sees_no_question(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(db, finding, tenant, now)

    async with source.client(db, rows.person(tenant, "viewer")) as http:
        refused = await http.post(
            source.path(finding, f"/fixes/{row.id}/verdict"), json={"verdict": "wrong"}
        )

    assert refused.status_code == 403
    assert (await _read(db, rows.person(tenant, "viewer"), finding))["verdict"] is None


async def test_fr29_a_suggestion_that_was_never_written_cannot_be_answered(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    row = await _proposed(
        db, finding, tenant, now, state="refused", reason="withheld_K6", edits=None
    )

    async with source.client(db, rows.person(tenant)) as http:
        refused = await http.post(
            source.path(finding, f"/fixes/{row.id}/verdict"), json={"verdict": "wrong"}
        )

    assert refused.status_code == 409 and refused.json()["code"] == "no_written_suggestion"


async def test_fr29_a_general_suggestion_is_answered_on_the_same_route(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding = await rows.ready(db, now)
    general = await rows.fix(db, finding, now)

    async with source.client(db, rows.person(tenant)) as http:
        answered = await http.post(
            source.path(finding, f"/fixes/{general.id}/verdict"),
            json={"verdict": "merge_as_is"},
        )
    shown = await rows.read(db, rows.person(tenant), finding)

    assert answered.status_code == 200
    assert shown["latest"]["verdict"]["chosen"] == "merge_as_is"
    assert "verdict" not in json.dumps(
        (await rows.read(db, rows.person(tenant, "viewer"), finding))["latest"]["verdict"] or {}
    )
