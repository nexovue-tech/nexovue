"""Runbooks (F4 FR32; design section 6 and *`/settings/runbooks`*). No HTTP.

A runbook belongs to one project (D32). Saving one writes its chunks **in the same
transaction** (`nexovue_platform.knowledge.chunk_runbook`), so chat never searches a runbook's
old text, and deleting it takes its chunks by their cascading key (BR16, AC38). Every write is
audited without its text (AC71).

**Absent where AI is not offered** (`ai_available`): on the hosted service these routes
answer as no route does, and no chunk is ever written (AC92).

Refusals are sentences with their own codes: over 32 KiB, the 201st, and text that looks like
a credential, which a second click (`confirm_credentials`) saves anyway (AC69, AC90). The
credential check is the one FR40's send-time mask applies, so what warns and what is masked
before sending are the same list.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nexovue_platform.assistant_masks import holds_credential
from nexovue_platform.deployment import Deployment, ai_available
from nexovue_platform.knowledge import (
    MAX_RUNBOOK_BYTES,
    MAX_RUNBOOK_SERVICE_CHARACTERS,
    MAX_RUNBOOK_TITLE_CHARACTERS,
    MAX_RUNBOOKS_PER_PROJECT,
    chunk_runbook,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.postgres.models import KnowledgeChunk, Project, Runbook, User
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.projects.service import require_project
from nexovue_api.runbooks.schemas import (
    NewRunbook,
    RefusedFile,
    RunbookFields,
    RunbookList,
    RunbookSummary,
    RunbookUpload,
    RunbookView,
    UploadedFile,
    UploadResult,
)

NOT_HERE: Final = "Runbooks are not offered on this install."
NO_SUCH_RUNBOOK: Final = "No such runbook."
# The sentences of AC90 and AC69, each with its code.
REFUSALS: Final[dict[str, str]] = {
    "runbook_too_large": f"A runbook can be at most {MAX_RUNBOOK_BYTES // 1024} KiB.",
    "runbook_empty": "A runbook needs some text.",
    "runbook_cap": (
        f"This project holds {MAX_RUNBOOKS_PER_PROJECT} runbooks, the most it can. "
        "Delete one to add another."
    ),
    "title_needed": "A runbook needs a title.",
    "title_too_long": f"A title can be at most {MAX_RUNBOOK_TITLE_CHARACTERS} characters.",
    "service_too_long": (
        f"A service name can be at most {MAX_RUNBOOK_SERVICE_CHARACTERS} characters."
    ),
    "looks_like_credential": (
        "This text looks like it holds a password, token or key. Do not paste secrets. "
        "Save anyway only if it holds none."
    ),
}
# A Markdown file's first top-level heading names its runbook; else its file name does.
_FIRST_TITLE: Final = re.compile(r"^#[ \t]+(.+?)[ \t#]*$", re.MULTILINE)
_MARKDOWN_SUFFIX: Final = re.compile(r"\.(?:md|markdown|txt)$", re.IGNORECASE)


class _Refused(Exception):  # noqa: N818 — a refusal, named for the reader
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    @property
    def detail(self) -> str:
        return REFUSALS[self.code]


def require_offered(deployment: Deployment) -> None:
    """Runbooks and their chunks exist only where AI may be offered (AC92)."""
    if not ai_available(deployment):
        raise NotFoundError(NOT_HERE)


def _name_of(user: User | None) -> str | None:
    if user is None or user.removed_at is not None:
        return None
    return user.name or user.email


def _summary(runbook: Runbook, author: User | None) -> RunbookSummary:
    return RunbookSummary(
        id=runbook.id,
        project_id=runbook.project_id,
        title=runbook.title,
        service_name=runbook.service_name,
        author=_name_of(author),
        created_at=runbook.created_at,
        updated_at=runbook.updated_at,
    )


def _view(runbook: Runbook, author: User | None, *, identity: Identity) -> RunbookView:
    return RunbookView(
        **_summary(runbook, author).model_dump(),
        content=None if identity.impersonation is not None else runbook.content,
    )


async def list_runbooks(
    session: AsyncSession, *, identity: Identity, project_id: uuid.UUID
) -> RunbookList:
    await require_project(session, org_id=identity.org_id, project_id=project_id)
    rows = await session.execute(
        select(Runbook, User)
        .outerjoin(User, (User.id == Runbook.created_by) & (User.org_id == Runbook.org_id))
        .where(Runbook.org_id == identity.org_id, Runbook.project_id == project_id)
        .order_by(func.lower(Runbook.title), Runbook.id)
    )
    return RunbookList(runbooks=[_summary(runbook, author) for runbook, author in rows.all()])


async def _own(session: AsyncSession, identity: Identity, runbook_id: uuid.UUID) -> Runbook:
    found: Runbook | None = await session.scalar(
        select(Runbook).where(Runbook.org_id == identity.org_id, Runbook.id == runbook_id)
    )
    if found is None:
        raise NotFoundError(NO_SUCH_RUNBOOK)
    # A deleted project's runbooks wait for the sweep; they are not read meanwhile.
    await require_project(session, org_id=identity.org_id, project_id=found.project_id)
    return found


async def _author(session: AsyncSession, runbook: Runbook) -> User | None:
    if runbook.created_by is None:
        return None
    found: User | None = await session.scalar(
        select(User).where(User.org_id == runbook.org_id, User.id == runbook.created_by)
    )
    return found


async def read_runbook(
    session: AsyncSession, *, identity: Identity, runbook_id: uuid.UUID
) -> RunbookView:
    runbook = await _own(session, identity, runbook_id)
    return _view(runbook, await _author(session, runbook), identity=identity)


@dataclass(frozen=True, slots=True)
class _Checked:
    title: str
    service_name: str | None
    content: str


def _checked(title: str, service_name: str | None, content: str, *, confirmed: bool) -> _Checked:
    title = title.strip()
    service = (service_name or "").strip() or None
    if not title:
        raise _Refused("title_needed")
    if len(title) > MAX_RUNBOOK_TITLE_CHARACTERS:
        raise _Refused("title_too_long")
    if service is not None and len(service) > MAX_RUNBOOK_SERVICE_CHARACTERS:
        raise _Refused("service_too_long")
    if not content.strip():
        raise _Refused("runbook_empty")
    if len(content.encode()) > MAX_RUNBOOK_BYTES:
        raise _Refused("runbook_too_large")
    if not confirmed and (holds_credential(content) or holds_credential(title)):
        raise _Refused("looks_like_credential")
    return _Checked(title, service, content)


async def _count(session: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID) -> int:
    counted = await session.scalar(
        select(func.count())
        .select_from(Runbook)
        .where(Runbook.org_id == org_id, Runbook.project_id == project_id)
    )
    return int(counted or 0)


def _write_chunks(session: AsyncSession, runbook: Runbook) -> int:
    pieces = chunk_runbook(runbook.content)
    session.add_all(
        KnowledgeChunk(
            org_id=runbook.org_id,
            project_id=runbook.project_id,
            source_kind="runbook",
            runbook_id=runbook.id,
            position=position,
            content=piece,
        )
        for position, piece in enumerate(pieces)
    )
    return len(pieces)


def _audit(
    session: AsyncSession, identity: Identity, runbook: Runbook, action: str, chunks: int
) -> None:
    audit.record_runbook_event(
        session,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        action=action,
        runbook_id=runbook.id,
        project_id=runbook.project_id,
        size_bytes=len(runbook.content.encode()),
        chunks=chunks,
    )


async def _create(
    session: AsyncSession,
    identity: Identity,
    project_id: uuid.UUID,
    checked: _Checked,
    *,
    now: datetime,
) -> Runbook:
    # The project's row, locked, so two saves at 199 cannot both pass the cap.
    await session.execute(
        select(Project.id)
        .where(Project.org_id == identity.org_id, Project.id == project_id)
        .with_for_update()
    )
    if await _count(session, identity.org_id, project_id) >= MAX_RUNBOOKS_PER_PROJECT:
        raise _Refused("runbook_cap")
    runbook = Runbook(
        org_id=identity.org_id,
        project_id=project_id,
        service_name=checked.service_name,
        title=checked.title,
        content=checked.content,
        created_by=identity.user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(runbook)
    await session.flush()
    chunks = _write_chunks(session, runbook)
    _audit(session, identity, runbook, audit.RUNBOOK_CREATED, chunks)
    await session.flush()
    return runbook


def _raise(refused: _Refused) -> None:
    error = ConflictError if refused.code == "runbook_cap" else InvalidRequestError
    raise error(refused.detail, code=refused.code) from refused


async def create_runbook(
    session: AsyncSession, *, identity: Identity, request: NewRunbook, now: datetime
) -> RunbookView:
    await require_project(session, org_id=identity.org_id, project_id=request.project_id)
    try:
        checked = _fields(request)
        runbook = await _create(session, identity, request.project_id, checked, now=now)
    except _Refused as refused:
        _raise(refused)
        raise
    return _view(runbook, identity.user, identity=identity)


def _fields(request: RunbookFields) -> _Checked:
    return _checked(
        request.title,
        request.service_name,
        request.content,
        confirmed=request.confirm_credentials,
    )


async def update_runbook(
    session: AsyncSession,
    *,
    identity: Identity,
    runbook_id: uuid.UUID,
    request: RunbookFields,
    now: datetime,
) -> RunbookView:
    runbook = await _own(session, identity, runbook_id)
    try:
        checked = _fields(request)
    except _Refused as refused:
        _raise(refused)
        raise
    runbook.title = checked.title
    runbook.service_name = checked.service_name
    runbook.content = checked.content
    runbook.updated_at = now
    # The old text's chunks go in this transaction, so chat never finds what was replaced.
    await session.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.org_id == identity.org_id,
            KnowledgeChunk.project_id == runbook.project_id,
            KnowledgeChunk.runbook_id == runbook.id,
        )
    )
    chunks = _write_chunks(session, runbook)
    _audit(session, identity, runbook, audit.RUNBOOK_UPDATED, chunks)
    await session.flush()
    return _view(runbook, await _author(session, runbook), identity=identity)


async def delete_runbook(
    session: AsyncSession, *, identity: Identity, runbook_id: uuid.UUID
) -> None:
    runbook = await _own(session, identity, runbook_id)
    _audit(session, identity, runbook, audit.RUNBOOK_DELETED, 0)
    # Its chunks go by their cascading key, in this statement (BR16).
    await session.execute(
        delete(Runbook).where(Runbook.org_id == identity.org_id, Runbook.id == runbook.id)
    )


def title_of(file: UploadedFile) -> str:
    """A file's runbook title: its first top-level heading, or its name without `.md`."""
    heading = _FIRST_TITLE.search(file.content)
    if heading is not None and heading.group(1).strip():
        return heading.group(1).strip()[:MAX_RUNBOOK_TITLE_CHARACTERS]
    return _MARKDOWN_SUFFIX.sub("", file.name.strip())[:MAX_RUNBOOK_TITLE_CHARACTERS]


async def upload(
    session: AsyncSession, *, identity: Identity, request: RunbookUpload, now: datetime
) -> UploadResult:
    """Every file into every project named, as separate copies: one action, refused file by
    file (AC92). A project outside the organization refuses the whole upload."""
    projects: Sequence[uuid.UUID] = list(dict.fromkeys(request.project_ids))
    for project_id in projects:
        await require_project(session, org_id=identity.org_id, project_id=project_id)
    created: list[RunbookSummary] = []
    refused: list[RefusedFile] = []
    for project_id in projects:
        for file in request.files:
            try:
                checked = _checked(
                    title_of(file),
                    request.service_name,
                    file.content,
                    confirmed=request.confirm_credentials,
                )
                runbook = await _create(session, identity, project_id, checked, now=now)
            except _Refused as refusal:
                refused.append(
                    RefusedFile(
                        name=file.name,
                        project_id=project_id,
                        code=refusal.code,
                        detail=refusal.detail,
                    )
                )
                continue
            created.append(_summary(runbook, identity.user))
    return UploadResult(created=created, refused=refused)
