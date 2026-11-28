"""F4 T6.1: chat's embeddings, run by `scheduler-insight` alone, as `nexovue_insight` (design,
section 6, *Embedding*; ADR-067; ADR-079).

Two tasks on queue `insight`, in `TASK_SETS["insight"]`:

- **`embed_chunks`**, started by beat (`EMBED_SWEEP_ENTRY`). Per organization: first it nulls
  the vectors of projects now opted out (AC70); then, only where the embedding model is set,
  checked and agreed to, chat on at *facts and text* with an owner's consent, AI on with a
  monthly limit, and the organization not deleted, it sends due chunks of live projects that
  are not opted out, masked as FR40 says, in the format's own batches. A chunk is due when it
  is new, or embedded by another model (re-embedding, FR31). **Everything is read again before
  each call** (FR25).
- **`check_embedding`**, a person's click (AC85): one real call with a constant text, whose
  result lands on the settings row only if the values it tested still stand.

**Embedding is spending** (F3's limits, ADR-079): each call takes its estimate from the
month under chat's share (`reserve_month`, `limits.chat_ceiling`) in the same transaction as
its `ai_runs` row (purpose `embed`), before any byte is sent, and settles through
`store.settle_run`. A run F3's sweep already called `died` settles nothing and writes no
vector (`docs/changes/wording-settles-once/`). **A failure leaves the chunk due,** so keyword
search still finds it (design, *Failure handling*), and the organization waits for the next run.

Statements bind `org_id`, but `_ORGANIZATIONS`, which lists whose settings could need work,
the one cross-tenant read, named in the tenancy structure test. The account reads a chunk's
text and whose it is, never a vector (`insight_account.VECTOR_GRANTS`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from nexovue_platform.ai.connection import (
    AI_CONNECTION_PURPOSE,
    ConnectionRefused,
    check_base_url,
    check_key,
    check_model,
    consent_fingerprint,
    key_owner,
    provider_host,
)
from nexovue_platform.ai.contract import estimate_tokens
from nexovue_platform.ai.embed import (
    EMBEDDING_KEY_PURPOSE,
    OWN_SOURCE,
    EmbeddingSetting,
    EmbeddingTarget,
    EmbedResponse,
    embedding_key_owner,
    vector_literal,
)
from nexovue_platform.ai.jobs import CHECK_CALL_SECONDS
from nexovue_platform.ai.limits import chat_ceiling, month_of
from nexovue_platform.assistant_masks import mask
from nexovue_platform.assistant_records import CHAT_DISCLOSURE_VERSION
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, SealError, keyring_from
from nexovue_platform.evidence.model_facts import SCHEMA_VERSION
from nexovue_platform.knowledge import (
    EMBED_CHECK_TASK,
    EMBED_SWEEP_CALLS,
    EMBED_SWEEP_CHUNKS,
    EMBED_SWEEP_TASK,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.insight import store, transport
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.probes.addresses import PrivateEndpoints
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# What a run row names as its prompt: the embedding request's shape, raised if it changes.
EMBED_VERSION: Final = "embed-1"
# What a check embeds: nexovue's own words, no customer's (as F3's check, AC35).
CHECK_TEXT: Final = "nexovue embedding check: slow checkout requests"
# A run stops starting calls past this, so it ends well inside its time limit (a guess).
SWEEP_BUDGET: Final = timedelta(seconds=120)
_FIELDS_SENT: Final = ("knowledge_chunks.content",)

# ------------------------------------------------------------------------- statements

# Across every organization: whose settings could need work, embedding or nulling. Each
# organization's own statements below bind its `org_id`. `:only` narrows it to one
# organization for a test, so a test's sweep never takes another test's rows; null for beat.
_ORGANIZATIONS: Final = text(
    """
    SELECT s.org_id FROM assistant_settings AS s
    WHERE (CAST(:only AS uuid) IS NULL OR s.org_id = CAST(:only AS uuid))
      AND (s.embedding_model IS NOT NULL
           OR EXISTS (SELECT 1 FROM ai_project_opt_outs AS o WHERE o.org_id = s.org_id))
    ORDER BY s.org_id
    """
)
_SETTINGS: Final = text(
    """
    SELECT s.enabled, s.data_level, s.mask_emails, s.consented_fingerprint,
           s.consented_disclosure_version, s.consented_data_level, s.embedding_model,
           s.embedding_wire_format, s.embedding_base_url, s.sealed_embedding_key,
           s.embedding_key_id, s.embedding_consented_fingerprint, s.embedding_checked_fingerprint,
           s.embedding_checked_ok, o.deleted_at IS NULL AS organization_live
    FROM assistant_settings AS s JOIN organizations AS o ON o.id = s.org_id
    WHERE s.org_id = :org_id
    """
)
_CONNECTION: Final = text(
    """
    SELECT id, wire_format, base_url, model, key_id, sealed_key, call_seconds,
           monthly_token_limit, enabled, consented_base_url, consented_fingerprint,
           consented_schema
    FROM ai_connections WHERE org_id = :org_id
    """
)
# AC70: an opted-out project's vectors go, and its chunks wait, due, until it is let back in.
# `embedded_model` is set exactly when a vector is, so the account never reads a vector.
_NULL_OPTED_OUT: Final = text(
    """
    UPDATE knowledge_chunks SET embedding = NULL, embedded_model = NULL, embedding_due = true
    WHERE org_id = :org_id AND embedded_model IS NOT NULL
      AND project_id IN (
          SELECT project_id FROM ai_project_opt_outs WHERE org_id = :org_id
      )
    """
)
# Due: new, or embedded by another model. Only live projects that are not opted out (AC70).
_DUE: Final = text(
    """
    SELECT c.id, c.content FROM knowledge_chunks AS c
    JOIN projects AS p ON p.org_id = c.org_id AND p.id = c.project_id
    WHERE c.org_id = :org_id AND p.deleted_at IS NULL
      AND (c.embedding_due OR c.embedded_model IS DISTINCT FROM :model)
      AND NOT EXISTS (
          SELECT 1 FROM ai_project_opt_outs AS o
          WHERE o.org_id = c.org_id AND o.project_id = c.project_id
      )
    ORDER BY c.embedding_due DESC, c.id
    LIMIT :limit
    """
)
# A chunk replaced or deleted meanwhile matches nothing (a runbook's save writes new rows).
_WRITE_VECTOR: Final = text(
    """
    UPDATE knowledge_chunks
    SET embedding = CAST(:vector AS vector), embedded_model = :model, embedding_due = false
    WHERE org_id = :org_id AND id = :id
    """
)
# A check's result, only on the values it tested: a change saved meanwhile stays untested.
_RECORD_CHECK: Final = text(
    """
    UPDATE assistant_settings
    SET embedding_checked_fingerprint = :fingerprint, embedding_checked_at = :at,
        embedding_checked_ok = :ok, embedding_check_reason = :reason
    WHERE org_id = :org_id AND embedding_model = :model
      AND embedding_base_url IS NOT DISTINCT FROM CAST(:own_base_url AS text)
      AND embedding_key_id IS NOT DISTINCT FROM CAST(:own_key_id AS uuid)
    """
)
# Whether the vector columns are there, and granted: an install without `vector` (AC43).
# `CASE` asks the privilege only of a column that exists, since asking of a missing one fails.
_VECTOR_GRANTED: Final = text(
    """
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.knowledge_chunks'::regclass
          AND attname = 'embedding' AND NOT attisdropped
    ) THEN has_column_privilege('public.knowledge_chunks', 'embedding', 'UPDATE') ELSE false END
    """
)


# ----------------------------------------------------------------------------- calling


class EmbedFailed(Exception):  # noqa: N818 — what the transport said, as a closed word
    def __init__(self, reason: str, *, reached: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.reached = reached


class Embedder(Protocol):
    """One embedding call: the real one sends bytes through F3's guarded transport; tests
    stand a fake in its place."""

    @property
    def batch(self) -> int: ...

    def body_of(self, texts: tuple[str, ...]) -> bytes: ...

    def embed(self, texts: tuple[str, ...]) -> EmbedResponse: ...


EmbedderFor = Callable[[EmbeddingTarget, str | None, int], Embedder]


@dataclass(frozen=True, slots=True)
class TransportEmbedder:
    """The real embedder: an embed format's bytes, sent through F3's transport, so the
    address is judged at connect time and no redirect is followed (F3 FR32, AC38)."""

    target: EmbeddingTarget
    key: str | None
    call_seconds: int
    endpoints: PrivateEndpoints

    @property
    def batch(self) -> int:
        fmt = self.target.format
        return 0 if fmt is None else fmt.batch

    def body_of(self, texts: tuple[str, ...]) -> bytes:
        fmt = self.target.format
        if fmt is None:
            raise EmbedFailed("no_embeddings", reached=False)
        return fmt.build(
            texts, model=self.target.model, base_url=self.target.base_url, key=self.key
        ).body

    def embed(self, texts: tuple[str, ...]) -> EmbedResponse:
        fmt = self.target.format
        if fmt is None:
            raise EmbedFailed("no_embeddings", reached=False)
        call = fmt.build(
            texts, model=self.target.model, base_url=self.target.base_url, key=self.key
        )
        answer = transport.send(
            call, timeout_seconds=self.call_seconds, private_endpoints=self.endpoints
        )
        if answer.failure is not None and answer.status is None:
            reason = "invalid" if answer.failure == "too_large" else answer.failure
            raise EmbedFailed(reason, reached=answer.reached)
        if answer.failure == "too_large":
            return EmbedResponse("invalid").with_counts(request_body=call.body)
        parsed = fmt.parse(answer.status or 0, answer.body, expected=len(texts))
        return parsed.with_counts(request_body=call.body)


def transport_embedder(endpoints: PrivateEndpoints) -> EmbedderFor:
    def build(target: EmbeddingTarget, key: str | None, call_seconds: int) -> Embedder:
        return TransportEmbedder(target, key, call_seconds, endpoints)

    return build


# ------------------------------------------------------------------------ the setting


@dataclass(frozen=True, slots=True)
class Ready:
    """An organization whose texts may go now, and how."""

    org_id: str
    target: EmbeddingTarget
    key: str | None
    call_seconds: int
    monthly_token_limit: int
    mask_emails: bool


@dataclass(frozen=True, slots=True)
class Loaded:
    """What an organization's rows say: its target and opened key, or why not."""

    setting: EmbeddingSetting
    target: EmbeddingTarget | None
    key: str | None
    call_seconds: int
    monthly_token_limit: int | None
    mask_emails: bool
    # Why nothing may be embedded now, or None (closed words, for the log only).
    blocked: str | None
    # The key could not be opened for this address, or breaks F3's rules (FR23).
    key_unreadable: bool = False


def _str(value: object) -> str | None:
    return None if value is None else str(value)


async def load(engine: AsyncEngine, *, org_id: str, keys: ConfiguredKeys) -> Loaded | None:
    """An organization's embedding setting and connection, read again (FR25); None with no
    settings row."""
    async with engine.connect() as db:
        row = (await db.execute(_SETTINGS, {"org_id": org_id})).one_or_none()
        connection = (await db.execute(_CONNECTION, {"org_id": org_id})).one_or_none()
    if row is None:
        return None
    setting = EmbeddingSetting(
        model=row.embedding_model,
        own_wire_format=row.embedding_wire_format,
        own_base_url=row.embedding_base_url,
        own_key_id=_str(row.embedding_key_id),
        own_consented_fingerprint=row.embedding_consented_fingerprint,
        checked_fingerprint=row.embedding_checked_fingerprint,
        checked_ok=row.embedding_checked_ok,
    )
    target = (
        None
        if connection is None and setting.own_base_url is None
        else setting.target(
            chat_wire_format="" if connection is None else connection.wire_format,
            chat_base_url="" if connection is None else connection.base_url,
            chat_key_id=None if connection is None else _str(connection.key_id),
        )
    )
    key: str | None = None
    key_unreadable = False
    if target is not None:
        try:
            key = _open_key(org_id, target, row, connection, keys)
        except (SealError, ValueError, ConnectionRefused):
            key_unreadable = True
    return Loaded(
        setting=setting,
        target=target,
        key=key,
        call_seconds=CHECK_CALL_SECONDS if connection is None else connection.call_seconds,
        monthly_token_limit=None if connection is None else connection.monthly_token_limit,
        mask_emails=row.mask_emails,
        blocked=_blocked(row, connection, setting, target),
        key_unreadable=key_unreadable,
    )


def _blocked(
    row: Any, connection: Any, setting: EmbeddingSetting, target: EmbeddingTarget | None
) -> str | None:
    """F3's rule that AI is on with an owner's consent, chat's that it is on at *facts and
    text* with its own, and the embedding setting's (`keyword_only`), in that order."""
    if not row.organization_live:
        return "organization_deleted"
    if (
        connection is None
        or not connection.enabled
        or connection.monthly_token_limit is None
        or connection.consented_base_url != connection.base_url
        or connection.consented_schema != SCHEMA_VERSION
        or connection.consented_fingerprint != _chat_consent(connection)
    ):
        return "ai_off"
    if (
        not row.enabled
        or row.data_level != "facts_and_text"
        or row.consented_fingerprint != _chat_consent(connection)
        or row.consented_disclosure_version != CHAT_DISCLOSURE_VERSION
        or row.consented_data_level != row.data_level
    ):
        return "chat_not_ready"
    return setting.keyword_only(target)


def _chat_consent(connection: Any) -> str:
    return consent_fingerprint(
        wire_format=connection.wire_format,
        base_url=connection.base_url,
        region=None,
        key_id=_str(connection.key_id),
    )


def _open_key(
    org_id: str, target: EmbeddingTarget, row: Any, connection: Any, keys: ConfiguredKeys
) -> str | None:
    """The key for this target's own address, checked again against F3's rules (FR23)."""
    check_base_url(target.base_url)
    check_model(target.model)
    if target.source == OWN_SOURCE:
        sealed, purpose = row.sealed_embedding_key, EMBEDDING_KEY_PURPOSE
        owner = embedding_key_owner(org_id, target.base_url)
    else:
        sealed, purpose = connection.sealed_key, AI_CONNECTION_PURPOSE
        owner = key_owner(org_id, str(connection.id), target.base_url)
    if sealed is None:
        return None
    key = SealedBox(keyring_from(keys.current, keys.previous), purpose=purpose).open(
        bytes(sealed), owner=owner
    )
    check_key(key)
    return key


def ready(loaded: Loaded | None, org_id: str) -> Ready | None:
    if (
        loaded is None
        or loaded.blocked is not None
        or loaded.key_unreadable
        or loaded.target is None
    ):
        return None
    assert loaded.monthly_token_limit is not None
    return Ready(
        org_id=org_id,
        target=loaded.target,
        key=loaded.key,
        call_seconds=loaded.call_seconds,
        monthly_token_limit=loaded.monthly_token_limit,
        mask_emails=loaded.mask_emails,
    )


# --------------------------------------------------------------------------- the sweep


@dataclass
class Swept:
    nulled: int = 0
    embedded: int = 0
    calls: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class SweepParts:
    keys: ConfiguredKeys
    embedder_for: EmbedderFor
    clock: Callable[[], datetime]


async def vector_granted(engine: AsyncEngine) -> bool:
    """False on an install without `vector`: the column, and so its grant, is missing."""
    async with engine.connect() as db:
        return bool(await db.scalar(_VECTOR_GRANTED))


async def sweep(engine: AsyncEngine, parts: SweepParts, *, only: str | None = None) -> Swept:
    swept = Swept()
    if not await vector_granted(engine):
        return swept
    started = parts.clock()
    async with engine.connect() as db:
        organizations = [
            str(org_id) for org_id in (await db.execute(_ORGANIZATIONS, {"only": only})).scalars()
        ]
    for org_id in organizations:
        async with engine.begin() as db:
            swept.nulled += (await db.execute(_NULL_OPTED_OUT, {"org_id": org_id})).rowcount
        while swept.calls < EMBED_SWEEP_CALLS and parts.clock() - started < SWEEP_BUDGET:
            outcome = await _one_call(engine, parts, org_id, swept)
            if outcome is None:
                break
    return swept


async def _one_call(
    engine: AsyncEngine, parts: SweepParts, org_id: str, swept: Swept
) -> bool | None:
    """One batch of one organization: True when it embedded, None when it should stop."""
    allowed = ready(await load(engine, org_id=org_id, keys=parts.keys), org_id)
    if allowed is None:
        return None
    embedder = parts.embedder_for(allowed.target, allowed.key, allowed.call_seconds)
    if embedder.batch < 1:
        return None
    size = min(embedder.batch, EMBED_SWEEP_CHUNKS - swept.embedded)
    if size < 1:
        return None
    async with engine.connect() as db:
        due = (
            await db.execute(_DUE, {"org_id": org_id, "model": allowed.target.model, "limit": size})
        ).all()
    if not due:
        return None
    texts = tuple(mask(row.content, mask_emails=allowed.mask_emails) for row in due)
    now = parts.clock()
    body = embedder.body_of(texts)
    reserve = estimate_tokens(len(body))
    admitted = await admit(engine, allowed, body=body, now=now)
    if admitted is None:
        logger.info("embedding waits: the month's share is spent", org_id=org_id)
        return None
    run_id, month = admitted
    swept.calls += 1
    response, end = _call(embedder, texts, reserve=reserve, clock=parts.clock)
    written = await settle(
        engine,
        org_id=org_id,
        run_id=run_id,
        month=month,
        reserved=reserve,
        end=end,
        writes=[]
        if response is None or response.outcome != "ok"
        else list(zip([str(row.id) for row in due], response.vectors, strict=True)),
        model=allowed.target.model,
    )
    if not written:
        swept.failed += 1
        logger.info(
            "an embedding call failed; its chunks stay due",
            org_id=org_id,
            outcome=end.outcome,
        )
        return None
    swept.embedded += len(due)
    return True


async def admit(
    engine: AsyncEngine,
    allowed: Ready,
    *,
    body: bytes,
    now: datetime,
    fields_sent: tuple[str, ...] = _FIELDS_SENT,
) -> tuple[str, Any] | None:
    """The month's reservation under chat's share and the run row, in one transaction,
    committed before any byte is sent; None when the month cannot hold it."""
    month = month_of(now)
    reserve = estimate_tokens(len(body))
    async with engine.begin() as db:
        if not await store.reserve_month(
            db,
            org_id=allowed.org_id,
            month=month,
            reserve=reserve,
            ceiling=chat_ceiling(allowed.monthly_token_limit),
        ):
            return None
        run_id = await store.insert_run(
            db,
            store.RunStart(
                org_id=allowed.org_id,
                requested_by=None,
                purpose="embed",
                wire_format=allowed.target.wire_format,
                model=allowed.target.model,
                region_label=None,
                provider_host=provider_host(allowed.target.base_url),
                prompt_version=EMBED_VERSION,
                request_sha256=hashlib.sha256(body).hexdigest(),
                fields_sent=fields_sent,
                reserved_tokens=reserve,
                started_at=now,
            ),
        )
    return run_id, month


def _call(
    embedder: Embedder,
    texts: tuple[str, ...],
    *,
    reserve: int,
    clock: Callable[[], datetime],
) -> tuple[EmbedResponse | None, store.RunEnd]:
    """The call, and its run row's ending; a call that may have reached the provider and
    returned nothing is charged its estimate, flagged (overstating is the safe side)."""
    try:
        response = embedder.embed(texts)
    except EmbedFailed as failed:
        return None, store.RunEnd(
            outcome="unavailable",
            input_tokens=reserve if failed.reached else None,
            output_tokens=0 if failed.reached else None,
            tokens_estimated=failed.reached,
            finished_at=clock(),
        )
    usable = response.outcome == "ok" and len(response.vectors) == len(texts)
    return response if usable else None, store.RunEnd(
        outcome="done" if usable else ("invalid" if response.outcome == "ok" else response.outcome),
        input_tokens=response.input_tokens,
        output_tokens=0,
        tokens_estimated=response.tokens_estimated,
        finished_at=clock(),
    )


async def settle(
    engine: AsyncEngine,
    *,
    org_id: str,
    run_id: str,
    month: Any,
    reserved: int,
    end: store.RunEnd,
    writes: list[tuple[str, tuple[float, ...]]],
    model: str,
) -> bool:
    """The run's end and its tokens moved, then the vectors, in one transaction. A run F3's
    sweep already ended settles nothing and writes no vector. True when vectors were written."""
    async with engine.begin() as db:
        if not await store.settle_run(
            db, org_id=org_id, run_id=run_id, month=month, reserved=reserved, end=end
        ):
            return False
        for chunk_id, vector in writes:
            await db.execute(
                _WRITE_VECTOR,
                {
                    "org_id": org_id,
                    "id": chunk_id,
                    "vector": vector_literal(vector),
                    "model": model,
                },
            )
    return bool(writes)


# --------------------------------------------------------------------------- the check


@dataclass(frozen=True, slots=True)
class Checked:
    ok: bool
    reason: str | None
    dimensions: int | None = None


async def check(
    engine: AsyncEngine, *, org_id: str, parts: SweepParts, requested_by: str | None
) -> Checked:
    """One real call with nexovue's own words, metered like any embedding, and its result on
    the settings row only if the values it tested still stand (AC85)."""
    loaded = await load(engine, org_id=org_id, keys=parts.keys)
    if loaded is None or loaded.setting.model is None or loaded.target is None:
        return Checked(False, "no_connection")
    target = loaded.target
    checked = await _check_call(engine, org_id, loaded, parts, requested_by)
    async with engine.begin() as db:
        await db.execute(
            _RECORD_CHECK,
            {
                "org_id": org_id,
                "model": target.model,
                "own_base_url": loaded.setting.own_base_url,
                "own_key_id": loaded.setting.own_key_id,
                "fingerprint": target.fingerprint,
                "at": parts.clock(),
                "ok": checked.ok,
                "reason": checked.reason,
            },
        )
    logger.info(
        "an embedding model was checked", org_id=org_id, passed=checked.ok, reason=checked.reason
    )
    return checked


async def _check_call(
    engine: AsyncEngine, org_id: str, loaded: Loaded, parts: SweepParts, requested_by: str | None
) -> Checked:
    target = loaded.target
    assert target is not None
    if target.format is None:
        return Checked(False, "no_embeddings")
    if loaded.key_unreadable:
        return Checked(False, "key_unreadable")
    try:
        check_base_url(target.base_url)
        check_model(target.model)
    except ConnectionRefused:
        return Checked(False, "not_allowed")
    if loaded.monthly_token_limit is None:
        return Checked(False, "no_connection")
    allowed = Ready(
        org_id=org_id,
        target=target,
        key=loaded.key,
        call_seconds=min(loaded.call_seconds, CHECK_CALL_SECONDS),
        monthly_token_limit=loaded.monthly_token_limit,
        mask_emails=loaded.mask_emails,
    )
    embedder = parts.embedder_for(target, allowed.key, allowed.call_seconds)
    texts = (CHECK_TEXT,)
    body = embedder.body_of(texts)
    reserve = estimate_tokens(len(body))
    admitted = await admit(engine, allowed, body=body, now=parts.clock(), fields_sent=())
    if admitted is None:
        return Checked(False, "limited")
    run_id, month = admitted
    try:
        response = embedder.embed(texts)
    except EmbedFailed as failed:
        end = store.RunEnd(
            outcome="unavailable",
            input_tokens=reserve if failed.reached else None,
            output_tokens=0 if failed.reached else None,
            tokens_estimated=failed.reached,
            finished_at=parts.clock(),
        )
        await settle(
            engine,
            org_id=org_id,
            run_id=run_id,
            month=month,
            reserved=reserve,
            end=end,
            writes=[],
            model=target.model,
        )
        return Checked(False, failed.reason)
    ok = response.outcome == "ok" and len(response.vectors) == 1
    end = store.RunEnd(
        outcome="done" if ok else ("invalid" if response.outcome == "ok" else response.outcome),
        input_tokens=response.input_tokens,
        output_tokens=0,
        tokens_estimated=response.tokens_estimated,
        finished_at=parts.clock(),
    )
    await settle(
        engine,
        org_id=org_id,
        run_id=run_id,
        month=month,
        reserved=reserve,
        end=end,
        writes=[],
        model=target.model,
    )
    if not ok:
        return Checked(False, "invalid" if response.outcome == "ok" else response.outcome)
    return Checked(True, None, dimensions=response.dimensions)


# ------------------------------------------------------------------------------- tasks


def _parts() -> SweepParts:
    settings = get_settings()
    return SweepParts(
        keys=settings.ai_secret_keys,
        embedder_for=transport_embedder(settings.ai_endpoints),
        clock=lambda: datetime.now(UTC),
    )


@task(EMBED_SWEEP_TASK)
def embed_chunks() -> None:
    swept = with_engine(lambda engine: sweep(engine, _parts()))
    if swept.calls or swept.nulled:
        logger.info(
            "the embedding sweep ran",
            embedded=swept.embedded,
            calls=swept.calls,
            failed=swept.failed,
            nulled=swept.nulled,
        )


@task(EMBED_CHECK_TASK)
def check_embedding(*, org_id: str, requested_by: str | None = None) -> None:
    with_engine(
        lambda engine: check(engine, org_id=org_id, parts=_parts(), requested_by=requested_by)
    )
