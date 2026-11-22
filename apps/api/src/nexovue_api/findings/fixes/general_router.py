"""F5 rung 2b's routes on a finding: the general suggestion (spec FR31 to FR38; design,
*Routes*; plan T2b.7). HTTP only.

Apart from `router.py`, whose rungs 1 and 2 must import no AI code (AC33): these read the
AI connection and F3's limits. A person's routes only, so a token is refused below each and
none is on the read surface (AC74). The click is `MayRequestFix`; a support view is refused
by method. The actions on code refuse a general row with a named code (AC73): CI here; the
check, the patch and the draft on rung 3's router, which serve both kinds.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayRequestFix
from nexovue_api.deps import AppSettings, DbSession
from nexovue_api.findings import fix_general
from nexovue_api.findings.fix_schemas import FindingGeneralFix, GeneralFixRequest

router = APIRouter(prefix="/findings", tags=["findings"])


@router.get(
    "/{finding_id}/fixes/general",
    summary="A general fix suggestion where Nexovue cannot read the service's code",
)
async def read_general_fix(
    identity: CurrentIdentity, db: DbSession, settings: AppSettings, finding_id: uuid.UUID
) -> FindingGeneralFix:
    """Rung 2b in this person's shape: why it is not offered, the offer, the latest
    suggestion checked again, and older ones. Postgres only; calls no provider."""
    return await fix_general.read_general(
        db, identity=identity, finding_id=finding_id, settings=settings, now=datetime.now(UTC)
    )


@router.post(
    "/{finding_id}/fixes/general",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask for a general fix suggestion, from this finding's facts and a language",
)
async def ask_general_fix(
    identity: MayRequestFix,
    db: DbSession,
    settings: AppSettings,
    finding_id: uuid.UUID,
    request: GeneralFixRequest,
) -> FindingGeneralFix:
    """FR31 to FR33: 409 with the reason's code when it is not offered. Adds one job on
    `insight-fix`; no repository, no GitHub, no source."""
    return await fix_general.ask_general(
        db,
        identity=identity,
        finding_id=finding_id,
        language=request.language,
        settings=settings,
        now=datetime.now(UTC),
    )


async def _refuse(
    identity: MayRequestFix, db: DbSession, finding_id: uuid.UUID, fix_id: uuid.UUID
) -> None:
    await fix_general.refuse_code_action(
        db, org_id=identity.org_id, finding_id=finding_id, fix_id=fix_id
    )


@router.post("/{finding_id}/fixes/{fix_id}/run-ci", summary="Ask CI to run on a draft")
async def run_ci(
    identity: MayRequestFix, db: DbSession, finding_id: uuid.UUID, fix_id: uuid.UUID
) -> None:
    """Rung 5 (Step 8). A general row refuses: `general_has_no_code` (AC73)."""
    await _refuse(identity, db, finding_id, fix_id)
