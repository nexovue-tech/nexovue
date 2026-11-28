"""F5's routes on a finding (spec FR1 to FR5; design, *Routes*). HTTP only.

Their own router, apart from F3's explanation routes in `findings/router.py`, whose
service imports AI code: the deploy line and the brief must work with AI off, and AC33's
import walk starts from this module (founder, 2026-11-21). A person's reads, never on the
read surface (AC3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.deps import AppSettings, DbSession
from nexovue_api.findings import fix_read
from nexovue_api.findings.fix_schemas import FindingFixes

router = APIRouter(prefix="/findings", tags=["findings"])


@router.get(
    "/{finding_id}/fixes",
    summary="What changed before a finding, and a brief to copy into your own coding tool",
)
async def read_fixes(
    identity: CurrentIdentity, db: DbSession, settings: AppSettings, finding_id: uuid.UUID
) -> FindingFixes:
    """F5 (FR1 to FR5): a person's read, so a token is refused below this route and it
    stays off the read surface (AC3). Works with AI off; calls no provider and no GitHub."""
    return await fix_read.read_fixes(
        db,
        org_id=identity.org_id,
        finding_id=finding_id,
        settings=settings,
        now=datetime.now(UTC),
    )
