"""F4 T6.1: chat's embedding model and its optional second connection (FR26, FR27, AC85; D61).

An admin names the embedding model, and may add a connection for embeddings only, with its
own address and key; an owner agrees to that connection separately, since text goes to an
address the chat consent did not name. **Nothing is embedded until a check passes** on exactly
the values saved: the check is a job on the AI container (the API never calls a provider,
F3 AC35), which records its result on the settings row only if those values still stand.

The rules that decide whether texts may go are `EmbeddingSetting.keyword_only`, shared with
the AI role, which reads them again before every call. Keyword search answers whatever this
says (AC41, AC43).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from nexovue_platform.ai.connection import (
    ConnectionRefused,
    check_base_url,
    check_key,
    check_model,
    provider_host,
)
from nexovue_platform.ai.embed import (
    EMBED_FORMATS,
    EMBEDDING_KEY_PURPOSE,
    EmbeddingSetting,
    EmbeddingTarget,
    embedding_key_owner,
)
from nexovue_platform.ai.jobs import EMBED_CHECK_REASONS, embedding_check_request
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, keyring_from
from nexovue_platform.jobs import ENQUEUE, WAKE
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api import insight_account
from nexovue_api.ai import queries as ai_queries
from nexovue_api.ai.service import CONTAINER_NOT_RUNNING, refuse_where_unavailable
from nexovue_api.ai.service import Availability as AIAvailability
from nexovue_api.assistant.schemas import (
    EmbeddingConsent,
    EmbeddingState,
    KeywordOnlyReason,
    SaveEmbedding,
)
from nexovue_api.assistant.standing import consent_matches, settings_of
from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.postgres.models import (
    AIConnection,
    AssistantSettings,
    KnowledgeChunk,
    Project,
)
from nexovue_api.errors.exceptions import (
    ConflictError,
    DependencyUnavailableError,
    InvalidRequestError,
)

AUDIT_TARGET: Final = "assistant_settings"
EMBEDDING_CHANGED: Final = "assistant.embedding_changed"
EMBEDDING_AGREED: Final = "assistant.embedding_disclosure_agreed"
EMBEDDING_TESTED: Final = "assistant.embedding_tested"

NO_MODEL: Final = "Name an embedding model first."
NO_CONNECTION: Final = "Save an AI connection first."
CANNOT_EMBED: Final = (
    "This provider has no embedding model. Add a connection for embeddings only, or leave "
    "the model empty: chat then searches by keyword."
)
OWN_FORMAT: Final = "A connection for embeddings speaks the OpenAI-style or Bedrock format."
NO_OWN_CONNECTION: Final = "There is no connection for embeddings to agree to."
CONSENT_MOVED: Final = (
    "The connection for embeddings changed after you read it. Read it again, then agree."
)
NO_AI_KEY: Final = "AI_SECRET_KEY is not set on this server, so a key cannot be saved."


def setting_of(row: AssistantSettings) -> EmbeddingSetting:
    return EmbeddingSetting(
        model=row.embedding_model,
        own_wire_format=row.embedding_wire_format,
        own_base_url=row.embedding_base_url,
        own_key_id=None if row.embedding_key_id is None else str(row.embedding_key_id),
        own_consented_fingerprint=row.embedding_consented_fingerprint,
        checked_fingerprint=row.embedding_checked_fingerprint,
        checked_ok=row.embedding_checked_ok,
    )


def _target(setting: EmbeddingSetting, connection: AIConnection | None) -> EmbeddingTarget | None:
    if connection is None and setting.own_base_url is None:
        return None
    return setting.target(
        chat_wire_format=connection.wire_format if connection else "",
        chat_base_url=connection.base_url if connection else "",
        chat_key_id=None
        if connection is None or connection.key_id is None
        else str(connection.key_id),
    )


def _chat_ready(row: AssistantSettings, connection: AIConnection | None) -> bool:
    """Texts go only with chat on, at *facts and text*, with an owner's consent standing."""
    return (
        connection is not None
        and row.enabled
        and row.data_level == "facts_and_text"
        and consent_matches(row, connection)
    )


async def embedding_state(
    session: AsyncSession, *, row: AssistantSettings, connection: AIConnection | None
) -> EmbeddingState:
    setting = setting_of(row)
    target = _target(setting, connection)
    vector = await insight_account.vector_columns_present(session)
    reason: KeywordOnlyReason | None = None
    if not vector:
        reason = "no_extension"
    elif (blocked := setting.keyword_only(target)) is not None:
        reason = blocked
    elif not _chat_ready(row, connection):
        reason = "chat_not_ready"
    embedded, total = await _chunk_counts(session, row)
    check = (
        "not_tested"
        if row.embedding_checked_at is None
        or target is None
        or row.embedding_checked_fingerprint != target.fingerprint
        else ("passed" if row.embedding_checked_ok else "failed")
    )
    return EmbeddingState(
        model=row.embedding_model,
        source=None if target is None else target.source,
        own_base_url=row.embedding_base_url,
        own_host=None if row.embedding_base_url is None else provider_host(row.embedding_base_url),
        own_wire_format=row.embedding_wire_format,
        own_has_key=row.sealed_embedding_key is not None,
        own_consent_fingerprint=setting.own_consent_fingerprint(),
        own_consent_current=setting.own_consent_fingerprint() is not None
        and setting.own_consented_fingerprint == setting.own_consent_fingerprint(),
        check=check,
        check_reason=row.embedding_check_reason
        if check == "failed" and row.embedding_check_reason in EMBED_CHECK_REASONS
        else None,
        checked_at=row.embedding_checked_at if check != "not_tested" else None,
        vector_available=vector,
        keyword_only=reason,
        chunks_embedded=embedded,
        chunks_total=total,
    )


async def _chunk_counts(session: AsyncSession, row: AssistantSettings) -> tuple[int, int]:
    """Chunks of the organization's live projects searchable by meaning with the current
    model, and all of them: re-embedding's progress (FR31)."""
    current = (KnowledgeChunk.embedded_model == row.embedding_model) & ~KnowledgeChunk.embedding_due
    found = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(case((current, 1), else_=0)), 0))
            .select_from(KnowledgeChunk)
            .join(
                Project,
                (Project.org_id == KnowledgeChunk.org_id)
                & (Project.id == KnowledgeChunk.project_id),
            )
            .where(KnowledgeChunk.org_id == row.org_id, Project.deleted_at.is_(None))
        )
    ).one()
    total, embedded = int(found[0]), int(found[1])
    return (embedded if row.embedding_model is not None else 0), total


def _record(
    session: AsyncSession, identity: Identity, action: str, detail: Mapping[str, Any]
) -> None:
    audit.record_ai_event(
        session,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        action=action,
        target_id=identity.org_id,
        detail=dict(detail),
        target_kind=AUDIT_TARGET,
    )


def _box(keys: ConfiguredKeys) -> SealedBox:
    if not keys.current:
        raise DependencyUnavailableError(NO_AI_KEY)
    return SealedBox(keyring_from(keys.current, keys.previous), purpose=EMBEDDING_KEY_PURPOSE)


def _refuse(refusal: ConnectionRefused) -> InvalidRequestError:
    return InvalidRequestError(str(refusal), code=f"embedding_{refusal.reason}")


async def save_embedding(
    session: AsyncSession, *, identity: Identity, request: SaveEmbedding, ai: AIAvailability
) -> AssistantSettings:
    """Validated against F3's rules (FR23) before anything is stored, and audited with field
    names and the host only (AC85). A changed value is untested until a new check passes,
    because the check's fingerprint no longer matches. Refused where AI cannot be offered."""
    await refuse_where_unavailable(session, ai)
    connection = await ai_queries.connection_of(session, org_id=identity.org_id)
    try:
        if request.model is not None:
            check_model(request.model)
        if request.own is not None:
            if request.own.wire_format not in EMBED_FORMATS:
                raise InvalidRequestError(OWN_FORMAT, code="embedding_wire_format")
            check_base_url(request.own.base_url)
            if request.own.key is not None:
                check_key(request.own.key)
    except ConnectionRefused as refused:
        raise _refuse(refused) from refused
    if request.model is not None and request.own is None:
        if connection is None:
            raise ConflictError(NO_CONNECTION, code="no_connection")
        if connection.wire_format not in EMBED_FORMATS:
            raise InvalidRequestError(CANNOT_EMBED, code="cannot_embed")
    row = await settings_of(session, org_id=identity.org_id)
    if row is None:
        row = AssistantSettings(org_id=identity.org_id, data_level="facts")
        session.add(row)
        await session.flush()
    before = _shown(row)
    row.embedding_model = request.model
    _set_own(row, request, ai.keys)
    changed = sorted(name for name, value in _shown(row).items() if before.get(name) != value)
    if changed:
        _record(
            session,
            identity,
            EMBEDDING_CHANGED,
            {
                "fields": changed,
                "host": None
                if row.embedding_base_url is None
                else provider_host(row.embedding_base_url),
            },
        )
    await session.flush()
    return row


def _shown(row: AssistantSettings) -> dict[str, Any]:
    """What an audit row may name as changed: never the key, only that it changed."""
    return {
        "model": row.embedding_model,
        "wire_format": row.embedding_wire_format,
        "address": row.embedding_base_url,
        "key": row.embedding_key_id,
    }


def _set_own(row: AssistantSettings, request: SaveEmbedding, keys: ConfiguredKeys) -> None:
    own = request.own
    if own is None:
        row.embedding_wire_format = row.embedding_base_url = None
        row.sealed_embedding_key = row.embedding_key_id = None
        return
    moved = own.base_url != row.embedding_base_url or own.wire_format != row.embedding_wire_format
    row.embedding_wire_format, row.embedding_base_url = own.wire_format, own.base_url
    if own.key is not None:
        # A new key with a new id, so the owner's consent and the check both move with it.
        row.embedding_key_id = uuid.uuid4()
        row.sealed_embedding_key = _box(keys).seal(
            own.key, owner=embedding_key_owner(str(row.org_id), own.base_url)
        )
    elif moved:
        # Sealed for the old address, it could not open for this one (as F3's key).
        row.sealed_embedding_key = row.embedding_key_id = None


async def agree_embedding(
    session: AsyncSession,
    *,
    identity: Identity,
    request: EmbeddingConsent,
    ai: AIAvailability,
    now: datetime,
) -> AssistantSettings:
    """An owner agrees to the second connection they read: refused if it moved since."""
    await refuse_where_unavailable(session, ai)
    row = await settings_of(session, org_id=identity.org_id)
    wanted = None if row is None else setting_of(row).own_consent_fingerprint()
    if row is None or wanted is None:
        raise ConflictError(NO_OWN_CONNECTION, code="no_embedding_connection")
    if request.fingerprint != wanted:
        raise ConflictError(CONSENT_MOVED, code="consent_moved")
    row.embedding_consented_fingerprint = wanted
    row.embedding_consented_at = now
    assert row.embedding_base_url is not None
    _record(session, identity, EMBEDDING_AGREED, {"host": provider_host(row.embedding_base_url)})
    await session.flush()
    return row


async def start_embedding_check(
    session: AsyncSession, *, identity: Identity, ai: AIAvailability
) -> None:
    """A check, as a job on the AI container, in this transaction with its audit row; the
    workers are woken when it commits."""
    await refuse_where_unavailable(session, ai)
    row = await settings_of(session, org_id=identity.org_id)
    if row is None or row.embedding_model is None:
        raise ConflictError(NO_MODEL, code="no_embedding_model")
    if not ai.container_running:
        raise ConflictError(CONTAINER_NOT_RUNNING, code="container_not_running")
    job = embedding_check_request(org_id=str(identity.org_id), requested_by=str(identity.user.id))
    await session.execute(text(ENQUEUE), job.parameters())
    await session.execute(text(WAKE), job.wake_parameters())
    _record(
        session, identity, EMBEDDING_TESTED, {"source": "own" if row.embedding_base_url else "chat"}
    )
