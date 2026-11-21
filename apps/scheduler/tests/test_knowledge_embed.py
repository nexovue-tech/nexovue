"""F4 plan T6.1, T6.2: chat's embedding sweep, its check and a question's own vector, run for
real as the AI account (`nexovue_insight`) against real Postgres with the `vector` columns
`setup` adds, and a fake embedding provider (AC41, AC70, AC85; FR25, FR31, FR40).

**Fake providers only** (foundation item 31): `FakeEmbedder` answers in process with
`meaning_vector`, records exactly what it was sent, and can fail as the transport fails. The
real call per format is the founder's, after Phase 2.

**Tests own their rows:** each makes its own organization (`made` deletes it after), and every
sweep here is narrowed to that organization (`only`), so no test takes another's rows. Clocks
are read when each test starts (`now`).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from assistant_rows import BASE_URL, Asked, as_insight, ask, install, made, reachable, run
from live import require_stack
from nexovue_platform.ai.connection import consent_fingerprint
from nexovue_platform.ai.embed import (
    EmbeddingSetting,
    EmbeddingTarget,
    EmbedResponse,
    OpenAIEmbeddings,
)
from nexovue_platform.ai.testing import meaning_vector
from nexovue_platform.checks.sealing import ConfiguredKeys, generate_key
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_scheduler.insight import knowledge
from nexovue_scheduler.insight.assistant_store import PostgresQuestionStore
from nexovue_scheduler.insight.knowledge import EmbedFailed, SweepParts

require_stack(
    reachable=reachable(),
    suite="chat's embedding suite",
    remedy="start the dev stack so Postgres answers, and run `setup` so the vector columns exist",
)

__all__ = ["as_insight", "install", "made"]

MODEL = "fake-embedding"
KEYS = ConfiguredKeys(generate_key(), "")
PLANTED_PASSWORD = "hunter2-planted"


@dataclass
class FakeEmbedder:
    """Answers with `meaning_vector`; records what it was sent; fails when told to."""

    batch: int = 64
    sent: list[tuple[str, ...]] = field(default_factory=list)
    fail: EmbedFailed | None = None
    vectors_short: bool = False
    during: Callable[[], None] | None = None

    def body_of(self, texts: tuple[str, ...]) -> bytes:
        return OpenAIEmbeddings().build(texts, model=MODEL, base_url=BASE_URL, key=None).body

    def embed(self, texts: tuple[str, ...]) -> EmbedResponse:
        self.sent.append(texts)
        if self.during is not None:
            self.during()
        if self.fail is not None:
            raise self.fail
        shown = texts[:-1] if self.vectors_short else texts
        return EmbedResponse(
            "ok",
            vectors=tuple(tuple(meaning_vector(text)) for text in shown),
            input_tokens=10 * len(texts),
        )


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC)


def _parts(embedder: FakeEmbedder, now: datetime) -> SweepParts:
    def embedder_for(target: EmbeddingTarget, key: str | None, call_seconds: int) -> FakeEmbedder:
        return embedder

    return SweepParts(keys=KEYS, embedder_for=embedder_for, clock=lambda: now)


def _target(model: str = MODEL) -> EmbeddingTarget:
    target = EmbeddingSetting(model=model).target(
        chat_wire_format="openai_chat", chat_base_url=BASE_URL, chat_key_id=None
    )
    assert target is not None
    return target


async def _embedding_on(install: AsyncEngine, asked: Asked, *, model: str = MODEL) -> None:
    """The model set and its check passed, on the chat connection `ask` made."""
    await run(
        install,
        "UPDATE assistant_settings SET embedding_model = :m, embedding_checked_fingerprint = :fp, "
        "embedding_checked_ok = true, embedding_checked_at = now() WHERE org_id = :o",
        o=asked.org_id,
        m=model,
        fp=_target(model).fingerprint,
    )


async def _runbook(
    install: AsyncEngine, asked: Asked, *contents: str, project_id: str | None = None
) -> list[str]:
    project = project_id or asked.project_id
    runbook_id = (
        await run(
            install,
            "INSERT INTO runbooks (org_id, project_id, title, content) VALUES (:o, :p, 'r', 'x') "
            "RETURNING id",
            o=asked.org_id,
            p=project,
        )
    )[0]
    ids = []
    for position, content in enumerate(contents):
        found = await run(
            install,
            "INSERT INTO knowledge_chunks (org_id, project_id, source_kind, runbook_id, position, "
            "content) VALUES (:o, :p, 'runbook', :r, :n, :c) RETURNING id",
            o=asked.org_id,
            p=project,
            r=runbook_id,
            n=position,
            c=content,
        )
        ids.append(str(found[0]))
    return ids


async def _chunks(install: AsyncEngine, asked: Asked) -> list[Any]:
    async with install.connect() as db:
        rows = await db.execute(
            text(
                "SELECT id::text, content, embedded_model, embedding_due, "
                "vector_dims(embedding) AS dims FROM knowledge_chunks WHERE org_id = :o "
                "ORDER BY position, id"
            ),
            {"o": asked.org_id},
        )
        return list(rows.all())


async def _month(install: AsyncEngine, asked: Asked) -> tuple[int, int]:
    found = await run(
        install,
        "SELECT used_tokens, reserved_tokens FROM ai_usage_monthly WHERE org_id = :o",
        o=asked.org_id,
    )
    return (0, 0) if found is None else (int(found[0]), int(found[1]))


async def _sweep(
    as_insight: AsyncEngine, asked: Asked, embedder: FakeEmbedder, now: datetime
) -> knowledge.Swept:
    return await knowledge.sweep(as_insight, _parts(embedder, now), only=asked.org_id)


async def test_due_chunks_are_embedded_masked_and_metered_as_the_ai_account(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], now: datetime
) -> None:
    """FR40: masked before they go; F3's limits: one run row, its tokens moved to used."""
    asked = await ask(install, made)
    await _embedding_on(install, asked)
    await _runbook(
        install,
        asked,
        "## Latency\nCheckout latency rises when the pool is full.",
        f"Log in with password={PLANTED_PASSWORD} and mail ops@example.com",
    )
    embedder = FakeEmbedder()

    swept = await _sweep(as_insight, asked, embedder, now)

    assert (swept.embedded, swept.calls, swept.failed) == (2, 1, 0)
    [texts] = embedder.sent
    assert PLANTED_PASSWORD not in " ".join(texts) and "ops@example.com" not in " ".join(texts)
    assert "[masked]" in texts[1]
    chunks = await _chunks(install, asked)
    assert [(row.embedded_model, row.embedding_due, row.dims) for row in chunks] == [
        (MODEL, False, len(meaning_vector("x"))),
        (MODEL, False, len(meaning_vector("x"))),
    ]
    runs = await run(
        install,
        "SELECT purpose, outcome, input_tokens, reserved_tokens, fields_sent::text FROM ai_runs "
        "WHERE org_id = :o",
        o=asked.org_id,
    )
    assert tuple(runs[:3]) == ("embed", "done", 20)
    assert await _month(install, asked) == (20, 0)
    # Nothing is due now, so a second run makes no call.
    assert (await _sweep(as_insight, asked, embedder, now)).calls == 0


@pytest.mark.parametrize(
    "blocked",
    ["no_check", "failed_check", "no_model", "facts_only", "chat_off", "own_unagreed", "ai_off"],
)
async def test_nothing_goes_until_the_setting_the_check_and_chat_all_allow_it(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], now: datetime, blocked: str
) -> None:
    """AC85: proved by a check before it takes effect; FR25: chat on at facts and text, with
    consent; D61: a second connection needs its own owner's agreement."""
    asked = await ask(install, made)
    await _embedding_on(install, asked)
    statement = {
        "no_check": "UPDATE assistant_settings SET embedding_checked_fingerprint = NULL",
        "failed_check": "UPDATE assistant_settings SET embedding_checked_ok = false",
        "no_model": "UPDATE assistant_settings SET embedding_model = NULL",
        "facts_only": "UPDATE assistant_settings SET data_level = 'facts', "
        "consented_data_level = 'facts'",
        "chat_off": "UPDATE assistant_settings SET enabled = false",
        "own_unagreed": "UPDATE assistant_settings SET embedding_wire_format = 'openai_chat', "
        "embedding_base_url = 'https://embed.example/v1'",
        "ai_off": "UPDATE ai_connections SET enabled = false",
    }[blocked]
    await run(install, f"{statement} WHERE org_id = :o", o=asked.org_id)
    await _runbook(install, asked, "Checkout latency rises.")
    embedder = FakeEmbedder()

    assert (await _sweep(as_insight, asked, embedder, now)).calls == 0
    assert embedder.sent == []
    assert [row.embedding_due for row in await _chunks(install, asked)] == [True]


async def test_a_second_connection_an_owner_agreed_to_and_a_check_passed_is_used(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], now: datetime
) -> None:
    asked = await ask(install, made)
    own = "https://embed.example/v1"
    setting = EmbeddingSetting(model=MODEL, own_wire_format="openai_chat", own_base_url=own)
    target = setting.target(
        chat_wire_format="openai_chat", chat_base_url=BASE_URL, chat_key_id=None
    )
    assert target is not None
    await run(
        install,
        "UPDATE assistant_settings SET embedding_model = :m, "
        "embedding_wire_format = 'openai_chat', embedding_base_url = :url, "
        "embedding_consented_fingerprint = :agreed, "
        "embedding_checked_fingerprint = :fp, embedding_checked_ok = true WHERE org_id = :o",
        o=asked.org_id,
        m=MODEL,
        url=own,
        agreed=consent_fingerprint(
            wire_format="openai_chat", base_url=own, region=None, key_id=None
        ),
        fp=target.fingerprint,
    )
    await _runbook(install, asked, "Checkout latency rises.")
    targets: list[EmbeddingTarget] = []
    embedder = FakeEmbedder()

    def embedder_for(chosen: EmbeddingTarget, key: str | None, call_seconds: int) -> FakeEmbedder:
        targets.append(chosen)
        return embedder

    parts = SweepParts(keys=KEYS, embedder_for=embedder_for, clock=lambda: now)
    assert (await knowledge.sweep(as_insight, parts, only=asked.org_id)).embedded == 1
    assert {(chosen.source, chosen.base_url) for chosen in targets} == {("own", own)}


async def test_opted_out_deleted_projects_and_organizations_see_no_call_and_lose_vectors(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], now: datetime
) -> None:
    """AC70: no `embed` call for a chunk of an opted-out or deleted project or a deleted
    organization; opting out nulls the project's stored vectors."""
    asked = await ask(install, made)
    await _embedding_on(install, asked)
    [kept] = await _runbook(install, asked, "Checkout latency rises.")
    embedder = FakeEmbedder()
    assert (await _sweep(as_insight, asked, embedder, now)).embedded == 1

    await run(
        install,
        "INSERT INTO ai_project_opt_outs (org_id, project_id) VALUES (:o, :p)",
        o=asked.org_id,
        p=asked.project_id,
    )
    await _runbook(install, asked, "Disk full on the queue host.")
    embedder.sent.clear()
    swept = await _sweep(as_insight, asked, embedder, now)
    assert (swept.nulled, embedder.sent) == (1, [])
    rows = {row.id: row for row in await _chunks(install, asked)}
    assert (rows[kept].embedded_model, rows[kept].dims, rows[kept].embedding_due) == (
        None,
        None,
        True,
    )

    await run(install, "DELETE FROM ai_project_opt_outs WHERE org_id = :o", o=asked.org_id)
    await run(install, "UPDATE projects SET deleted_at = now() WHERE org_id = :o", o=asked.org_id)
    assert (await _sweep(as_insight, asked, embedder, now)).calls == 0
    await run(install, "UPDATE projects SET deleted_at = NULL WHERE org_id = :o", o=asked.org_id)
    await run(install, "UPDATE organizations SET deleted_at = now() WHERE id = :o", o=asked.org_id)
    assert (await _sweep(as_insight, asked, embedder, now)).calls == 0
    assert embedder.sent == []


async def test_a_new_model_re_embeds_everything_while_the_old_vectors_wait(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], now: datetime
) -> None:
    """FR31: changing the model re-embeds in the background; until then each vector keeps
    the model that made it, which search filters on."""
    asked = await ask(install, made)
    await _embedding_on(install, asked)
    await _runbook(install, asked, "Checkout latency rises.", "Disk full on the queue host.")
    assert (await _sweep(as_insight, asked, FakeEmbedder(), now)).embedded == 2

    await _embedding_on(install, asked, model="fake-embedding-2")
    assert {row.embedded_model for row in await _chunks(install, asked)} == {MODEL}
    embedder = FakeEmbedder(batch=1)
    assert (await _sweep(as_insight, asked, embedder, now)).embedded == 2
    assert len(embedder.sent) == 2
    assert {row.embedded_model for row in await _chunks(install, asked)} == {"fake-embedding-2"}


@pytest.mark.parametrize("failure", ["reached", "not_reached", "short", "refused"])
async def test_a_failed_call_leaves_the_chunk_due_and_charges_only_what_may_be_billed(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], now: datetime, failure: str
) -> None:
    """Design, *Failure handling*: embedding fails, the chunk stays due, keyword search works,
    the next run retries. A call that may have reached the provider is charged its estimate."""
    asked = await ask(install, made)
    await _embedding_on(install, asked)
    await _runbook(install, asked, "Checkout latency rises.", "Disk full.")
    embedder = FakeEmbedder()
    if failure == "reached":
        embedder.fail = EmbedFailed("timed_out", reached=True)
    elif failure == "not_reached":
        embedder.fail = EmbedFailed("address_refused", reached=False)
    elif failure == "short":
        embedder.vectors_short = True
    else:
        embedder.fail = None

        def refuse(texts: tuple[str, ...]) -> EmbedResponse:
            # As the real embedder does: a reply with no count is estimated from the request.
            return EmbedResponse("bad_key").with_counts(request_body=embedder.body_of(texts))

        embedder.embed = refuse  # type: ignore[method-assign]

    swept = await _sweep(as_insight, asked, embedder, now)

    assert (swept.calls, swept.failed, swept.embedded) == (1, 1, 0)
    assert [row.embedding_due for row in await _chunks(install, asked)] == [True, True]
    used, reserved = await _month(install, asked)
    assert reserved == 0
    estimate = -(-len(embedder.body_of(("Checkout latency rises.", "Disk full."))) // 3)
    assert (
        used == {"reached": estimate, "not_reached": 0, "short": 20, "refused": estimate}[failure]
    )


async def test_over_the_month_s_share_nothing_is_sent(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], now: datetime
) -> None:
    """F3's reservation under chat's share: a month that cannot hold the call sends nothing."""
    asked = await ask(install, made, limit=10)
    await _embedding_on(install, asked)
    await _runbook(install, asked, "Checkout latency rises.")
    embedder = FakeEmbedder()
    assert (await _sweep(as_insight, asked, embedder, now)).calls == 0
    assert embedder.sent == [] and await _month(install, asked) == (0, 0)


async def test_a_call_the_sweep_already_ended_settles_nothing_and_writes_no_vector(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], now: datetime
) -> None:
    """`docs/changes/wording-settles-once/`: F3's sweep calls the run `died` while the call
    is out and charges its reservation; the late answer then changes nothing."""
    asked = await ask(install, made)
    await _embedding_on(install, asked)
    await _runbook(install, asked, "Checkout latency rises.")
    dsn = install.url.render_as_string(hide_password=False)

    def sweep_ends_it() -> None:
        async def died() -> None:
            # What `store.sweep_died` does, for this organization's one run alone.
            engine = create_async_engine(dsn)
            try:
                async with engine.begin() as db:
                    row = (
                        await db.execute(
                            text(
                                "UPDATE ai_runs SET outcome = 'died', finished_at = now(), "
                                "input_tokens = reserved_tokens, output_tokens = 0, "
                                "tokens_estimated = true WHERE org_id = :o AND outcome = 'started' "
                                "RETURNING reserved_tokens"
                            ),
                            {"o": asked.org_id},
                        )
                    ).one()
                    await db.execute(
                        text(
                            "UPDATE ai_usage_monthly SET used_tokens = used_tokens + :r, "
                            "reserved_tokens = GREATEST(reserved_tokens - :r, 0) WHERE org_id = :o"
                        ),
                        {"o": asked.org_id, "r": row.reserved_tokens},
                    )
            finally:
                await engine.dispose()

        thread = threading.Thread(target=lambda: asyncio.run(died()))
        thread.start()
        thread.join()

    embedder = FakeEmbedder(during=sweep_ends_it)
    swept = await _sweep(as_insight, asked, embedder, now)

    assert (swept.embedded, swept.failed) == (0, 1)
    [chunk] = await _chunks(install, asked)
    assert (chunk.embedding_due, chunk.dims) == (True, None)
    estimate = -(-len(embedder.body_of(("Checkout latency rises.",))) // 3)
    assert await _month(install, asked) == (estimate, 0)
    outcome = await run(install, "SELECT outcome FROM ai_runs WHERE org_id = :o", o=asked.org_id)
    assert outcome[0] == "died"


async def test_a_check_records_its_result_only_on_the_values_it_tested(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], now: datetime
) -> None:
    """AC85: a real call with nexovue's own words; a change saved during it stays untested."""
    asked = await ask(install, made)
    await run(
        install,
        "UPDATE assistant_settings SET embedding_model = :m WHERE org_id = :o",
        o=asked.org_id,
        m=MODEL,
    )
    embedder = FakeEmbedder()
    checked = await knowledge.check(
        as_insight, org_id=asked.org_id, parts=_parts(embedder, now), requested_by=None
    )
    assert (checked.ok, checked.reason) == (True, None)
    assert embedder.sent == [(knowledge.CHECK_TEXT,)]
    row = await run(
        install,
        "SELECT embedding_checked_ok, embedding_checked_fingerprint, embedding_check_reason "
        "FROM assistant_settings WHERE org_id = :o",
        o=asked.org_id,
    )
    assert tuple(row) == (True, _target().fingerprint, None)
    purpose = await run(install, "SELECT purpose FROM ai_runs WHERE org_id = :o", o=asked.org_id)
    assert purpose[0] == "embed"

    def renamed() -> None:
        async def change() -> None:
            engine = create_async_engine(install.url.render_as_string(hide_password=False))
            try:
                async with engine.begin() as db:
                    await db.execute(
                        text(
                            "UPDATE assistant_settings SET embedding_model = 'other', "
                            "embedding_checked_ok = NULL, embedding_checked_fingerprint = NULL "
                            "WHERE org_id = :o"
                        ),
                        {"o": asked.org_id},
                    )
            finally:
                await engine.dispose()

        thread = threading.Thread(target=lambda: asyncio.run(change()))
        thread.start()
        thread.join()

    moved = FakeEmbedder(during=renamed)
    await knowledge.check(
        as_insight, org_id=asked.org_id, parts=_parts(moved, now), requested_by=None
    )
    row = await run(
        install,
        "SELECT embedding_model, embedding_checked_ok FROM assistant_settings WHERE org_id = :o",
        o=asked.org_id,
    )
    assert tuple(row) == ("other", None)


async def test_a_provider_with_no_embeddings_fails_its_check_with_no_call(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], now: datetime
) -> None:
    asked = await ask(install, made)
    await run(
        install,
        "UPDATE ai_connections SET wire_format = 'anthropic_messages' WHERE org_id = :o",
        o=asked.org_id,
    )
    await run(
        install,
        "UPDATE assistant_settings SET embedding_model = :m WHERE org_id = :o",
        o=asked.org_id,
        m=MODEL,
    )
    embedder = FakeEmbedder()
    checked = await knowledge.check(
        as_insight, org_id=asked.org_id, parts=_parts(embedder, now), requested_by=None
    )
    assert (checked.ok, checked.reason, embedder.sent) == (False, "no_embeddings", [])
    row = await run(
        install,
        "SELECT embedding_checked_ok, embedding_check_reason FROM assistant_settings "
        "WHERE org_id = :o",
        o=asked.org_id,
    )
    assert tuple(row) == (False, "no_embeddings")


async def test_a_question_s_own_vector_is_stored_on_its_step_and_nulled_when_it_ends(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], now: datetime
) -> None:
    """T6.2: the worker stores the phrase's vector and model on its step, as the AI account;
    `assistant_finish_message` nulls both when the question ends (design, *Settling*, 5)."""
    asked = await ask(install, made)
    await _embedding_on(install, asked)
    store = PostgresQuestionStore(as_insight)
    question = await store.start(org_id=asked.org_id, message_id=asked.message_id)
    assert question is not None
    allowed = await store.embedding(question, keys=KEYS)
    assert allowed is not None and allowed.target.model == MODEL
    step_id = await store.add_step(
        question,
        position=1,
        tool="search_knowledge",
        arguments={"q": "slow"},
        display=None,
        from_time=None,
        to_time=None,
    )
    assert step_id is not None
    run_id = await store.start_embed_run(question, allowed.target, request_sha256="0" * 64)
    assert run_id
    vector = tuple(meaning_vector("why is it slow?"))
    assert await store.set_query_embedding(question, step_id, vector=vector, model=MODEL)
    stored = await run(
        install,
        "SELECT vector_dims(query_embedding), query_embedding_model FROM assistant_steps "
        "WHERE id = :s",
        s=step_id,
    )
    assert tuple(stored) == (len(vector), MODEL)

    assert await store.finish(question, state="answered", answer={"sentences": []})
    stored = await run(
        install,
        "SELECT query_embedding IS NULL, query_embedding_model FROM assistant_steps WHERE id = :s",
        s=step_id,
    )
    assert tuple(stored) == (True, None)
    # And with the setting not ready, no phrase is embedded.
    await run(
        install,
        "UPDATE assistant_settings SET embedding_model = NULL WHERE org_id = :o",
        o=asked.org_id,
    )
    assert await store.embedding(question, keys=KEYS) is None
