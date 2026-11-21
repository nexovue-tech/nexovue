"""F5 rung 3's routes on a finding: Ask, the file list, Send and Cancel, and the brief's
paths (spec FR5c, FR11 to FR13a; design, *Routes*; plan T3.9, T3.11, T3.16). HTTP only.

Apart from `fixes/router.py`, whose rungs 1 and 2 import no AI code (AC33): these read the
AI connection and F3's limits to decide FR9. A person's routes only, so a token is refused
below each and none is on the read surface (AC3); every write is `MayRequestFix`, and a
support view is refused by method. Ask and the brief's paths read this service's stacks
through the tenant chokepoint (`TenantRepository`), so `/findings` keeps the telemetry
tier. **Nothing here calls GitHub or a provider**: each step adds a job, and Send stops at
Step 3's guard.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.auth.policy import MayRequestFix
from nexovue_api.deps import AppSettings, DbSession
from nexovue_api.findings import fix_service
from nexovue_api.findings.fix_source_schemas import (
    BriefFiles,
    BriefFilesStarted,
    FindingSourceFix,
    SourceFilesRequest,
    SourceFixRequest,
    SourceSendRequest,
)

router = APIRouter(prefix="/findings", tags=["findings"])


@router.get(
    "/{finding_id}/fixes/source",
    summary="A suggested fix from the service's own files: whether it can be asked, and its list",
)
async def read_source_fix(
    identity: CurrentIdentity, db: DbSession, settings: AppSettings, finding_id: uuid.UUID
) -> FindingSourceFix:
    """Rung 3 in this person's shape. Postgres only: no GitHub, no ClickHouse."""
    return await fix_service.read_source(
        db, identity=identity, finding_id=finding_id, settings=settings, now=datetime.now(UTC)
    )


@router.post(
    "/{finding_id}/fixes",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask for a suggested fix: read and list the files it would send",
)
async def ask_source_fix(
    identity: MayRequestFix,
    db: DbSession,
    repository: TenantRepository,
    settings: AppSettings,
    finding_id: uuid.UUID,
    request: SourceFixRequest,
) -> FindingSourceFix:
    """FR11: 409 with FR9's reason when it cannot be asked. Adds one job reading files."""
    return await fix_service.ask(
        db,
        repository,
        identity=identity,
        finding_id=finding_id,
        request=request,
        settings=settings,
        now=datetime.now(UTC),
    )


@router.post(
    "/{finding_id}/fixes/{fix_id}/files",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Remove files from the list, or name more, and read the list again",
)
async def change_source_files(
    identity: MayRequestFix,
    db: DbSession,
    settings: AppSettings,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    request: SourceFilesRequest,
) -> FindingSourceFix:
    return await fix_service.change_files(
        db,
        identity=identity,
        finding_id=finding_id,
        fix_id=fix_id,
        request=request,
        settings=settings,
        now=datetime.now(UTC),
    )


@router.post(
    "/{finding_id}/fixes/{fix_id}/send",
    summary="Send the listed files to the AI provider, bound to the list on screen",
)
async def send_source_files(
    identity: MayRequestFix,
    db: DbSession,
    settings: AppSettings,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    request: SourceSendRequest,
) -> FindingSourceFix:
    """FR13a: 409 with the new list when the digest is old (AC3b). Step 3 sends nothing."""
    return await fix_service.send(
        db,
        identity=identity,
        finding_id=finding_id,
        fix_id=fix_id,
        request=request,
        settings=settings,
        now=datetime.now(UTC),
    )


@router.post(
    "/{finding_id}/fixes/{fix_id}/cancel",
    summary="Cancel a suggested fix before anything is sent",
)
async def cancel_source_fix(
    identity: MayRequestFix,
    db: DbSession,
    settings: AppSettings,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
) -> FindingSourceFix:
    return await fix_service.cancel(
        db,
        identity=identity,
        finding_id=finding_id,
        fix_id=fix_id,
        settings=settings,
        now=datetime.now(UTC),
    )


@router.post(
    "/{finding_id}/brief/files",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Find the files the error names in the repository's tree, for the brief",
)
async def ask_brief_files(
    identity: MayRequestFix,
    db: DbSession,
    repository: TenantRepository,
    settings: AppSettings,
    finding_id: uuid.UUID,
) -> BriefFilesStarted:
    """FR5c: the tree only; no file is read and nothing leaves, so no consent is needed."""
    return await fix_service.brief_files(
        db,
        repository,
        identity=identity,
        finding_id=finding_id,
        settings=settings,
        now=datetime.now(UTC),
    )


@router.get(
    "/{finding_id}/brief/files/{request_id}",
    summary="The files the error names, once the tree has been read",
)
async def read_brief_files(
    identity: CurrentIdentity, db: DbSession, finding_id: uuid.UUID, request_id: uuid.UUID
) -> BriefFiles:
    return await fix_service.brief_files_result(
        db, identity=identity, finding_id=finding_id, request_id=request_id
    )
