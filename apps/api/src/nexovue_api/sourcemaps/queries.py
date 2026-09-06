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
