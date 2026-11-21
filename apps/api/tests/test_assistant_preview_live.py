"""F4 plan T2.12, AC2, FR24: chat's consent preview, `GET /ai/preview?purpose=chat`.

Against real Postgres, in a rolled-back transaction. The project's newest log line comes from
a stand-in repository returning one stored row, so what is held is chat's own part: the row
goes through the logs route's own mapping, then F4's send-time masks, `bounded` and F3's
fence, as the worker sends it; with no logs, labelled samples, and the preview still reads.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from assistant_live import NOW, chat_on, db, require_postgres, tenant
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.ai.service import consent_fingerprint_of
from nexovue_api.assistant.preview import chat_preview
from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.postgres.models import AssistantSettings, KnowledgeChunk, Project
from nexovue_api.errors.exceptions import ConflictError
from nexovue_api.runbooks import service as runbooks
from nexovue_api.runbooks.schemas import NewRunbook

require_postgres("chat's preview suite")

__all__ = ["db"]

PLANTED_TOKEN = "abcdefgh12345678"
STORED_ROW: Mapping[str, Any] = {
    "row_key": 1,
    "observed_nanos": 1_762_950_000_000_000_000,
    "service_name": "checkout",
    "severity": "ERROR",
    "severity_number": 17,
    "body": f"card declined, Authorization: Bearer {PLANTED_TOKEN} for ada@example.com",
    "trace_ref": "",
    "span_ref": "",
    "attributes_json": "{}",
}


class _Logs:
    """The project's ClickHouse, holding `rows`; it records that a read happened."""

    def __init__(self, rows: list[Mapping[str, Any]]) -> None:
        self.rows = rows
        self.read = 0

    async def query(self, _sql: str, _parameters: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        self.read += 1
        return self.rows


async def _project(db: AsyncSession, org_id: Any) -> Project:
    found = (await db.scalars(select(Project).where(Project.org_id == org_id))).first()
    assert found is not None
    return found


async def test_the_preview_shows_a_real_log_line_masked_and_fenced_as_it_would_be_sent(
    db: AsyncSession,
) -> None:
    made = await tenant(db)
    connection = await chat_on(db, made)
    project = await _project(db, made.organization.id)
    await db.execute(
        update(Project)
        .where(Project.id == project.id)
        .values(masking_config={"disable": ["cookies"]})
    )

    preview = await chat_preview(
        db,
        _Logs([STORED_ROW]),  # type: ignore[arg-type]
        org_id=made.organization.id,
        project_id=project.id,
        now=NOW,
    )

    by_kind = {sample.kind: sample for sample in preview.samples}
    log = by_kind["Reading logs: a log line"]
    assert not log.labelled_sample
    assert log.sent.startswith("<untrusted-") and 'label="step s1 result"' in log.sent
    assert "card declined" in log.sent
    assert PLANTED_TOKEN not in log.sent and "ada@example.com" not in log.sent
    assert "[masked]" in log.sent
    assert not by_kind["The service in view"].labelled_sample
    assert json.dumps(project.name)[1:-1] in by_kind["The project's name"].sent
    kinds = {(kind.kind, kind.level) for kind in preview.kinds}
    assert ("Reading logs: body", "facts_and_text") in kinds
    assert ("Your question", "facts") in kinds
    assert any(kind.startswith("Earlier questions") for kind, _ in kinds)
    # Step 6 (AC2): text sent for embedding is in the complete list, chunks and phrases both.
    assert any(kind.startswith("Runbook text and fact lines, to be embedded") for kind, _ in kinds)
    assert any(kind.startswith("Chat's search phrases, to be embedded") for kind, _ in kinds)
    assert preview.masking_rules_off == ["cookies"]
    assert preview.sent_for_embedding == 0
    assert preview.consent_fingerprint == consent_fingerprint_of(connection)
    assert preview.address == connection.base_url


async def test_with_no_logs_the_preview_shows_labelled_samples_and_still_reads(
    db: AsyncSession,
) -> None:
    made = await tenant(db)
    await chat_on(db, made)
    project = await _project(db, made.organization.id)
    logs = _Logs([])

    preview = await chat_preview(
        db,
        logs,  # type: ignore[arg-type]
        org_id=made.organization.id,
        project_id=project.id,
        now=NOW,
    )

    assert logs.read == 1
    by_kind = {sample.kind: sample for sample in preview.samples}
    assert by_kind["Reading logs: a log line"].labelled_sample
    assert by_kind["The service in view"].labelled_sample
    # The built-in sample is masked like a real one.
    assert "abcdefgh12345678" not in by_kind["Reading logs: a log line"].sent


async def test_with_an_embedding_model_the_preview_counts_every_chunk_sent_at_once(
    db: AsyncSession,
) -> None:
    """FR24, AC2: the bulk embedding count, the project's chunks, once a model is set."""
    made = await tenant(db, "PreviewEmbedding")
    await chat_on(db, made)
    project = await _project(db, made.organization.id)
    await runbooks.create_runbook(
        db,
        identity=Identity(user=made.user, organization=made.organization),
        request=NewRunbook(
            project_id=project.id, title="Pool", content="## One\nfirst part\n## Two\nsecond part"
        ),
        now=NOW,
    )
    chunks = await db.scalar(
        select(func.count()).where(KnowledgeChunk.org_id == made.organization.id)
    )
    assert chunks

    def preview() -> Any:
        return chat_preview(
            db,
            _Logs([]),  # type: ignore[arg-type]
            org_id=made.organization.id,
            project_id=project.id,
            now=NOW,
        )

    assert (await preview()).sent_for_embedding == 0
    await db.execute(
        update(AssistantSettings)
        .where(AssistantSettings.org_id == made.organization.id)
        .values(embedding_model="text-embed")
    )
    assert (await preview()).sent_for_embedding == chunks


async def test_without_a_connection_there_is_nothing_to_preview(db: AsyncSession) -> None:
    made = await tenant(db)
    project = await _project(db, made.organization.id)

    with pytest.raises(ConflictError):
        await chat_preview(
            db,
            _Logs([]),  # type: ignore[arg-type]
            org_id=made.organization.id,
            project_id=project.id,
            now=NOW,
        )
