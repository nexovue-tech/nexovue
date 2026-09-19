"""Uploading the files a check sends, before or after the check is saved.

**The body is the file**, not a multipart form. A form is parsed — and spooled to
disk — before any dependency runs, so a size cap checked afterwards is a cap on
what we already accepted. Reading the raw stream lets the cap stop the upload at
the byte it is crossed, after the caller's role has been checked (design, *Files
in requests*; the `deploys/router.py` pattern).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query, Request, status

from nexovue_api.auth.policy import MayOperateChecks
from nexovue_api.checkfiles import service
from nexovue_api.checkfiles.schemas import MAX_FILE_BYTES, MAX_FILE_NAME_LENGTH, CheckFileSummary
from nexovue_api.deps import DbSession, ObjectStoreDep
from nexovue_api.errors.exceptions import InvalidRequestError

router = APIRouter(prefix="/check-files", tags=["check files"])

TOO_LARGE: Final = "A file may be at most 3 MB."

FileName = Annotated[str, Query(min_length=1, max_length=MAX_FILE_NAME_LENGTH)]


@router.post("", status_code=status.HTTP_201_CREATED, summary="Keep a file a check will send")
async def upload_file(
    identity: MayOperateChecks,
    db: DbSession,
    store: ObjectStoreDep,
    request: Request,
    project_id: Annotated[uuid.UUID, Query()],
    name: FileName,
) -> CheckFileSummary:
    return await service.upload(
        db,
        store,
        org_id=identity.org_id,
        project_id=project_id,
        name=name,
        content_type=request.headers.get("content-type"),
        body=await _bounded_body(request),
    )


async def _bounded_body(request: Request) -> bytes:
    """The bytes that arrived, refused at the first byte past the cap (FR4, AC6)."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_FILE_BYTES:
        raise InvalidRequestError(TOO_LARGE, errors=[{"field": "file", "reason": TOO_LARGE}])
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_FILE_BYTES:
            raise InvalidRequestError(TOO_LARGE, errors=[{"field": "file", "reason": TOO_LARGE}])
        chunks.append(chunk)
    return b"".join(chunks)
