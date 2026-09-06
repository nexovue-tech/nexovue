"""Turning source map rows into the shapes the API promises.

No SQL text and no HTTP. The upload path — object storage and symbolication —
is not here yet; what exists is the read that proves a project's maps arrived.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import SourceMap
from nexovue_api.sourcemaps import queries
from nexovue_api.sourcemaps.schemas import SourceMapListResponse, SourceMapSummary

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200


async def list_source_maps(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, limit: int
) -> SourceMapListResponse:
    """The maps uploaded for a project, newest first."""
    rows = await queries.list_for_project(
        session, org_id=org_id, project_id=project_id, limit=limit
    )
    return SourceMapListResponse(source_maps=[_to_summary(row) for row in rows])


def _to_summary(row: SourceMap) -> SourceMapSummary:
    return SourceMapSummary(
        id=row.id,
        release=row.release,
        debug_id=row.debug_id or "",
        minified_path=row.minified_path,
        size_bytes=row.size_bytes,
        uploaded_at=row.uploaded_at,
    )
