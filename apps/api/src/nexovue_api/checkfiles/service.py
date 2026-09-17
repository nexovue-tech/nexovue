"""Where a check's files live, who may attach them, and when they go.

**Bytes first, then the row would leave orphans; the row first leaves none.** The
row is flushed inside the request's transaction and the object is written after
it: if the store refuses, the exception rolls the row back, and an object whose
row never committed is still under the tenant's prefix for a purge to remove.

**Nothing here deletes an object.** A request cannot delete one after its own
transaction commits — FastAPI runs even background tasks before the session
dependency does — and deleting one before would lose a file the rolled-back check
still names. Deleting a check detaches its files instead, and the scheduler's sweep
removes the row and then the object once the detachment is durable (BR9).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.checkfiles import queries
from nexovue_api.checkfiles.schemas import (
    DEFAULT_CONTENT_TYPE,
    MAX_FILE_BYTES,
    MAX_FILE_NAME_LENGTH,
    CheckFileSummary,
)
from nexovue_api.data.objectstore import ObjectStore, ObjectStoreError
from nexovue_api.data.postgres.models import CheckFile
from nexovue_api.errors.exceptions import (
    DependencyUnavailableError,
    InvalidRequestError,
)
from nexovue_api.projects import service as projects

_KEY_ROOT: Final = "synthetics"

_NO_STORE: Final = (
    "Files cannot be attached on this deployment because it has no object storage configured."
)
_STORE_FAILED: Final = "The file could not be stored just now. Nothing was saved; try again."


def tenant_prefix(org_id: uuid.UUID) -> str:
    """Every check file one organization owns, as a key prefix for erasure."""
    return f"{_KEY_ROOT}/{org_id}/"


def object_key(org_id: uuid.UUID, file_id: uuid.UUID) -> str:
    return f"{_KEY_ROOT}/{org_id}/files/{file_id}"


def _summary(row: CheckFile) -> CheckFileSummary:
    return CheckFileSummary(
        id=row.id,
        project_id=row.project_id,
        check_id=row.check_id,
        name=row.name,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
    )


async def upload(
    session: AsyncSession,
    store: ObjectStore | None,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
    content_type: str | None,
    body: bytes,
) -> CheckFileSummary:
    """Keep one file for a check that may not be saved yet (FR4)."""
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    if store is None:
        raise DependencyUnavailableError(_NO_STORE)
    if len(body) > MAX_FILE_BYTES:
        raise InvalidRequestError(
            f"A file may be at most {MAX_FILE_BYTES // (1024 * 1024)} MB.",
            errors=[{"field": "file", "reason": "larger than 3 MB"}],
        )
    display_name = (name.strip() or "file")[:MAX_FILE_NAME_LENGTH]
    row = CheckFile(
        org_id=org_id,
        project_id=project_id,
        name=display_name,
        content_type=(content_type or DEFAULT_CONTENT_TYPE)[:MAX_FILE_NAME_LENGTH],
        size_bytes=len(body),
    )
    queries.add_file(session, row)
    await session.flush()
    await session.refresh(row)
    try:
        await store.put(object_key(org_id, row.id), body, content_type=row.content_type)
    except ObjectStoreError as refused:
        raise DependencyUnavailableError(_STORE_FAILED) from refused
    return _summary(row)


async def validate_attachable(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    file_ids: Sequence[uuid.UUID],
    check_id: uuid.UUID | None,
    in_use_allowed: bool = False,
) -> dict[uuid.UUID, CheckFile]:
    """The rows for these ids, or the ids that cannot be used, named.

    Saving attaches a file, so it must be unattached or already this check's.
    Run now only reads one, so any file of the project will do.
    """
    rows = {
        row.id: row
        for row in await queries.files_in_project(
            session, org_id=org_id, project_id=project_id, file_ids=file_ids
        )
    }
    unusable = [
        file_id
        for file_id in file_ids
        if file_id not in rows
        or (not in_use_allowed and rows[file_id].check_id not in (None, check_id))
    ]
    if unusable:
        raise InvalidRequestError(
            "A file this check sends is missing or belongs to another check.",
            errors=[
                {"field": "config", "reason": f"file {file_id} is not available"}
                for file_id in unusable
            ],
        )
    return rows


async def attach(
    session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID, file_ids: Sequence[uuid.UUID]
) -> None:
    await queries.attach(session, org_id=org_id, check_id=check_id, file_ids=file_ids)


async def count_for_check(session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID) -> int:
    return await queries.count_files_of_check(session, org_id=org_id, check_id=check_id)


async def detach_for_check(
    session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID
) -> None:
    """A deleted check's files are detached with it; the sweep removes them (BR9)."""
    await queries.detach_files_of_check(session, org_id=org_id, check_id=check_id)
