"""Step 3's tables in the database itself (F5 plan T3.1; spec AC30, AC75's storage half,
BR8, BR9's one statement, D75's function).

Each test builds its own tenants inside a transaction rolled back afterwards.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import general_fix_rows as rows
import pytest
import source_fix_rows as source
from general_fix_rows import POSTGRES_DSN, reachable
from live import require_stack
from nexovue_platform.fixes.statements import CLEAR_STORED_CODE
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

db = rows.db
now = rows.now

require_stack(
    reachable=reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_source_storage_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


async def _refused(db: AsyncSession, sql: str, **parameters: Any) -> str:
    async with db.begin_nested() as savepoint:
        try:
            await db.execute(text(sql), parameters)
        except (IntegrityError, DBAPIError) as refused:
            await savepoint.rollback()
            return str(refused.orig)
    raise AssertionError(f"the database took {sql}")


_SOURCE = """
INSERT INTO finding_fixes (id, org_id, project_id, finding_id, kind, state,
    connection_repository_id, folder, requested_by)
VALUES (:id, :org, :project, :finding, 'source', :state, :repository, 'services/shop', :user)
"""


async def _source_row(
    db: AsyncSession, tenant: Any, finding: Any, repository: Any, state: str
) -> uuid.UUID:
    fix_id = uuid.uuid4()
    await db.execute(
        text(_SOURCE),
        {
            "id": fix_id,
            "org": tenant.organization.id,
            "project": tenant.project.id,
            "finding": finding.id,
            "state": state,
            "repository": repository.id,
            "user": tenant.user.id,
        },
    )
    return fix_id


async def test_ac30_a_suggestion_cannot_name_another_organization_s_repository(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding, _ = await source.ready(db, now)
    other = await rows.tenant(db)
    theirs = await source.fix_app(db, other, key="other/repo")

    refused = await _refused(
        db,
        _SOURCE,
        id=uuid.uuid4(),
        org=acme.organization.id,
        project=acme.project.id,
        finding=finding.id,
        state="gathering",
        repository=theirs.id,
        user=acme.user.id,
    )

    assert "fk_finding_fixes_repository" in refused


async def test_ac30_a_code_location_and_a_consent_cannot_cross_organizations(
    db: AsyncSession, now: datetime
) -> None:
    acme, _, _ = await source.ready(db, now)
    theirs = await source.fix_app(db, await rows.tenant(db), key="other/repo")

    location = await _refused(
        db,
        "INSERT INTO service_code_locations (org_id, service_name, connection_repository_id) "
        "VALUES (:org, 'x', :repository)",
        org=acme.organization.id,
        repository=theirs.id,
    )
    consent = await _refused(
        db,
        "INSERT INTO fix_source_consents (org_id, connection_repository_id, provider_base_url, "
        "provider_format, model, require_github_access, consented_at) VALUES (:org, "
        ":repository, 'https://p', 'openai_chat', 'm', false, now())",
        org=acme.organization.id,
        repository=theirs.id,
    )

    assert "fk_service_code_locations_repository" in location
    assert "fk_fix_source_consents_repository" in consent


async def test_br8_one_suggestion_in_flight_across_both_kinds(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    await _source_row(db, tenant, finding, repository, "gathered")

    refused = await _refused(
        db,
        _SOURCE,
        id=uuid.uuid4(),
        org=tenant.organization.id,
        project=tenant.project.id,
        finding=finding.id,
        state="gathering",
        repository=repository.id,
        user=tenant.user.id,
    )
    general = await _refused(
        db,
        "INSERT INTO finding_fixes (org_id, project_id, finding_id, kind, explanation_version, "
        "state, general_language) VALUES (:org, :project, :finding, 'general', 1, "
        "'proposing', 'python')",
        org=tenant.organization.id,
        project=tenant.project.id,
        finding=finding.id,
    )

    assert "uq_finding_fixes_one_working" in refused
    assert "uq_finding_fixes_one_working" in general


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("folder", "'src'"),
        ("files_read", "'{}'::jsonb"),
        ("edits", "'[]'::jsonb"),
        ("path_candidates", "'[]'::jsonb"),
    ],
)
async def test_ac75_a_general_row_holds_no_repository_folder_files_or_edits(
    db: AsyncSession, now: datetime, column: str, value: str
) -> None:
    tenant, finding, _ = await source.ready(db, now)

    refused = await _refused(
        db,
        f"INSERT INTO finding_fixes (org_id, project_id, finding_id, kind, explanation_version, "
        f"state, general_language, {column}) VALUES (:org, :project, :finding, 'general', 1, "
        f"'refused', 'python', {value})",
        org=tenant.organization.id,
        project=tenant.project.id,
        finding=finding.id,
    )

    assert "general_holds_no_source" in refused or "candidates_only_while_gathering" in refused


async def test_candidates_live_only_while_gathering(db: AsyncSession, now: datetime) -> None:
    tenant, finding, repository = await source.ready(db, now)
    fix_id = await _source_row(db, tenant, finding, repository, "gathering")

    refused = await _refused(
        db,
        "UPDATE finding_fixes SET state = 'gathered', path_candidates = '[]' WHERE id = :id",
        id=fix_id,
    )

    assert "candidates_only_while_gathering" in refused


async def test_br9_the_one_statement_empties_every_source_column_and_table(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    fix_id = await _source_row(db, tenant, finding, repository, "gathered")
    org = tenant.organization.id
    await db.execute(
        text(
            "UPDATE finding_fixes SET files_read = '{}', files_digest = 'd', withheld_refs = '[]' "
            "WHERE id = :id"
        ),
        {"id": fix_id},
    )
    await db.execute(
        text(
            "INSERT INTO finding_fix_source_packs (fix_id, org_id, files, frames, bytes, "
            "expires_at) VALUES (:id, :org, '[]', '[]', 0, :at)"
        ),
        {"id": fix_id, "org": org, "at": now + timedelta(minutes=5)},
    )
    await db.execute(
        text(
            "INSERT INTO finding_fix_checks (org_id, fix_id, requested_by, purpose, status, "
            "patch, expires_at) VALUES (:org, :id, :user, 'patch', 'unchanged', 'diff', :at)"
        ),
        {"id": fix_id, "org": org, "user": tenant.user.id, "at": now + timedelta(minutes=5)},
    )

    other_org = await db.scalar(
        text(CLEAR_STORED_CODE), {"org_id": uuid.uuid4(), "fix_id": fix_id, "reason": "aged_out"}
    )
    cleared = await db.scalar(
        text(CLEAR_STORED_CODE), {"org_id": org, "fix_id": fix_id, "reason": "aged_out"}
    )

    row = (
        await db.execute(
            text(
                "SELECT files_read, files_digest, withheld_refs, code_deleted_reason, state "
                "FROM finding_fixes WHERE id = :id"
            ),
            {"id": fix_id},
        )
    ).one()
    left = await db.scalar(
        text(
            "SELECT (SELECT count(*) FROM finding_fix_source_packs WHERE fix_id = :id) + "
            "(SELECT count(*) FROM finding_fix_checks WHERE fix_id = :id)"
        ),
        {"id": fix_id},
    )
    assert (other_org, cleared) == (0, 1)
    assert row == (None, None, None, "aged_out", "gathered")
    assert left == 0


async def test_d75_the_more_files_function_moves_only_a_proposing_row_with_names(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    fix_id = await _source_row(db, tenant, finding, repository, "proposing")
    call = "SELECT fix_request_more_files(:id, :org)"
    org = tenant.organization.id

    without_names = await db.scalar(text(call), {"id": fix_id, "org": org})
    await db.execute(
        text("UPDATE finding_fixes SET requested_paths = '[]' WHERE id = :id"), {"id": fix_id}
    )
    wrong_org = await db.scalar(text(call), {"id": fix_id, "org": uuid.uuid4()})
    moved = await db.scalar(text(call), {"id": fix_id, "org": org})

    row = (
        await db.execute(
            text("SELECT state, round, candidates_from FROM finding_fixes WHERE id = :id"),
            {"id": fix_id},
        )
    ).one()
    job = (
        await db.execute(
            text("SELECT task, queue, kwargs FROM jobs WHERE org_id = :org"), {"org": org}
        )
    ).one()
    assert (without_names, wrong_org, moved) == (False, False, True)
    assert tuple(row) == ("gathering", 2, "model")
    assert job.kwargs == {"fix_id": str(fix_id), "org_id": str(org)}
    granted = await db.scalar(
        text(
            "SELECT has_function_privilege('public', 'fix_request_more_files(uuid, uuid)', "
            "'EXECUTE')"
        )
    )
    assert granted is False
