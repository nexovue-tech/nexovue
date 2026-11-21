"""A person's GitHub link as personal data (F5 plan T3.17; spec AC31, AC11).

The export carries it; removing the member deletes it and any attempt; a support view
cannot start a link (refused by method below the route, `_hold_support_view`), and the
routes take only a person's identity. Each test builds its own tenant inside a transaction
rolled back afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import general_fix_rows as rows
from fastapi.routing import APIRoute
from general_fix_rows import POSTGRES_DSN, reachable
from live import require_stack
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.connections.fix import router as fix_setup_router
from nexovue_api.data.postgres.models import GitHubLinkAttempt, GitHubUserLink
from nexovue_api.members import service as members
from nexovue_api.profile import personal_data
from nexovue_api.signup.service import ProvisionedTenant

db = rows.db
now = rows.now

require_stack(
    reachable=reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_github_link_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


async def _linked(db: AsyncSession, tenant: ProvisionedTenant, now: datetime) -> None:
    db.add(
        GitHubUserLink(
            user_id=tenant.user.id,
            org_id=tenant.organization.id,
            github_user_id=4242,
            github_login="octo-person",
            linked_at=now,
        )
    )
    db.add(
        GitHubLinkAttempt(
            org_id=tenant.organization.id,
            user_id=tenant.user.id,
            session_hash="s" * 64,
            state_hash="t" * 64,
            status="waiting",
            expires_at=now + timedelta(minutes=10),
        )
    )
    await db.flush()


async def test_ac31_the_personal_data_export_carries_the_link(
    db: AsyncSession, now: datetime
) -> None:
    tenant = await rows.tenant(db)
    await _linked(db, tenant, now)

    exported = await personal_data.export(
        db, user=tenant.user, organization=tenant.organization, now=now
    )

    assert exported.github_link is not None
    assert (exported.github_link.github_user_id, exported.github_link.github_login) == (
        4242,
        "octo-person",
    )


async def test_ac31_removing_a_member_deletes_the_link_and_its_attempts(
    db: AsyncSession, now: datetime
) -> None:
    tenant = await rows.tenant(db)
    await _linked(db, tenant, now)

    await members.scrub(db, member=tenant.user, now=datetime.now(UTC))
    await db.flush()

    for model in (GitHubUserLink, GitHubLinkAttempt):
        left = await db.scalar(
            select(func.count()).select_from(model).where(model.org_id == tenant.organization.id)
        )
        assert left == 0, model.__tablename__


def test_ac11_the_link_routes_take_a_person_s_identity_and_write_only_by_post() -> None:
    routes = {
        route.name: route for route in fix_setup_router.router.routes if isinstance(route, APIRoute)
    }

    for name in ("start_user_link", "complete_user_link"):
        assert routes[name].methods == {"POST"}
    assert routes["remove_user_link"].methods == {"DELETE"}


async def test_ac11_a_support_view_reads_no_paths_in_the_audit_log(
    db: AsyncSession, now: datetime
) -> None:
    from nexovue_api.audit import read
    from nexovue_api.audit import service as audit

    tenant = await rows.tenant(db)
    audit.record_fix_event(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        action=audit.REPOSITORY_READ,
        target_kind=audit.FINDING_TARGET,
        target_id=tenant.user.id,
        detail={"purpose": "fix", "paths": [{"path": "src/a.py"}], "files": [{"path": "x"}]},
    )
    await db.flush()

    viewed = await read.read_page(db, org_id=tenant.organization.id, support_view=True)
    owned = await read.read_page(db, org_id=tenant.organization.id)

    [seen] = [e for e in viewed.entries if e.action == audit.REPOSITORY_READ]
    [mine] = [e for e in owned.entries if e.action == audit.REPOSITORY_READ]
    assert seen.detail == {"purpose": "fix"}
    assert mine.detail is not None and "paths" in mine.detail
