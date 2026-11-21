"""F4 plan T5.1, T5.2, T7.1 and T7.2: runbooks and their chunks, notes' chunks, and Save as
runbook, against real Postgres (AC38, AC39, AC69, AC71, AC90, AC92, AC95, AC106).

Each test runs inside a transaction that is rolled back (`assistant_live.db`), so it owns its
rows and the stack's sweeps never see them. Clocks are pinned (`NOW`).
"""

from __future__ import annotations

import ast
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Final

import pytest
from assistant_live import NOW, db, require_postgres, tenant
from nexovue_platform.knowledge import (
    MAX_RUNBOOK_BYTES,
    MAX_RUNBOOK_CHUNKS,
    MAX_RUNBOOKS_PER_PROJECT,
)
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.dependencies import Identity
from nexovue_api.auth.sessions import Impersonation
from nexovue_api.data.postgres.models import (
    AuditLogEntry,
    Finding,
    FindingNote,
    KnowledgeChunk,
    Project,
    Runbook,
    User,
)
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.runbooks import router as runbooks_router
from nexovue_api.runbooks import service
from nexovue_api.runbooks.schemas import (
    NewRunbook,
    NoteToRunbook,
    RunbookFields,
    RunbookUpload,
    UploadedFile,
)
from nexovue_api.signup.service import ProvisionedTenant

require_postgres("runbooks")

# The rolled-back session, as a fixture.
__all__ = ["db"]

PLANTED: Final = "planted-runbook-words-8c1"


def _identity(made: ProvisionedTenant, user: User | None = None) -> Identity:
    return Identity(user=user or made.user, organization=made.organization)


def _text(*sections: str) -> str:
    return "\n".join(f"## {section}\n" + f"{section} step. " * 40 for section in sections)


async def _save(
    db: AsyncSession, made: ProvisionedTenant, content: str, *, title: str = "Checkout pool"
) -> uuid.UUID:
    view = await service.create_runbook(
        db,
        identity=_identity(made),
        request=NewRunbook(project_id=made.project.id, title=title, content=content),
        now=NOW,
    )
    return view.id


async def _chunks(db: AsyncSession, runbook_id: uuid.UUID) -> list[KnowledgeChunk]:
    return list(
        await db.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.runbook_id == runbook_id)
            .order_by(KnowledgeChunk.position)
        )
    )


async def test_a_runbook_saves_with_its_chunks_split_by_heading(db: AsyncSession) -> None:
    """AC90, FR30: written in the runbook's own transaction, one chunk a section."""
    made = await tenant(db, "Runbooks")
    runbook_id = await _save(db, made, _text("Pool", "Restart", "Check"))

    chunks = await _chunks(db, runbook_id)
    assert [chunk.position for chunk in chunks] == [0, 1, 2]
    assert [chunk.content.splitlines()[0] for chunk in chunks] == [
        "## Pool",
        "## Restart",
        "## Check",
    ]
    assert {(chunk.source_kind, chunk.project_id) for chunk in chunks} == {
        ("runbook", made.project.id)
    }


async def test_a_runbook_of_short_headings_makes_at_most_18_chunks_and_200_fit(
    db: AsyncSession,
) -> None:
    """AC106: 32 KiB of nothing but headings saves, under the ceiling; 200 stay at 3,600 or
    fewer; and the 201st is refused in a sentence (AC90)."""
    made = await tenant(db, "Headings")
    headings = "# a\n" * (MAX_RUNBOOK_BYTES // 4)
    assert len(headings.encode()) == MAX_RUNBOOK_BYTES
    for number in range(MAX_RUNBOOKS_PER_PROJECT):
        await _save(db, made, headings, title=f"Headings {number}")

    per_runbook = (
        await db.execute(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.org_id == made.organization.id)
            .group_by(KnowledgeChunk.runbook_id)
        )
    ).scalars()
    counts = list(per_runbook)
    assert len(counts) == MAX_RUNBOOKS_PER_PROJECT
    assert max(counts) <= MAX_RUNBOOK_CHUNKS
    assert sum(counts) <= MAX_RUNBOOKS_PER_PROJECT * MAX_RUNBOOK_CHUNKS == 3_600
    with pytest.raises(ConflictError) as refused:
        await _save(db, made, "one more", title="The 201st")
    assert refused.value.extensions["code"] == "runbook_cap"
    assert refused.value.detail == (
        "This project holds 200 runbooks, the most it can. Delete one to add another."
    )


async def test_a_runbook_over_32_kib_is_refused_in_a_sentence_and_nothing_is_kept(
    db: AsyncSession,
) -> None:
    made = await tenant(db, "Large")
    with pytest.raises(InvalidRequestError) as refused:
        await _save(db, made, "x" * (MAX_RUNBOOK_BYTES + 1))
    assert refused.value.extensions["code"] == "runbook_too_large"
    assert refused.value.detail == "A runbook can be at most 32 KiB."
    assert (
        await db.scalar(
            select(func.count()).select_from(Runbook).where(Runbook.org_id == made.organization.id)
        )
        == 0
    )


async def test_text_that_looks_like_a_credential_warns_and_a_second_click_saves(
    db: AsyncSession,
) -> None:
    """AC69, FR32: the same forms FR40's mask hides before sending."""
    made = await tenant(db, "Secrets")
    content = "Connect with password: hunter2-correct-horse and restart the pool."
    with pytest.raises(InvalidRequestError) as warned:
        await _save(db, made, content)
    assert warned.value.extensions["code"] == "looks_like_credential"
    assert "Do not paste secrets" in warned.value.detail

    view = await service.create_runbook(
        db,
        identity=_identity(made),
        request=NewRunbook(
            project_id=made.project.id,
            title="Pool",
            content=content,
            confirm_credentials=True,
        ),
        now=NOW,
    )
    assert view.content == content


async def test_each_write_is_audited_without_its_text(db: AsyncSession) -> None:
    """AC71: `runbook.created`, `runbook.updated`, `runbook.deleted`, and no word of it."""
    made = await tenant(db, "Audited")
    runbook_id = await _save(db, made, f"{PLANTED} restart the pool", title=f"{PLANTED} title")
    await service.update_runbook(
        db,
        identity=_identity(made),
        runbook_id=runbook_id,
        request=RunbookFields(title=f"{PLANTED} again", content=f"{PLANTED} rewritten"),
        now=NOW + timedelta(minutes=1),
    )
    await service.delete_runbook(db, identity=_identity(made), runbook_id=runbook_id)

    entries = list(
        await db.scalars(
            select(AuditLogEntry)
            .where(
                AuditLogEntry.org_id == made.organization.id,
                AuditLogEntry.action.like("runbook.%"),
            )
            .order_by(AuditLogEntry.id)
        )
    )
    assert [entry.action for entry in entries] == [
        "runbook.created",
        "runbook.updated",
        "runbook.deleted",
    ]
    assert {entry.target for entry in entries} == {f"runbook:{runbook_id}"}
    assert all(PLANTED not in str(entry.detail) for entry in entries)
    assert entries[0].detail is not None and entries[0].detail["chunks"] == 1


async def test_deleting_or_rewriting_a_runbook_takes_its_chunks_in_the_same_transaction(
    db: AsyncSession,
) -> None:
    """AC38, BR16: by the cascading key, before anything commits."""
    made = await tenant(db, "Deleted")
    runbook_id = await _save(db, made, f"## Old\n{PLANTED} old text")
    await service.update_runbook(
        db,
        identity=_identity(made),
        runbook_id=runbook_id,
        request=RunbookFields(title="Pool", content="## New\nnew text"),
        now=NOW,
    )
    assert [chunk.content for chunk in await _chunks(db, runbook_id)] == ["## New\nnew text"]

    await service.delete_runbook(db, identity=_identity(made), runbook_id=runbook_id)
    assert await _chunks(db, runbook_id) == []


async def test_deleting_a_finding_takes_its_fact_line(db: AsyncSession) -> None:
    """AC38: a fact line goes with its finding, by any path, the sweep's bulk delete too."""
    made = await tenant(db, "Facts")
    finding = Finding(
        org_id=made.organization.id,
        project_id=made.project.id,
        scope="service",
        service_name="checkout",
        severity="warning",
        state="closed",
        closed_reason="recovered",
        title="checkout is slower",
        signals=[{"signal": "slower"}],
        started_at=NOW - timedelta(hours=1),
        closed_at=NOW,
    )
    db.add(finding)
    await db.flush()
    db.add(
        KnowledgeChunk(
            org_id=made.organization.id,
            project_id=made.project.id,
            source_kind="fact",
            finding_id=finding.id,
            content="service checkout | signal slower",
            fact_service="checkout",
            fact_signal="slower",
            fact_day=NOW.date(),
        )
    )
    await db.flush()

    await db.execute(delete(Finding).where(Finding.id == finding.id))
    left = await db.scalar(
        select(func.count())
        .select_from(KnowledgeChunk)
        .where(KnowledgeChunk.finding_id == finding.id)
    )
    assert left == 0


async def test_a_chunk_cannot_name_a_runbook_of_another_project(db: AsyncSession) -> None:
    """The database refuses it (the composite key), so a project's search never meets
    another project's runbook (AC42)."""
    made = await tenant(db, "Projects")
    runbook_id = await _save(db, made, "restart the pool")
    other = Project(org_id=made.organization.id, slug=f"p-{uuid.uuid4().hex[:8]}", name="Other")
    db.add(other)
    await db.flush()
    db.add(
        KnowledgeChunk(
            org_id=made.organization.id,
            project_id=other.id,
            source_kind="runbook",
            runbook_id=runbook_id,
            content="smuggled",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_a_viewer_reads_a_runbook_and_a_support_view_sees_no_body(db: AsyncSession) -> None:
    """AC92, FR32, D48."""
    made = await tenant(db, "Readers")
    runbook_id = await _save(db, made, "restart the pool")
    viewer = User(
        org_id=made.organization.id, email=f"viewer-{uuid.uuid4()}@example.test", role="viewer"
    )
    db.add(viewer)
    await db.flush()

    read = await service.read_runbook(db, identity=_identity(made, viewer), runbook_id=runbook_id)
    assert read.content == "restart the pool"
    support = Identity(
        user=made.user,
        organization=made.organization,
        impersonation=Impersonation(
            grant_id=str(uuid.uuid4()),
            impersonation_id=str(uuid.uuid4()),
            operator_label="support",
            reason="a customer asked",
            until=NOW + timedelta(hours=1),
        ),
    )
    listed = await service.list_runbooks(db, identity=support, project_id=made.project.id)
    assert [summary.id for summary in listed.runbooks] == [runbook_id]
    seen = await service.read_runbook(db, identity=support, runbook_id=runbook_id)
    assert seen.content is None
    assert seen.title == "Checkout pool"


async def test_another_organization_s_runbook_is_not_found(db: AsyncSession) -> None:
    made = await tenant(db, "Owner")
    stranger = await tenant(db, "Stranger")
    runbook_id = await _save(db, made, "restart the pool")
    with pytest.raises(NotFoundError):
        await service.read_runbook(db, identity=_identity(stranger), runbook_id=runbook_id)
    with pytest.raises(NotFoundError):
        await service.delete_runbook(db, identity=_identity(stranger), runbook_id=runbook_id)
    with pytest.raises(NotFoundError):
        await service.list_runbooks(db, identity=_identity(stranger), project_id=made.project.id)


async def test_uploading_30_files_imports_them_in_one_action_with_per_file_errors(
    db: AsyncSession,
) -> None:
    """AC92: separate copies in each project named, refused file by file."""
    made = await tenant(db, "Upload")
    second = Project(org_id=made.organization.id, slug=f"s-{uuid.uuid4().hex[:8]}", name="Staging")
    db.add(second)
    await db.flush()
    good = [UploadedFile(name=f"r{n}.md", content=f"# Runbook {n}\nrestart") for n in range(28)]
    files = [
        *good,
        UploadedFile(name="big.md", content="x" * (MAX_RUNBOOK_BYTES + 1)),
        UploadedFile(name="empty.md", content="   "),
    ]

    result = await service.upload(
        db,
        identity=_identity(made),
        request=RunbookUpload(project_ids=[made.project.id, second.id], files=files),
        now=NOW,
    )

    assert len(result.created) == 2 * 28
    assert {summary.title for summary in result.created} == {f"Runbook {n}" for n in range(28)}
    assert sorted((refused.name, refused.code) for refused in result.refused) == sorted(
        [("big.md", "runbook_too_large"), ("empty.md", "runbook_empty")] * 2
    )
    for project_id in (made.project.id, second.id):
        assert (
            await db.scalar(
                select(func.count()).select_from(Runbook).where(Runbook.project_id == project_id)
            )
            == 28
        )


async def test_runbooks_are_absent_where_ai_is_not_offered(db: AsyncSession) -> None:
    """AC92: on the hosted service the routes answer as no route does, so no chunk is ever
    written; every handler asks first."""
    with pytest.raises(NotFoundError):
        service.require_offered("hosted")
    service.require_offered("self-hosted")
    tree = ast.parse(Path(runbooks_router.__file__).read_text(encoding="utf-8"))
    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.decorator_list
    ]
    assert len(handlers) == 7
    for handler in handlers:
        first = (
            handler.body[1]
            if isinstance(handler.body[0], ast.Expr)
            and isinstance(handler.body[0].value, ast.Constant)
            else handler.body[0]
        )
        assert "require_offered" in ast.unparse(first), handler.name


# ------------------------------------------------------------- Step 7: notes and cases


def _case(made: ProvisionedTenant, *, state: str = "resolved") -> Finding:
    resolved = state == "resolved"
    return Finding(
        org_id=made.organization.id,
        project_id=made.project.id,
        scope="service",
        service_name="checkout",
        severity="warning",
        state="closed",
        closed_reason="recovered",
        title=PLANTED,
        signals=[{"signal": "slower"}],
        started_at=NOW - timedelta(hours=1),
        closed_at=NOW - timedelta(minutes=10),
        case_state=state,
        case_resolution="resolved" if resolved else None,
        case_opened_at=NOW - timedelta(minutes=50),
        case_activity_at=NOW,
        case_resolved_at=NOW if resolved else None,
    )


async def _noted(
    db: AsyncSession, made: ProvisionedTenant, body: str, *, state: str = "resolved"
) -> tuple[Finding, FindingNote]:
    finding = _case(made, state=state)
    db.add(finding)
    await db.flush()
    note = FindingNote(
        org_id=made.organization.id,
        finding_id=finding.id,
        author_id=made.user.id,
        body=body,
        created_at=NOW - timedelta(minutes=20),
    )
    db.add(note)
    await db.flush()
    return finding, note


async def test_deleting_a_note_takes_its_chunk_and_a_chunk_names_its_own_organization_s(
    db: AsyncSession,
) -> None:
    """AC38, BR16: a note's chunk goes with it in one statement; the database refuses a chunk
    naming another organization's note (the composite key, D34)."""
    made = await tenant(db, "NoteChunks")
    stranger = await tenant(db, "NoteStranger")
    _, note = await _noted(db, made, "raised the pool")
    db.add(
        KnowledgeChunk(
            org_id=made.organization.id,
            project_id=made.project.id,
            source_kind="note",
            note_id=note.id,
            content=note.body,
        )
    )
    await db.flush()

    await db.execute(delete(FindingNote).where(FindingNote.id == note.id))
    left = await db.scalar(
        select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.note_id == note.id)
    )
    assert left == 0

    _, theirs = await _noted(db, stranger, "theirs")
    async with db.begin_nested():
        db.add(
            KnowledgeChunk(
                org_id=made.organization.id,
                project_id=made.project.id,
                source_kind="note",
                note_id=theirs.id,
                content="smuggled",
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()


async def test_save_as_runbook_copies_a_resolved_case_s_note_and_its_line_and_outlives_it(
    db: AsyncSession,
) -> None:
    """AC95, FR41: a person's click copies the note and the case's fact line, composed from
    columns alone (AC39: no finding title), into a runbook this person wrote, chunked for
    search; it survives the finding's deletion, which the expiry sweep does at retention."""
    made = await tenant(db, "SaveAsRunbook")
    finding, note = await _noted(db, made, "Raised the pool to 40, then restarted pgbouncer.")

    view = await service.save_note_as_runbook(
        db, identity=_identity(made), request=NoteToRunbook(note_id=note.id), now=NOW
    )

    assert (view.project_id, view.service_name, view.author) == (
        made.project.id,
        "checkout",
        made.user.name or made.user.email,
    )
    assert view.title == f"From a resolved case on checkout, {NOW:%Y-%m-%d}"
    assert view.content is not None
    assert view.content.startswith("Raised the pool to 40, then restarted pgbouncer.\n")
    assert "Saved as a runbook by a person." in view.content
    assert (
        f"service checkout | signal slower | {NOW - timedelta(hours=1):%Y-%m-%d %H:%M} to "
        f"{NOW:%Y-%m-%d %H:%M} UTC | closed: case resolved"
    ) in view.content
    assert PLANTED not in view.content and PLANTED not in view.title
    assert await _chunks(db, view.id)

    await db.execute(delete(Finding).where(Finding.id == finding.id))
    kept = await db.scalar(select(Runbook).where(Runbook.id == view.id))
    assert kept is not None and kept.created_by == made.user.id


async def test_save_as_runbook_refuses_an_active_case_another_s_note_and_asks_twice_for_a_secret(
    db: AsyncSession,
) -> None:
    """FR41 offers it on a resolved case only; another organization's note is one that does
    not exist; a note holding a credential form needs the second click (AC69)."""
    made = await tenant(db, "SaveRefusals")
    stranger = await tenant(db, "SaveStranger")
    _, active = await _noted(db, made, "still looking", state="investigating")
    _, theirs = await _noted(db, stranger, "their fix")
    _, secret = await _noted(db, made, "used password=hunter2 to get in")

    with pytest.raises(ConflictError) as refused:
        await service.save_note_as_runbook(
            db, identity=_identity(made), request=NoteToRunbook(note_id=active.id), now=NOW
        )
    assert refused.value.extensions["code"] == "case_not_resolved"
    with pytest.raises(NotFoundError):
        await service.save_note_as_runbook(
            db, identity=_identity(made), request=NoteToRunbook(note_id=theirs.id), now=NOW
        )
    with pytest.raises(InvalidRequestError) as warned:
        await service.save_note_as_runbook(
            db, identity=_identity(made), request=NoteToRunbook(note_id=secret.id), now=NOW
        )
    assert warned.value.extensions["code"] == "looks_like_credential"
    saved = await service.save_note_as_runbook(
        db,
        identity=_identity(made),
        request=NoteToRunbook(note_id=secret.id, confirm_credentials=True),
        now=NOW,
    )
    assert saved.id is not None
