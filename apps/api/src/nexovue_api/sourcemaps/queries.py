"""Source map metadata reads.

Every statement carries `org_id` in the statement rather than in a check
afterwards, so another organization's row returns nothing and "not yours" stays
indistinguishable from "does not exist".
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import SourceMap


async def list_for_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, limit: int
) -> Sequence[SourceMap]:
    """A project's uploaded maps, newest first.

    Ordered by `id` as well as by time: uploads from one CI step share a second,
    and a uuidv7 tie-break keeps the page stable across requests.
    """
    statement = (
        select(SourceMap)
        .where(SourceMap.org_id == org_id, SourceMap.project_id == project_id)
        .order_by(SourceMap.uploaded_at.desc(), SourceMap.id.desc())
        .limit(limit)
    )
    return (await session.scalars(statement)).all()


async def insert(session: AsyncSession, row: SourceMap) -> SourceMap:
    """Record one uploaded map. The caller's session owns the commit."""
    session.add(row)
    await session.flush()
    return row


async def find_by_debug_id(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, debug_id: str
) -> SourceMap | None:
    """The map a build stamped with this ECMA-426 debug id.

    Newest wins. The schema deliberately holds no unique constraint — a
    re-uploaded build must supersede rather than fail, and a build may
    legitimately emit two chunks sharing an id — so which row answers is decided
    here, from `uploaded_at`, exactly as migration 0017 said it would be.
    """
    statement = (
        select(SourceMap)
        .where(
            SourceMap.org_id == org_id,
            SourceMap.project_id == project_id,
            SourceMap.debug_id == debug_id,
        )
        .order_by(SourceMap.uploaded_at.desc(), SourceMap.id.desc())
        .limit(1)
    )
    found: SourceMap | None = await session.scalar(statement)
    return found


async def find_by_release_and_path(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    release: str,
    minified_path: str,
) -> SourceMap | None:
    """The map for one file of one build, when no debug id was emitted.

    Newest wins, for the reason `find_by_debug_id` gives.
    """
    statement = (
        select(SourceMap)
        .where(
            SourceMap.org_id == org_id,
            SourceMap.project_id == project_id,
            SourceMap.release == release,
            SourceMap.minified_path == minified_path,
        )
        .order_by(SourceMap.uploaded_at.desc(), SourceMap.id.desc())
        .limit(1)
    )
    found: SourceMap | None = await session.scalar(statement)
    return found
