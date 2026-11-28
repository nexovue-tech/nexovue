"""Taking stored code back where the API owns the trigger, against real Postgres (F5 plan
T5.3; spec BR9, AC29, AC29b, AC30, AC31; ADR-094).

A marker string stands in for a customer file. After each trigger no column of the
suggestion holds it, the pack and every check row are gone, `code_deleted_at` says why,
and the record stays: the row, its state, its verdict and the audit entries.

- **An owner withdraws** (through the route): code goes, work in flight ends.
- **The Fix App is disconnected** (F22's disconnect, with the registered hook): consents
  are withdrawn too; a tracker connection's disconnect changes nothing of F5's.
- **A project is deleted**: its suggestions in flight stop, of either kind, and its code
  goes; another project's do not.
- **A member is removed**: their GitHub link and their check runs go; their verdict stays,
  the organization's record, and their export lists it.

Each test builds its own tenant inside a transaction rolled back afterwards.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import general_fix_rows as rows
import source_fix_rows as source
from general_fix_rows import POSTGRES_DSN, reachable
from live import require_stack
from nexovue_platform.fixes import records
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import nexovue_api
from nexovue_api.audit import service as audit_service
from nexovue_api.connections import service as connections
from nexovue_api.data.postgres.models import (
    Connection,
    FindingFix,
    FindingFixCheck,
    FindingFixSourcePack,
    FixSourceConsent,
    GitHubUserLink,
    Project,
)
from nexovue_api.findings import fix_takeback
from nexovue_api.members import service as members
from nexovue_api.profile import personal_data
from nexovue_api.projects import service as projects
from nexovue_api.settings import API_PREFIX

db = rows.db
now = rows.now

require_stack(
    reachable=reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_takeback_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

MARKER: Final = "MARKER-77ab-in-a-customer-file"
DEPLOY: Final = Path(nexovue_api.__file__).parents[4] / "deploy"


async def _suggestion(
    db: AsyncSession,
    tenant: Any,
    finding: Any,
    repository: Any,
    now: datetime,
    *,
    state: str = "proposed",
    kind: str = "source",
    project_id: uuid.UUID | None = None,
    **changes: Any,
) -> FindingFix:
    written = state in ("proposed", "stale")
    row = FindingFix(
        id=uuid.uuid4(),
        org_id=finding.org_id,
        project_id=project_id or finding.project_id,
        finding_id=finding.id,
        kind=kind,
        state=state,
        state_changed_at=now,
        connection_repository_id=repository.id if kind == "source" else None,
        folder=source.FOLDER if kind == "source" else None,
        files_read={"files": [{"ref": "file_1", "path": f"x/{MARKER}.py"}]}
        if kind == "source"
        else None,
        edits=[{"file_ref": "file_1", "old_text": MARKER}]
        if kind == "source" and written
        else None,
        files_digest="d" * 64 if kind == "source" else None,
        requested_by=tenant.user.id,
        requested_at=now,
        finished_at=now if written else None,
    )
    for name, value in changes.items():
        setattr(row, name, value)
    db.add(row)
    await db.flush()
    if kind == "source":
        if state == "proposing":
            db.add(
                FindingFixSourcePack(
                    fix_id=row.id,
                    org_id=row.org_id,
                    files=[{"ref": "file_1", "content": MARKER}],
                    frames=[],
                    bytes=len(MARKER),
                    expires_at=now + timedelta(hours=1),
                )
            )
        db.add(
            FindingFixCheck(
                id=uuid.uuid4(),
                org_id=row.org_id,
                fix_id=row.id,
                requested_by=tenant.user.id,
                purpose="patch",
                status="unchanged",
                patch=f"+{MARKER}\n",
                requested_at=now,
                finished_at=now,
                expires_at=now + timedelta(minutes=15),
            )
        )
    await db.flush()
    return row


async def _row(db: AsyncSession, fix_id: uuid.UUID) -> Any:
    return (
        await db.execute(text("SELECT * FROM finding_fixes WHERE id = :id"), {"id": fix_id})
    ).one()


async def _holds_marker(db: AsyncSession, fix_id: uuid.UUID) -> bool:
    found = await db.scalar(
        text(
            """
            SELECT (SELECT row_to_json(f)::text FROM finding_fixes AS f WHERE f.id = :id)
                || coalesce((SELECT string_agg(row_to_json(p)::text, '')
                             FROM finding_fix_source_packs AS p WHERE p.fix_id = :id), '')
                || coalesce((SELECT string_agg(row_to_json(c)::text, '')
                             FROM finding_fix_checks AS c WHERE c.fix_id = :id), '')
            """
        ),
        {"id": fix_id},
    )
    return MARKER in str(found)


async def _consents(db: AsyncSession, org_id: uuid.UUID) -> list[FixSourceConsent]:
    return list(await db.scalars(select(FixSourceConsent).where(FixSourceConsent.org_id == org_id)))


# ------------------------------------------------------------------ an owner withdraws


async def test_ac29_an_owner_s_withdrawal_takes_every_copy_and_keeps_the_record(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    written = await _suggestion(
        db, tenant, finding, repository, now, verdict="wrong", verdict_at=now
    )

    async with source.client(db, rows.person(tenant, "owner")) as http:
        withdrawn = await http.delete(
            f"{API_PREFIX}/connections/github/repositories/{repository.id}/source-consents",
            params={"folder": source.FOLDER},
        )

    assert withdrawn.status_code == 200, withdrawn.text
    kept = await _row(db, written.id)
    assert (kept.state, kept.code_deleted_reason, kept.verdict) == (
        "proposed",
        "consent_withdrawn",
        "wrong",
    )
    assert not await _holds_marker(db, written.id)


# --------------------------------------------------------- the Fix App is disconnected


async def test_ac29_disconnecting_the_fix_app_withdraws_consent_and_takes_every_copy(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    written = await _suggestion(db, tenant, finding, repository, now)
    other_finding = await rows.finding(db, tenant, now, service_name="payments")
    flying = await _suggestion(db, tenant, other_finding, repository, now, state="proposing")

    assert fix_takeback.on_disconnect in connections.DISCONNECT_HOOKS
    await connections.disconnect(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        connection_id=repository.connection_id,
        hooks=tuple(connections.DISCONNECT_HOOKS),
    )

    [consent] = await _consents(db, tenant.organization.id)
    assert consent.withdrawn_reason == "disconnected" and consent.withdrawn_at is not None
    assert (await _row(db, written.id)).code_deleted_reason == "disconnected"
    ended = await _row(db, flying.id)
    assert (ended.state, ended.reason, ended.code_deleted_reason) == (
        "failed",
        "consent_ended_in_flight",
        "disconnected",
    )
    assert not await _holds_marker(db, written.id) and not await _holds_marker(db, flying.id)
    audited = (
        await db.execute(
            text("SELECT detail FROM audit_log WHERE org_id = :org AND action = :action"),
            {"org": tenant.organization.id, "action": audit_service.AI_SOURCE_DISCLOSURE_WITHDRAWN},
        )
    ).scalar_one()
    assert audited["reason"] == "disconnected"


async def test_disconnecting_a_tracker_connection_changes_nothing_of_f5_s(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    written = await _suggestion(db, tenant, finding, repository, now)
    tracker = Connection(
        id=uuid.uuid4(), org_id=tenant.organization.id, kind="github", state="working"
    )
    db.add(tracker)
    await db.flush()

    await connections.disconnect(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        connection_id=tracker.id,
        hooks=tuple(connections.DISCONNECT_HOOKS),
    )

    assert (await _row(db, written.id)).code_deleted_at is None
    assert all(each.withdrawn_at is None for each in await _consents(db, tenant.organization.id))


# ------------------------------------------------------------------ a project is deleted


async def test_adr_094_deleting_a_project_stops_its_work_and_takes_its_code(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    second = Project(
        id=uuid.uuid4(), org_id=tenant.organization.id, slug=f"p-{uuid.uuid4().hex[:8]}", name="P2"
    )
    db.add(second)
    await db.flush()
    written = await _suggestion(db, tenant, finding, repository, now)
    flying = await _suggestion(
        db,
        tenant,
        await rows.finding(db, tenant, now, service_name="payments"),
        repository,
        now,
        state="proposing",
    )
    general = await rows.fix(
        db, await rows.finding(db, tenant, now, service_name="search"), now, state="proposing"
    )
    elsewhere_finding = await rows.finding(db, tenant, now, project_id=second.id)
    elsewhere = await _suggestion(
        db, tenant, elsewhere_finding, repository, now, project_id=second.id
    )

    await projects.delete(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        project_id=tenant.project.id,
    )

    assert (await _row(db, written.id)).code_deleted_reason == "project_deleted"
    ended = await _row(db, flying.id)
    assert (ended.state, ended.reason) == ("failed", "project_deleted")
    stopped = await _row(db, general.id)
    assert (stopped.state, stopped.reason) == ("failed", "project_deleted")
    assert not await _holds_marker(db, written.id) and not await _holds_marker(db, flying.id)
    assert (await _row(db, elsewhere.id)).code_deleted_at is None


# ------------------------------------------------------------------ a member is removed


async def test_ac31_removing_a_member_takes_their_link_and_checks_and_keeps_their_verdict(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    leaving = await db.scalar(
        text(
            "INSERT INTO users (org_id, email, name, role, password_hash) "
            "VALUES (:org, :email, 'Bea', 'member', 'x') RETURNING id"
        ),
        {"org": tenant.organization.id, "email": f"bea-{uuid.uuid4()}@example.test"},
    )
    written = await _suggestion(
        db,
        tenant,
        finding,
        repository,
        now,
        verdict="merge_as_is",
        verdict_by=leaving,
        verdict_at=now,
    )
    db.add(
        FindingFixCheck(
            id=uuid.uuid4(),
            org_id=tenant.organization.id,
            fix_id=written.id,
            requested_by=leaving,
            purpose="read",
            status="unchanged",
            requested_at=now,
            finished_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    db.add(
        GitHubUserLink(
            user_id=leaving, org_id=tenant.organization.id, github_user_id=5, github_login="bea"
        )
    )
    await db.flush()

    await members.remove(
        db,
        actor=tenant.user,
        organization=tenant.organization,
        member_id=leaving,
        now=now,
    )

    checks = (
        (
            await db.execute(
                text("SELECT requested_by FROM finding_fix_checks WHERE fix_id = :id"),
                {"id": written.id},
            )
        )
        .scalars()
        .all()
    )
    assert checks == [tenant.user.id]  # theirs gone, the owner's own kept
    assert (
        await db.scalar(select(GitHubUserLink.user_id).where(GitHubUserLink.user_id == leaving))
        is None
    )
    kept = await _row(db, written.id)
    assert (kept.verdict, kept.verdict_by) == ("merge_as_is", leaving)


async def test_the_export_lists_the_person_s_link_and_verdicts(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    written = await _suggestion(
        db,
        tenant,
        finding,
        repository,
        now,
        verdict="useful_start",
        verdict_by=tenant.user.id,
        verdict_at=now,
    )
    db.add(
        GitHubUserLink(
            user_id=tenant.user.id,
            org_id=tenant.organization.id,
            github_user_id=6,
            github_login="acme-person",
        )
    )
    await db.flush()

    exported = await personal_data.export(
        db, user=tenant.user, organization=tenant.organization, now=now
    )

    assert exported.github_link is not None and exported.github_link.github_login == "acme-person"
    [verdict] = exported.fix_verdicts
    assert (verdict.fix_id, verdict.verdict) == (written.id, "useful_start")
    assert MARKER not in json.dumps(exported.model_dump(mode="json"))


def test_the_sweep_writes_the_api_s_own_audit_words() -> None:
    assert records.AI_SOURCE_DISCLOSURE_WITHDRAWN == audit_service.AI_SOURCE_DISCLOSURE_WITHDRAWN
    assert records.REPOSITORY_AUDIT_TARGET == audit_service.REPOSITORY_TARGET


# ------------------------------------------------------------------ a restore


async def test_ac6a_the_restore_s_statements_take_every_copy_and_withdraw_every_consent(
    db: AsyncSession, now: datetime
) -> None:
    """The file `restore-postgres.sh` runs, statement by statement, on rows the snapshot
    brought back: code deleted after the snapshot does not come back (BR9, D29)."""
    tenant, finding, repository = await source.ready(db, now)
    written = await _suggestion(db, tenant, finding, repository, now)
    flying = await _suggestion(
        db,
        tenant,
        await rows.finding(db, tenant, now, service_name="payments"),
        repository,
        now,
        state="proposing",
    )
    sql = (DEPLOY / "backup" / "clear-stored-code.sql").read_text(encoding="utf-8")

    for statement in (part for part in sql.split(";\n") if part.strip("\n -")):
        lines = [line for line in statement.splitlines() if not line.startswith("--")]
        await db.execute(text("\n".join(lines)))

    assert (await _row(db, written.id)).code_deleted_reason == "restored"
    ended = await _row(db, flying.id)
    assert (ended.state, ended.reason) == ("failed", "consent_ended_in_flight")
    assert not await _holds_marker(db, written.id) and not await _holds_marker(db, flying.id)
    assert all(
        each.withdrawn_reason == "restored" for each in await _consents(db, tenant.organization.id)
    )
