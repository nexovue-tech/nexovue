"""Postgres reads behind the admin console.

**Cross-tenant by design** (ADR-018): the operator searches every organization. Only the
search itself is unscoped, and it is named in `test_postgres_tenancy_structure`'s
exemptions; every read about one organization names it.
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from datetime import date
from typing import Final

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    Organization,
    Project,
    UsageDaily,
    User,
    WebsitePayment,
)
from nexovue_api.superadmin.seed import PLATFORM_ORG_ID

SEARCH_LIMIT: Final = 50


async def search_organizations(
    session: AsyncSession, needle: str | None = None
) -> list[Organization]:
    """Every customer, newest first — or the ones a needle matches.

    **No needle is the ordinary case.** One operator with a handful of customers
    opens this screen to see who they are, not to recall a name they already
    know; the search narrows a list that is already on screen
    (`docs/changes/platform-list-first/`). A needle matches the name, the slug,
    the id, or a member's address.
    """
    conditions: list[ColumnElement[bool]] = []
    if needle and needle.strip():
        pattern = f"%{needle.strip()}%"
        by_member = select(User.org_id).where(User.email.ilike(pattern))
        conditions = [
            Organization.name.ilike(pattern),
            Organization.slug.ilike(pattern),
            Organization.id.in_(by_member),
        ]
        with suppress(ValueError):
            conditions.append(Organization.id == uuid.UUID(needle.strip()))
    statement = (
        select(Organization)
        # nexovue's own organization is not a customer: it has no project, no
        # usage and nothing to suspend or comp, and it matches "nexovue" — the
        # word an operator is most likely to type.
        # `or_()` with nothing in it is false, so an absent needle drops the
        # clause entirely rather than matching no row.
        .where(*([or_(*conditions)] if conditions else []), Organization.id != PLATFORM_ORG_ID)
        .order_by(Organization.created_at.desc())
        .limit(SEARCH_LIMIT)
    )
    return list(await session.scalars(statement))


async def members_of(session: AsyncSession, *, org_id: uuid.UUID) -> list[User]:
    """Everyone in the organization who has not been removed, oldest first.

    **`id` is the tiebreak, and it is load-bearing.** `created_at` defaults to
    Postgres `now()`, which is *transaction* start time — so every row written in
    one transaction shares it to the microsecond, and an owner and the teammate
    they invited in the same request sort in whichever order the planner
    happened to produce. The list a human reads then changes between two loads of
    the same page.

    Found 2026-09-21 by the parallel gate, as `test_admin_live` returning
    `[viewer, owner]` where it had always returned `[owner, viewer]`. Nothing had
    changed but the plan. `members/queries.members_of` already ordered this way;
    this copy was written without it.

    Ids are uuidv7, so the tiebreak is itself time-ordered rather than arbitrary.
    """
    statement = (
        select(User)
        .where(User.org_id == org_id, User.removed_at.is_(None))
        .order_by(User.created_at, User.id)
    )
    return list(await session.scalars(statement))


async def projects_of(session: AsyncSession, *, org_id: uuid.UUID) -> list[Project]:
    statement = (
        select(Project)
        .where(Project.org_id == org_id, Project.deleted_at.is_(None))
        # Same tiebreak, same reason: projects created in one transaction share a
        # `created_at`, and a list that reorders itself between loads reads as a
        # bug in the data rather than in the sort.
        .order_by(Project.created_at, Project.id)
    )
    return list(await session.scalars(statement))


async def last_ingest_days(session: AsyncSession, *, org_id: uuid.UUID) -> dict[uuid.UUID, date]:
    statement = (
        select(UsageDaily.project_id, func.max(UsageDaily.day))
        .where(UsageDaily.org_id == org_id, UsageDaily.bytes > 0)
        .group_by(UsageDaily.project_id)
    )
    rows = (await session.execute(statement)).tuples().all()
    return {project_id: day for project_id, day in rows if day is not None}


async def pending_owner(session: AsyncSession, *, org_id: uuid.UUID) -> User | None:
    """The owner who has not set a password yet — an onboarded organization's first."""
    statement = select(User).where(
        User.org_id == org_id,
        User.role == "owner",
        User.password_hash.is_(None),
        User.removed_at.is_(None),
    )
    found: User | None = await session.scalar(statement.limit(1))
    return found


async def payment_is_recorded(session: AsyncSession, razorpay_payment_id: str) -> bool:
    """Whether a website payment already bought an organization. Unique system-wide,
    like an ingest key's prefix, so not scoped by one."""
    statement = select(WebsitePayment.id).where(
        WebsitePayment.razorpay_payment_id == razorpay_payment_id
    )
    return await session.scalar(statement.limit(1)) is not None
