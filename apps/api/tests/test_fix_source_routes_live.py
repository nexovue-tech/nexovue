"""Rung 3's routes, against real Postgres (F5 plan T3.7, T3.9, T3.11, T3.16; spec AC3,
AC3a, AC3b, AC8's ordering through the route, AC10, AC12d, AC15, AC16, AC16b, AC46).

Each test builds its own tenant inside a transaction rolled back afterwards; ClickHouse is
replaced by `source_fix_rows.Stacks`, which answers as the service-bound group read does.
**No provider and no GitHub is reached:** Ask and Files add a pack job with ids only, and
Send adds the AI role's job with ids only (Step 4, T4.14).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

import general_fix_rows as rows
import pytest
import source_fix_rows as source
from general_fix_rows import POSTGRES_DSN, reachable
from live import require_stack
from nexovue_platform.fixes.records import (
    BUILD_SOURCE_PACK_TASK,
    FIX_QUEUE,
    FIX_SENT,
    PROPOSE_FIX_TASK,
    SUGGEST_GENERAL_FIX_TASK,
)
from nexovue_platform.fixes.sentences import (
    ELIGIBILITY_SENTENCES,
    REASON_SENTENCES,
    TYPED_REFUSED,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    AuditLogEntry,
    FindingFix,
    FindingFixSourcePack,
    GitHubUserLink,
)

db = rows.db
now = rows.now

require_stack(
    reachable=reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_source_routes_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


async def _jobs(db: AsyncSession, org_id: uuid.UUID) -> list[Any]:
    return list(
        (
            await db.execute(
                text("SELECT task, kwargs, queue FROM jobs WHERE org_id = :org"), {"org": org_id}
            )
        ).all()
    )


async def _ask(
    db: AsyncSession, identity: Any, finding: Any, stacks: Any = None, **body: Any
) -> Any:
    async with source.client(db, identity, stacks) as http:
        return await http.post(source.path(finding, "/fixes"), json=body)


async def _read(db: AsyncSession, identity: Any, finding: Any) -> Any:
    async with source.client(db, identity) as http:
        response = await http.get(source.path(finding, "/fixes/source"))
    assert response.status_code == 200, response.text
    return response.json()


async def test_ready_offers_ask_with_its_label_and_tries(db: AsyncSession, now: datetime) -> None:
    tenant, finding, _ = await source.ready(db, now)

    body = await _read(db, rows.person(tenant), finding)

    assert body["offer"]["result"] == "ready"
    assert body["offer"]["tries_left"] == 3 and body["latest"] is None


async def test_ac15_ask_stores_candidates_from_this_service_and_adds_a_job_with_ids_only(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    stacks = source.Stacks()

    response = await _ask(db, rows.person(tenant), finding, stacks)

    assert response.status_code == 202, response.text
    row = await db.scalar(select(FindingFix).where(FindingFix.finding_id == finding.id))
    assert row is not None and row.kind == "source" and row.state == "gathering"
    assert row.path_candidates == [
        {"path": "/srv/app/services/shop/app/handler.py", "line": 41, "from": "stack"}
    ]
    assert (row.connection_repository_id, row.folder) == (repository.id, source.FOLDER)
    [job] = await _jobs(db, tenant.organization.id)
    assert job.task == BUILD_SOURCE_PACK_TASK and job.queue == "connections-interactive"
    assert job.kwargs == {"fix_id": str(row.id), "org_id": str(tenant.organization.id)}
    assert all(each.get("service_name") in (None, "checkout") for each in stacks.asked)


async def test_ac16_no_message_text_reaches_any_column(db: AsyncSession, now: datetime) -> None:
    tenant, finding, _ = await source.ready(db, now)

    await _ask(db, rows.person(tenant), finding)

    stored = await db.scalar(
        text("SELECT finding_fixes::text FROM finding_fixes WHERE finding_id = :id"),
        {"id": finding.id},
    )
    assert "ignore previous" not in stored and "etc/passwd" not in stored


@pytest.mark.parametrize(
    ("stacks", "reason"),
    [
        (source.Stacks(group_found=False), "stack_aged_out"),
        (source.Stacks(stacks=("TimeoutError: no frames here\n",)), "no_stack"),
    ],
)
async def test_ac16b_an_aged_out_or_empty_stack_reaches_its_reason_and_sentence(
    db: AsyncSession, now: datetime, stacks: Any, reason: str
) -> None:
    tenant, finding, _ = await source.ready(db, now)

    await _ask(db, rows.person(tenant), finding, stacks)
    body = await _read(db, rows.person(tenant), finding)

    assert body["latest"]["state"] == "refused" and body["latest"]["reason"] == reason
    assert body["latest"]["sentence"] == REASON_SENTENCES[reason].requester
    assert await _jobs(db, tenant.organization.id) == []


async def test_ac46_a_typed_path_holding_dot_dot_is_refused_beside_it(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)

    response = await _ask(db, rows.person(tenant), finding, paths=["app/../../secrets.py"])

    assert response.status_code == 422
    assert response.json()["detail"] == TYPED_REFUSED.format(path="app/../../secrets.py")


async def test_typed_paths_are_marked_as_the_person_s(db: AsyncSession, now: datetime) -> None:
    tenant, finding, _ = await source.ready(db, now)

    await _ask(db, rows.person(tenant), finding, paths=["app/handler.py"])

    row = await db.scalar(select(FindingFix).where(FindingFix.finding_id == finding.id))
    assert row is not None
    assert (row.candidates_from, row.basis) == ("person", "named_files")


async def _gathered(db: AsyncSession, finding: Any, tenant: Any, **listing: Any) -> FindingFix:
    row = await db.scalar(select(FindingFix).where(FindingFix.finding_id == finding.id))
    assert row is not None
    row.state, row.path_candidates = "gathered", None
    row.files_read = {
        "commit": "c" * 40,
        "commit_known": False,
        "files": [
            {
                "ref": "file_1",
                "path": f"{source.FOLDER}/app/handler.py",
                "blob_sha": "a" * 40,
                "mode": "100644",
                "role": "stack",
                "why": {"kind": "stack", "line": 41},
                "bytes": 120,
                "lines_sent": None,
                "moved_on_default": False,
            },
            {
                "ref": "file_2",
                "path": f"{source.FOLDER}/app/money.py",
                "blob_sha": "b" * 40,
                "mode": "100644",
                "role": "imported",
                "why": {"kind": "imported_by", "path": f"{source.FOLDER}/app/handler.py"},
                "bytes": 80,
                "lines_sent": None,
                "moved_on_default": False,
            },
        ],
        "left_out": [],
        "unmatched": 1,
        "typed_missing": [],
        **listing,
    }
    row.files_digest = "d" * 64
    db.add(
        FindingFixSourcePack(
            fix_id=row.id,
            org_id=row.org_id,
            files=[
                {"ref": "file_1", "path": f"{source.FOLDER}/app/handler.py", "content": "x\n"},
                {"ref": "file_2", "path": f"{source.FOLDER}/app/money.py", "content": "y\n"},
            ],
            frames=[{"ref": "file_1", "line": 41}],
            bytes=4,
            expires_at=row.state_changed_at + timedelta(minutes=20),
        )
    )
    await db.flush()
    return row


async def test_a_requester_reads_the_list_and_a_viewer_reads_only_a_state(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    await _ask(db, rows.person(tenant), finding)
    await _gathered(db, finding, tenant)

    mine = await _read(db, rows.person(tenant), finding)
    viewer = await _read(db, rows.person(tenant, "viewer"), finding)
    support = await _read(db, rows.support_view(tenant, now), finding)

    files = mine["latest"]["files"]
    assert [each["why"] for each in files["files"]] == [
        "named at line 41 of the error's stack",
        f"imported by {source.FOLDER}/app/handler.py",
    ]
    assert files["files"][0]["github_url"].startswith("https://github.com/acme/shop/blob/")
    assert files["unmatched"] == 1
    for other in (viewer, support):
        assert other["latest"]["files"] is None and other["offer"] is None
        assert "handler" not in json.dumps(other)
    assert support["support_view_note"]


async def test_ac3b_send_with_an_old_digest_is_409_with_the_new_list(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    await _ask(db, rows.person(tenant), finding)
    row = await _gathered(db, finding, tenant)

    async with source.client(db, rows.person(tenant)) as http:
        stale = await http.post(
            source.path(finding, f"/fixes/{row.id}/send"), json={"files_digest": "e" * 64}
        )
        fresh = await http.post(
            source.path(finding, f"/fixes/{row.id}/send"), json={"files_digest": "d" * 64}
        )

    assert stale.status_code == 409 and stale.json()["code"] == "list_changed"
    assert stale.json()["fix"]["latest"]["files"]["digest"] == "d" * 64
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["latest"]["state"] == "proposing"


async def test_t4_14_send_adds_one_ai_job_with_ids_only_and_audits_what_goes_where(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    await _ask(db, rows.person(tenant), finding)
    row = await _gathered(db, finding, tenant)

    async with source.client(db, rows.person(tenant)) as http:
        sent = await http.post(
            source.path(finding, f"/fixes/{row.id}/send"),
            json={"files_digest": "d" * 64, "hint": "  look at the retry  "},
        )

    assert sent.status_code == 200, sent.text
    assert sent.json()["latest"]["heading"] == "Writing a suggestion"
    jobs = [job for job in await _jobs(db, tenant.organization.id) if job.queue == FIX_QUEUE]
    assert [job.task for job in jobs] == [PROPOSE_FIX_TASK]
    assert set(jobs[0].kwargs) == {"fix_id", "org_id", "sent_by"}
    await db.refresh(row)
    assert (row.state, row.sent_by, row.retry_code) == ("proposing", tenant.user.id, None)
    pack = await db.get(FindingFixSourcePack, row.id)
    assert pack is not None and pack.hint == "look at the retry"
    [entry] = (
        await db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.org_id == tenant.organization.id, AuditLogEntry.action == FIX_SENT
            )
        )
    ).all()
    assert entry.detail is not None
    assert entry.detail["files"] == [
        {"path": f"{source.FOLDER}/app/handler.py", "blob_sha": "a" * 40},
        {"path": f"{source.FOLDER}/app/money.py", "blob_sha": "b" * 40},
    ]
    assert entry.detail["provider_host"] and entry.detail["round"] == 1
    assert "look at the retry" not in json.dumps(entry.detail)


async def test_t4_14_send_refuses_with_ai_off_and_with_the_tries_used(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    await _ask(db, rows.person(tenant), finding)
    row = await _gathered(db, finding, tenant)
    for _ in range(3):
        db.add(
            FindingFix(
                id=uuid.uuid4(),
                org_id=row.org_id,
                project_id=row.project_id,
                finding_id=row.finding_id,
                kind="source",
                state="proposed",
                state_changed_at=now,
                requested_at=now - timedelta(minutes=5),
                sent_at=now - timedelta(minutes=5),
            )
        )
    await db.flush()

    async with source.client(db, rows.person(tenant)) as http:
        spent = await http.post(
            source.path(finding, f"/fixes/{row.id}/send"), json={"files_digest": "d" * 64}
        )

    assert spent.status_code == 409 and spent.json()["code"] == "limit_reached"
    await db.refresh(row)
    assert row.state == "gathered"


async def test_ac3a_removing_a_file_reads_again_with_no_ai_job(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    await _ask(db, rows.person(tenant), finding)
    row = await _gathered(db, finding, tenant)

    async with source.client(db, rows.person(tenant)) as http:
        removed = await http.post(
            source.path(finding, f"/fixes/{row.id}/files"),
            json={"remove": [f"{source.FOLDER}/app/money.py"]},
        )

    assert removed.status_code == 202
    tasks = [job.task for job in await _jobs(db, tenant.organization.id)]
    assert SUGGEST_GENERAL_FIX_TASK not in tasks and PROPOSE_FIX_TASK not in tasks
    await db.refresh(row)
    assert row.state == "gathering" and row.path_candidates is not None
    assert {"path": "app/money.py", "line": None, "from": "removed"} in row.path_candidates
    assert all(
        each.get("path") != "app/money.py" or each["from"] == "removed"
        for each in row.path_candidates
    )


async def test_ac12f_a_left_out_top_file_needs_confirmation_at_send(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    await _ask(db, rows.person(tenant), finding)
    row = await _gathered(db, finding, tenant, top_left_out=True)

    async with source.client(db, rows.person(tenant)) as http:
        response = await http.post(
            source.path(finding, f"/fixes/{row.id}/send"), json={"files_digest": "d" * 64}
        )

    assert response.status_code == 409 and response.json()["code"] == "confirm_top_left_out"


async def test_ac46_cancel_reads_as_a_choice_with_its_pack_gone(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    await _ask(db, rows.person(tenant), finding)
    row = await _gathered(db, finding, tenant)

    async with source.client(db, rows.person(tenant)) as http:
        await http.post(source.path(finding, f"/fixes/{row.id}/cancel"))
    body = await _read(db, rows.person(tenant), finding)

    assert body["latest"]["neutral"] is True and body["latest"]["reason"] == "cancelled"
    assert body["latest"]["sentence"] == "You cancelled this. Nothing was sent."
    assert body["offer"]["result"] == "ready"


@pytest.mark.parametrize("route", ["/fixes", "/brief/files"])
async def test_ac10_a_viewer_is_refused_on_each_write(
    db: AsyncSession, now: datetime, route: str
) -> None:
    tenant, finding, _ = await source.ready(db, now)

    async with source.client(db, rows.person(tenant, "viewer")) as http:
        response = await http.post(source.path(finding, route), json={})

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("label", "arrange", "reason"),
    [
        ("no code location", "location", "no_code_location"),
        ("no read permission", "permission", "no_read_permission"),
        ("no consent", "consent", "no_consent"),
    ],
)
async def test_ac8_each_code_access_reason_stops_ask_and_shows_rung_2b_instead(
    db: AsyncSession, now: datetime, label: str, arrange: str, reason: str
) -> None:
    tenant, finding, _repository = await source.ready(db, now)
    table = {
        "location": "DELETE FROM service_code_locations WHERE org_id = :org",
        "permission": "UPDATE connections SET granted_permissions = NULL WHERE org_id = :org",
        "consent": "DELETE FROM fix_source_consents WHERE org_id = :org",
    }[arrange]
    await db.execute(text(table), {"org": tenant.organization.id})

    response = await _ask(db, rows.person(tenant), finding)
    general = await rows.read(db, rows.person(tenant), finding)

    assert response.status_code == 409 and response.json()["code"] == reason, label
    assert general["offer"] is not None
    assert general["line"]["sentence"].startswith(
        ELIGIBILITY_SENTENCES[reason].requester.split("{")[0][:30]
    )


async def test_d67_where_rung_3_is_ready_rung_2b_is_not_offered(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)

    general = await rows.read(db, rows.person(tenant), finding)

    assert general["offer"] is None and general["line"] is None


async def test_the_owner_s_requirement_asks_an_unlinked_person_to_link_first(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _repository = await source.ready(db, now)
    await db.execute(
        text("UPDATE fix_source_consents SET require_github_access = true WHERE org_id = :org"),
        {"org": tenant.organization.id},
    )

    body = await _read(db, rows.person(tenant), finding)
    refused = await _ask(db, rows.person(tenant), finding)
    db.add(
        GitHubUserLink(
            user_id=tenant.user.id,
            org_id=tenant.organization.id,
            github_user_id=7,
            github_login="octo",
        )
    )
    await db.flush()
    linked = await _read(db, rows.person(tenant), finding)

    assert body["offer"]["needs_link"] is True and body["offer"]["link_sentence"]
    assert refused.status_code == 409 and refused.json()["code"] == "not_linked"
    assert linked["offer"]["needs_link"] is False


async def test_another_organization_s_finding_is_a_404(db: AsyncSession, now: datetime) -> None:
    _, finding, _ = await source.ready(db, now)
    other = await rows.tenant(db)

    response = await _ask(db, rows.person(other), finding)

    assert response.status_code == 404


async def test_ac35_the_brief_s_paths_add_a_tree_only_job_and_hold_the_stack_in_its_row(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, _ = await source.ready(db, now)

    async with source.client(db, rows.person(tenant)) as http:
        started = await http.post(source.path(finding, "/brief/files"))
        request_id = started.json()["request_id"]
        pending = await http.get(source.path(finding, f"/brief/files/{request_id}"))

    [job] = await _jobs(db, tenant.organization.id)
    assert set(job.kwargs) == {"request_id", "finding_id", "org_id"}
    assert pending.json() == {"pending": True, "paths": []}
    assert finding.started_at < now - timedelta(minutes=1)
