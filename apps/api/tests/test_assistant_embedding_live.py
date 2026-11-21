"""F4 plan T6.1: chat's embedding model and its second connection, as an admin saves them and
an owner agrees (FR26, FR27, AC85; D61), against real Postgres.

Each test runs inside a transaction that is rolled back, so it owns its rows; a check's job
is enqueued in that transaction and never committed, so no worker takes it. The API never
calls a provider: the check is a job on the AI container (F3 AC35), whose own suite is the
scheduler's `test_knowledge_embed.py`.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from assistant_live import AVAILABLE, BASE_URL, NOW, chat_on, db, require_postgres, tenant
from nexovue_platform.ai.embed import EmbeddingSetting
from nexovue_platform.deployment import HOSTED
from nexovue_platform.knowledge import EMBED_CHECK_TASK
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.assistant import embedding, service
from nexovue_api.assistant.schemas import (
    EmbeddingConsent,
    EmbeddingState,
    OwnEmbeddingConnection,
    SaveEmbedding,
)
from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.postgres.models import (
    AIConnection,
    AssistantSettings,
    AuditLogEntry,
    KnowledgeChunk,
)
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError
from nexovue_api.runbooks import service as runbooks
from nexovue_api.runbooks.schemas import NewRunbook
from nexovue_api.signup.service import ProvisionedTenant

require_postgres("chat's embedding settings")

__all__ = ["db"]

OWN = "https://embed.example/v1"
OWN_KEY = "sk-embedding-test-not-real"


async def _ready(db: AsyncSession, name: str) -> tuple[ProvisionedTenant, Identity]:
    made = await tenant(db, name)
    await chat_on(db, made)
    return made, Identity(user=made.user, organization=made.organization)


async def _state(db: AsyncSession, made: ProvisionedTenant) -> EmbeddingState:
    settings = await service.chat_settings(db, org_id=made.organization.id, ai=AVAILABLE, now=NOW)
    return settings.embedding


async def _audits(
    db: AsyncSession, made: ProvisionedTenant, action: str
) -> list[dict[str, object]]:
    rows = await db.scalars(
        select(AuditLogEntry.detail).where(
            AuditLogEntry.org_id == made.organization.id, AuditLogEntry.action == action
        )
    )
    return [dict(detail or {}) for detail in rows]


async def test_a_model_is_validated_saved_and_audited_by_field_names_only(
    db: AsyncSession,
) -> None:
    """AC85: validated before it is stored, audited with field names, and untested until a
    check of exactly these values passes."""
    made, identity = await _ready(db, "EmbedModel")
    with pytest.raises(InvalidRequestError):
        await embedding.save_embedding(
            db, identity=identity, request=SaveEmbedding(model="../escape"), ai=AVAILABLE
        )
    await embedding.save_embedding(
        db, identity=identity, request=SaveEmbedding(model="text-embed"), ai=AVAILABLE
    )
    state = await _state(db, made)
    assert (state.model, state.source, state.check) == ("text-embed", "chat", "not_tested")
    assert state.keyword_only == "not_tested"
    assert await _audits(db, made, embedding.EMBEDDING_CHANGED) == [
        {"fields": ["model"], "host": None}
    ]
    # Saving the same values again changes nothing, and audits nothing.
    await embedding.save_embedding(
        db, identity=identity, request=SaveEmbedding(model="text-embed"), ai=AVAILABLE
    )
    assert len(await _audits(db, made, embedding.EMBEDDING_CHANGED)) == 1


async def test_a_chat_provider_with_no_embeddings_needs_a_second_connection(
    db: AsyncSession,
) -> None:
    """D61: Anthropic has no embedding model."""
    made, identity = await _ready(db, "EmbedAnthropic")
    await db.execute(
        update(AIConnection)
        .where(AIConnection.org_id == made.organization.id)
        .values(wire_format="anthropic_messages")
    )
    with pytest.raises(InvalidRequestError) as refused:
        await embedding.save_embedding(
            db, identity=identity, request=SaveEmbedding(model="text-embed"), ai=AVAILABLE
        )
    assert refused.value.extensions["code"] == "cannot_embed"
    own = OwnEmbeddingConnection(wire_format="openai_chat", base_url=OWN, key=OWN_KEY)
    await embedding.save_embedding(
        db, identity=identity, request=SaveEmbedding(model="text-embed", own=own), ai=AVAILABLE
    )
    assert (await _state(db, made)).source == "own"


async def test_a_second_connection_is_checked_sealed_and_never_shown(db: AsyncSession) -> None:
    """D61, F3 FR23: its address is held to F3's rules; its key is sealed for that address,
    never returned, never in an audit row, and dropped when the address moves."""
    made, identity = await _ready(db, "EmbedOwn")
    for bad in ("http://8.8.8.8/v1", "https://user:pass@embed.example/v1", "ftp://x"):
        with pytest.raises(InvalidRequestError):
            await embedding.save_embedding(
                db,
                identity=identity,
                request=SaveEmbedding(
                    model="m", own=OwnEmbeddingConnection(wire_format="openai_chat", base_url=bad)
                ),
                ai=AVAILABLE,
            )
    with pytest.raises(InvalidRequestError):
        await embedding.save_embedding(
            db,
            identity=identity,
            request=SaveEmbedding(
                model="m",
                own=OwnEmbeddingConnection(wire_format="anthropic_messages", base_url=OWN),
            ),
            ai=AVAILABLE,
        )
    own = OwnEmbeddingConnection(wire_format="openai_chat", base_url=OWN, key=OWN_KEY)
    row = await embedding.save_embedding(
        db, identity=identity, request=SaveEmbedding(model="m", own=own), ai=AVAILABLE
    )
    assert row.sealed_embedding_key is not None and OWN_KEY.encode() not in row.sealed_embedding_key
    state = await _state(db, made)
    assert (state.own_host, state.own_has_key, state.own_wire_format) == (
        "https://embed.example",
        True,
        "openai_chat",
    )
    assert OWN_KEY not in state.model_dump_json()
    [audit] = await _audits(db, made, embedding.EMBEDDING_CHANGED)
    assert audit == {
        "fields": ["address", "key", "model", "wire_format"],
        "host": "https://embed.example",
    }
    moved = OwnEmbeddingConnection(wire_format="openai_chat", base_url="https://other.example/v1")
    row = await embedding.save_embedding(
        db, identity=identity, request=SaveEmbedding(model="m", own=moved), ai=AVAILABLE
    )
    assert (row.sealed_embedding_key, row.embedding_key_id) == (None, None)


async def test_an_owner_agrees_to_the_second_connection_they_read(db: AsyncSession) -> None:
    """D61: its own consent line; refused when the connection moved after it was read."""
    made, identity = await _ready(db, "EmbedConsent")
    own = OwnEmbeddingConnection(wire_format="openai_chat", base_url=OWN, key=OWN_KEY)
    await embedding.save_embedding(
        db, identity=identity, request=SaveEmbedding(model="m", own=own), ai=AVAILABLE
    )
    state = await _state(db, made)
    assert (state.own_consent_current, state.keyword_only) == (False, "no_consent")
    with pytest.raises(ConflictError):
        await embedding.agree_embedding(
            db,
            identity=identity,
            request=EmbeddingConsent(fingerprint="stale"),
            ai=AVAILABLE,
            now=NOW,
        )
    await embedding.agree_embedding(
        db,
        identity=identity,
        request=EmbeddingConsent(fingerprint=state.own_consent_fingerprint),
        ai=AVAILABLE,
        now=NOW,
    )
    assert (await _state(db, made)).own_consent_current is True
    assert await _audits(db, made, embedding.EMBEDDING_AGREED) == [
        {"host": "https://embed.example"}
    ]
    # A new key is a new agreement.
    await embedding.save_embedding(
        db,
        identity=identity,
        request=SaveEmbedding(model="m", own=own.model_copy(update={"key": "sk-another-key"})),
        ai=AVAILABLE,
    )
    assert (await _state(db, made)).own_consent_current is False


async def test_a_check_is_a_job_on_the_ai_container_with_ids_only(db: AsyncSession) -> None:
    """AC85, F3 AC35: the API never calls a provider."""
    made, identity = await _ready(db, "EmbedCheck")
    with pytest.raises(ConflictError):
        await embedding.start_embedding_check(db, identity=identity, ai=AVAILABLE)
    await embedding.save_embedding(
        db, identity=identity, request=SaveEmbedding(model="m"), ai=AVAILABLE
    )
    with pytest.raises(ConflictError):
        await embedding.start_embedding_check(
            db, identity=identity, ai=replace(AVAILABLE, insight_replicas=0)
        )
    await embedding.start_embedding_check(db, identity=identity, ai=AVAILABLE)
    job = (
        await db.execute(
            text("SELECT queue, kwargs FROM jobs WHERE task = :t AND kwargs->>'org_id' = :o"),
            {"t": EMBED_CHECK_TASK, "o": str(made.organization.id)},
        )
    ).one()
    assert job.queue == "insight"
    assert set(job.kwargs) == {"org_id", "requested_by"}
    assert len(await _audits(db, made, embedding.EMBEDDING_TESTED)) == 1


async def test_meaning_search_is_on_only_when_the_check_and_chat_both_allow_it(
    db: AsyncSession,
) -> None:
    """FR27: the settings section says which is on, and why search is keyword only; the
    progress counts what the current model has embedded (FR31)."""
    made, identity = await _ready(db, "EmbedState")
    assert (await _state(db, made)).keyword_only == "no_model"
    await embedding.save_embedding(
        db, identity=identity, request=SaveEmbedding(model="m"), ai=AVAILABLE
    )
    target = EmbeddingSetting(model="m").target(
        chat_wire_format="openai_chat", chat_base_url=BASE_URL, chat_key_id=None
    )
    assert target is not None
    await db.execute(
        update(AssistantSettings)
        .where(AssistantSettings.org_id == made.organization.id)
        .values(
            embedding_checked_fingerprint=target.fingerprint,
            embedding_checked_ok=True,
            embedding_checked_at=NOW,
        )
    )
    await runbooks.create_runbook(
        db,
        identity=identity,
        request=NewRunbook(project_id=made.project.id, title="Pool", content="## Pool\nrestart"),
        now=NOW,
    )
    await db.execute(
        update(KnowledgeChunk)
        .where(KnowledgeChunk.org_id == made.organization.id)
        .values(embedded_model="m", embedding_due=False)
    )
    state = await _state(db, made)
    assert (state.keyword_only, state.check, state.vector_available) == (None, "passed", True)
    assert (state.chunks_embedded, state.chunks_total) == (1, 1)
    await db.execute(
        update(AssistantSettings)
        .where(AssistantSettings.org_id == made.organization.id)
        .values(data_level="facts", consented_data_level="facts")
    )
    assert (await _state(db, made)).keyword_only == "chat_not_ready"
    # A model with no passing check of its own is untested, and has embedded nothing.
    await embedding.save_embedding(
        db, identity=identity, request=SaveEmbedding(model="other"), ai=AVAILABLE
    )
    state = await _state(db, made)
    assert (state.check, state.keyword_only, state.chunks_embedded) == (
        "not_tested",
        "not_tested",
        0,
    )


async def test_on_the_hosted_service_every_embedding_write_is_refused(db: AsyncSession) -> None:
    """F3 AC40: where AI cannot be offered, nothing is saved, agreed or checked."""
    made, identity = await _ready(db, "EmbedHosted")
    hosted = replace(AVAILABLE, deployment=HOSTED)
    with pytest.raises(ConflictError):
        await embedding.save_embedding(
            db, identity=identity, request=SaveEmbedding(model="m"), ai=hosted
        )
    with pytest.raises(ConflictError):
        await embedding.agree_embedding(
            db, identity=identity, request=EmbeddingConsent(fingerprint="x"), ai=hosted, now=NOW
        )
    with pytest.raises(ConflictError):
        await embedding.start_embedding_check(db, identity=identity, ai=hosted)
    assert (await _state(db, made)).model is None
