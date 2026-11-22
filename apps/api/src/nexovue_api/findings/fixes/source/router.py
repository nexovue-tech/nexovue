"""F5 rung 3's routes on a finding: Ask, the file list, Send and Cancel, the brief's paths,
the check run, the patch, the verdict, the draft pull request and **Delete this branch**
(spec FR5c, FR11 to FR13a, FR20a, FR22 to FR24a, FR29; design, *Routes*; plan T3.9, T3.11,
T3.16, T4.12, T5.1, T5.2, T7.4, T7.10). HTTP only.

Apart from `fixes/router.py`, whose rungs 1 and 2 import no AI code (AC33): these read the
AI connection and F3's limits to decide FR9. A person's routes only, so a token is refused
below each and none is on the read surface (AC3); every write is `MayRequestFix`, and a
support view is refused by method, and the patch, a read, by `MayReadFixCode`. Ask and the
brief's paths read this service's stacks through the tenant chokepoint
(`TenantRepository`), so `/findings` keeps the telemetry tier. **Nothing here calls GitHub
or a provider**: each step adds a job; Send's goes to the AI role, and the check run's to
`scheduler-connections`. The draft's routes are `MayOpenFixPullRequest`, and each passes
`write_gate.WRITE_ACCESS_GO`, so while write access is off in code (ADR-097) they refuse
`writes_off` before reading anything.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, status
from fastapi.responses import PlainTextResponse
from nexovue_platform.fixes import write_gate
from nexovue_platform.fixes.records import CHECK_FOR_READ

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.auth.policy import MayOpenFixPullRequest, MayReadFixCode, MayRequestFix
from nexovue_api.deps import AppSettings, DbSession
from nexovue_api.findings import fix_checks, fix_draft, fix_service, fix_verdict
from nexovue_api.findings.fix_checks import PATCH_MEDIA_TYPE
from nexovue_api.findings.fix_schemas import VerdictRequest, VerdictView
from nexovue_api.findings.fix_source_schemas import (
    BriefFiles,
    BriefFilesStarted,
    CheckRequest,
    CheckView,
    DeleteBranchStarted,
    DraftRequest,
    DraftStarted,
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
        db,
        identity=identity,
        finding_id=finding_id,
        settings=settings,
        now=datetime.now(UTC),
        go=write_gate.WRITE_ACCESS_GO,
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
    """FR13a: 409 with the new list when the digest is old (AC3b); else the AI role's job."""
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
    "/{finding_id}/fixes/{fix_id}/check",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Check my access: ask GitHub whether you may read this suggestion's code",
)
async def check_source_fix(
    identity: MayRequestFix,
    db: DbSession,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    request: Annotated[CheckRequest | None, Body()] = None,
) -> CheckView:
    """`read` (D20): under the owner's requirement, quoted code shows only after this
    person's own check passes. `patch` (FR20a): the files re-read and the patch rebuilt for
    this person. Adds one job; the result is polled through the suggestion's read. One
    route for both kinds: a general row refuses, `general_has_no_code` (AC73)."""
    return await fix_checks.ask_check(
        db,
        identity=identity,
        finding_id=finding_id,
        fix_id=fix_id,
        purpose=CHECK_FOR_READ if request is None else request.for_,
        go=write_gate.WRITE_ACCESS_GO,
        now=datetime.now(UTC),
    )


@router.get(
    "/{finding_id}/fixes/{fix_id}/patch",
    summary="Download a suggestion as a patch, rebuilt by your own check",
    response_class=PlainTextResponse,
    responses={200: {"content": {PATCH_MEDIA_TYPE: {}}}},
)
async def download_patch(
    identity: MayReadFixCode, db: DbSession, finding_id: uuid.UUID, fix_id: uuid.UUID
) -> PlainTextResponse:
    """UR6, FR20a: only this person's own passed patch check, within its 15 minutes, on a
    written suggestion that still holds its code. Never a token or a support view."""
    served = await fix_checks.serve_patch(
        db, identity=identity, finding_id=finding_id, fix_id=fix_id, now=datetime.now(UTC)
    )
    return PlainTextResponse(
        served.text,
        media_type=PATCH_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{served.filename}"'},
    )


@router.post(
    "/{finding_id}/fixes/{fix_id}/verdict",
    summary="Say whether a suggestion was useful; this changes nothing else",
)
async def record_verdict(
    identity: MayRequestFix,
    db: DbSession,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    request: VerdictRequest,
) -> VerdictView:
    """FR29, UR11: one answer per suggestion, the last person's. Either kind."""
    return await fix_verdict.record(
        db,
        identity=identity,
        finding_id=finding_id,
        fix_id=fix_id,
        verdict=request.verdict,
        now=datetime.now(UTC),
    )


@router.post(
    "/{finding_id}/fixes/{fix_id}/draft-pull-request",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Open a suggestion as a draft pull request, from your own passed check",
)
async def open_draft_pull_request(
    identity: MayOpenFixPullRequest,
    db: DbSession,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    request: DraftRequest,
) -> DraftStarted:
    """FR22: refused `writes_off` while write access is off in code. With the go, this
    person's own passed `draft` check within 15 minutes, its digest, and one job."""
    return await fix_draft.open_draft(
        db,
        identity=identity,
        finding_id=finding_id,
        fix_id=fix_id,
        request=request,
        go=write_gate.WRITE_ACCESS_GO,
        now=datetime.now(UTC),
    )


@router.post(
    "/{finding_id}/fixes/{fix_id}/delete-branch",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Delete this suggestion's branch, once its pull request is closed or never opened",
)
async def delete_suggestion_branch(
    identity: MayOpenFixPullRequest,
    db: DbSession,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
) -> DeleteBranchStarted:
    """FR23, AC66: a job deletes only `nexovue/suggestion-<id>`, only at its own head."""
    return await fix_draft.delete_branch(
        db,
        identity=identity,
        finding_id=finding_id,
        fix_id=fix_id,
        go=write_gate.WRITE_ACCESS_GO,
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
