"""F4 plan T2.9: chat's loop against a scripted provider, an in-memory store and a recording
API client (AC28, AC55, AC56, AC58, AC60; Stop during the last call).

Every test pins the clock. What the loop writes is read from `FakeStore`: exactly one finish
per question, what it holds returned or charged, steps and reads recorded, tool results
never stored.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from assistant_fakes import (
    CALL_SECONDS,
    KEYS,
    NOW,
    PROJECT,
    TICKET,
    WINDOW_FROM,
    WINDOW_TO,
    FakeStore,
    RecordingClient,
    question,
)
from nexovue_platform.ai.chat import ChatProvider, ChatRequest, ChatResponse
from nexovue_platform.ai.embed import EmbeddingTarget, EmbedResponse
from nexovue_platform.ai.testing import ScriptedProvider, answers, calls
from nexovue_platform.assistant_records import MAX_STEPS, QUESTION_RESERVATION_TOKENS

from nexovue_scheduler.insight.api_client import ApiAnswer
from nexovue_scheduler.insight.assistant import LoopParts, ProviderFailed, answer
from nexovue_scheduler.insight.assistant_prompt import StepResult, build_request
from nexovue_scheduler.insight.assistant_store import ChatConnection
from nexovue_scheduler.insight.knowledge import EmbedFailed, Ready

SERVICES_CALL = calls("list_services", {"from": WINDOW_FROM, "to": WINDOW_TO})
GOOD_ANSWER = answers(
    {
        "sentences": [
            {
                "text": "checkout failed 8% of its 1,200 requests in this window.",
                "cites": [{"step": "s1", "rows": [0], "fields": ["error_rate", "request_count"]}],
            }
        ],
        "not_verified": ["The database was not read."],
        "abstained": None,
    }
)


def _parts(
    store: FakeStore,
    provider: ChatProvider,
    client: RecordingClient | None = None,
    clock: list[datetime] | None = None,
) -> LoopParts:
    moments = clock or [NOW]

    def now() -> datetime:
        return moments[0]

    def provider_for(connection: ChatConnection, key: str | None) -> ChatProvider:
        return provider

    return LoopParts(
        store=store,
        client=client or RecordingClient(),
        provider_for=provider_for,
        keys=KEYS,
        clock=now,
    )


async def _ask(
    store: FakeStore, replies: Sequence[Any], client: RecordingClient | None = None
) -> tuple[str | None, ScriptedProvider]:
    provider = ScriptedProvider(list(replies))
    asked = store.the_question
    assert asked is not None
    state = await answer(asked.org_id, asked.id, _parts(store, provider, client))
    return state, provider


async def test_a_question_reads_through_its_ticket_and_answers_with_a_counted_label() -> None:
    store, client = FakeStore(), RecordingClient()

    state, provider = await _ask(store, [SERVICES_CALL, GOOD_ANSWER], client)

    assert state == "answered"
    [finish] = store.finishes
    assert finish["remainder"] == "return"
    [sentence] = finish["answer"]["sentences"]
    assert sentence["label"] == "counted"
    assert sentence["cites"] == ["s1"]
    # One read, through the API, with the question's ticket and the window it asked for.
    [(tool, query, ticket)] = client.asked
    assert (tool.name, ticket) == ("list_services", TICKET)
    assert query["from"].startswith("2026-11-11T14:00:00")
    # The question's own project, written by nexovue, never chosen by the model.
    assert query["project_id"] == PROJECT
    assert store.steps["step1"]["state"] == "read" and store.steps["step1"]["row_count"] == 2
    assert [read["tool"] for read in store.reads] == ["list_services"]
    # Both calls were metered, from the question's own reservation.
    assert store.spent == [(1100, False), (1100, False)]
    assert store.counts == ["answered"]
    # The result went to the model fenced, and nowhere in what is stored.
    assert "<untrusted-" in provider.requests[1].user_text()
    assert 'label="step s1 result"' in provider.requests[1].user_text()
    assert "checkout" not in str(store.steps)


async def test_an_argument_the_catalog_does_not_allow_is_told_and_counts_as_a_step() -> None:
    store, client = FakeStore(), RecordingClient()
    bad = calls("list_services", {"from": WINDOW_FROM, "to": WINDOW_TO, "method": "POST"})

    cites_second = answers(
        {
            "sentences": [
                {
                    "text": "checkout failed 8% of its 1,200 requests in this window.",
                    "cites": [{"step": "s2"}],
                }
            ]
        }
    )

    state, provider = await _ask(store, [bad, SERVICES_CALL, cites_second], client)

    assert state == "answered"
    assert store.steps["step1"]["state"] == "refused"
    assert len(client.asked) == 1
    assert "takes no argument named method" in provider.requests[1].user_text()


async def test_a_month_without_room_ends_over_limit_before_any_call() -> None:
    """AC56: the reservation is refused, so nothing is sent and nothing is held."""
    store = FakeStore(reserve_ok=False)

    state, provider = await _ask(store, [SERVICES_CALL])

    assert state == "over_limit"
    assert provider.requests == []
    assert store.spent == []


async def test_ai_turned_off_mid_question_ends_it_refused_with_no_further_call() -> None:
    """AC60: rechecked before every provider call."""
    store = FakeStore(allowed=[None, None, "ai_off"])

    state, provider = await _ask(store, [SERVICES_CALL, GOOD_ANSWER])

    assert state == "refused"
    assert store.finishes[0]["failure"] == "ai_off"
    assert len(provider.requests) == 1


async def test_stop_during_the_last_call_settles_and_keeps_stopped() -> None:
    """AC28: the person's Stop wins; the call that was running is still counted."""
    store = FakeStore(running=[True, False])

    state, provider = await _ask(store, [SERVICES_CALL, GOOD_ANSWER])

    assert state == "stopped"
    assert len(provider.requests) == 1
    assert store.spent == [(1100, False)]
    assert store.finishes[0]["remainder"] == "return"


async def test_a_call_that_timed_out_charges_its_estimate_and_fails() -> None:
    """AC58: a killed call may still be billed, so its estimate is spent, flagged."""

    def timed_out(request: ChatRequest) -> ChatResponse:
        raise ProviderFailed("timed_out", reached=True)

    store = FakeStore()

    state, _ = await _ask(store, [timed_out])

    assert state == "failed"
    assert store.finishes[0]["failure"] == "timed_out"
    [(tokens, estimated)] = store.spent
    assert tokens > 0 and estimated


async def test_a_spent_reservation_makes_no_further_call() -> None:
    """AC56: the check before each call holds a question to what it reserved."""
    heavy = calls("list_services", {"from": WINDOW_FROM, "to": WINDOW_TO})
    heavy = ChatResponse(
        outcome="ok",
        tool_call=heavy.tool_call,
        input_tokens=QUESTION_RESERVATION_TOKENS - 1_000,
        output_tokens=100,
    )
    store = FakeStore()

    state, provider = await _ask(store, [heavy, GOOD_ANSWER])

    # Too little left even for a final call: the steps stand alone.
    assert state == "partial"
    assert len(provider.requests) == 1


async def test_too_little_time_left_makes_no_call() -> None:
    """AC55: one clock; a call starts only if it can finish."""
    store = FakeStore(the_question=question(deadline_at=NOW + timedelta(seconds=CALL_SECONDS - 1)))

    state, provider = await _ask(store, [SERVICES_CALL])

    assert state == "partial"
    assert provider.requests == []


async def test_the_last_call_offers_no_tools_once_steps_run_out() -> None:
    store = FakeStore(the_question=question(deadline_at=NOW + timedelta(hours=1)))
    replies: list[Any] = [SERVICES_CALL] * MAX_STEPS + [GOOD_ANSWER]

    state, provider = await _ask(store, replies)

    assert state == "partial"
    assert provider.requests[-1].tools == ()
    assert len(store.steps) == MAX_STEPS


async def test_an_answer_sent_back_once_then_withheld() -> None:
    linked = answers(
        {"sentences": [{"text": "See https://evil.example now.", "cites": [{"step": "s1"}]}]}
    )
    store = FakeStore()

    state, provider = await _ask(store, [SERVICES_CALL, linked, linked])

    assert state == "withheld"
    assert store.finishes[0]["rejection"] == "way_out"
    assert "sent back" in provider.requests[2].user_text()
    assert store.counts == ["withheld", "rejected_way_out"]


async def test_an_answer_citing_nothing_read_is_withheld_at_once() -> None:
    uncited = answers({"sentences": [{"text": "It is fine.", "cites": [{"step": "s7"}]}]})
    store = FakeStore()

    state, provider = await _ask(store, [SERVICES_CALL, uncited])

    assert state == "withheld"
    assert store.finishes[0]["rejection"] == "citations"
    assert len(provider.requests) == 2


async def test_a_ticket_refused_on_a_read_ends_the_question_with_no_further_call() -> None:
    store = FakeStore()

    state, provider = await _ask(store, [SERVICES_CALL, GOOD_ANSWER], RecordingClient(status=403))

    assert state == "refused"
    assert store.finishes[0]["failure"] == "ticket_refused"
    assert len(provider.requests) == 1


async def test_a_failed_read_is_told_and_the_question_goes_on() -> None:
    store = FakeStore()
    fallback = answers({"sentences": [], "abstained": "not_enough_evidence"})

    state, provider = await _ask(store, [SERVICES_CALL, fallback], RecordingClient(status=503))

    assert state == "abstained"
    assert store.steps["step1"]["state"] == "failed"
    assert "failed; answer with what you have" in provider.requests[1].user_text()


async def test_a_question_no_longer_waiting_is_left_alone() -> None:
    store = FakeStore(the_question=None)
    provider = ScriptedProvider([])

    assert await answer("org", "message", _parts(store, provider)) is None
    assert store.finishes == []


@pytest.mark.parametrize("level", ["facts"])
async def test_log_text_is_not_offered_at_the_facts_level(level: str) -> None:
    store = FakeStore(the_question=question(data_level=level))
    search = calls("search_logs", {"from": WINDOW_FROM, "to": WINDOW_TO})

    state, provider = await _ask(store, [search, GOOD_ANSWER])

    assert "search_logs" not in [tool.name for tool in provider.requests[0].tools]
    assert store.steps["step1"]["state"] == "refused"
    assert state == "withheld"


def test_a_whole_tool_result_reaches_the_model_past_f3_s_default_cap() -> None:
    """`bounded` already cut a result to whole rows; F3's fence, at its 4,000-character
    default, would cut that JSON mid-row, so chat names its own cap."""
    body = json.dumps({"logs": [{"body": "x" * 7_000}], "end": "kept"})
    request = build_request(
        question="q",
        context={},
        history=[],
        results=[StepResult("s1", "search_logs", "s1: search_logs read 1 rows", body)],
        tools=(),
        now=NOW,
        project_name="p",
        project_zone="UTC",
        final=True,
    )
    assert json.dumps(body)[1:-1] in request.user_text()


# ------------------------------------------------------------------ AC24: the aged-out range
# A Free-plan project keeps raw telemetry 7 days and rollups 30 (`0005_plans_table.py`).
TEN_DAYS_AGO = {
    "from": (NOW - timedelta(days=10, hours=1)).isoformat(),
    "to": (NOW - timedelta(days=10)).isoformat(),
}
ABSTAIN = answers({"sentences": [], "abstained": "no_data"})


async def test_a_range_past_raw_retention_ends_aged_out_in_code_with_no_read() -> None:
    """AC24: `search_logs` reads raw logs; a range 10 days old on a 7-day project is decided
    in code, before any read, and the model is told the data is no longer kept."""
    store, client = FakeStore(), RecordingClient()

    state, provider = await _ask(store, [calls("search_logs", TEN_DAYS_AGO), ABSTAIN], client)

    assert state == "abstained"
    assert client.asked == []
    step = store.steps["step1"]
    assert step["state"] == "aged_out"
    assert step["failure"] == f"kept_since:{(NOW - timedelta(days=7)).date().isoformat()}"
    assert store.reads == [], "no read happened, so none is recorded"
    told = provider.requests[1].user_text()
    assert "no longer kept" in told and "not evidence that nothing happened" in told


async def test_the_same_range_on_a_rollup_tool_inside_derived_retention_is_read() -> None:
    """AC24: `list_services` reads a rollup kept 30 days, so 10 days ago is read, not aged out."""
    store, client = FakeStore(), RecordingClient()

    await _ask(store, [calls("list_services", TEN_DAYS_AGO), ABSTAIN], client)

    [(tool, query, _)] = client.asked
    assert tool.name == "list_services"
    assert query["from"] == TEN_DAYS_AGO["from"]
    assert store.steps["step1"]["state"] == "read"
    assert store.steps["step1"]["failure"] is None


async def test_a_short_retention_project_ages_out_a_rollup_range_too() -> None:
    """AC24 on a short-retention project: rollups kept 7 days age the same range out."""
    store, client = FakeStore(derived_retention_days=7), RecordingClient()

    await _ask(store, [calls("list_services", TEN_DAYS_AGO), ABSTAIN], client)

    assert client.asked == []
    assert store.steps["step1"]["state"] == "aged_out"


async def test_a_range_half_past_retention_reads_only_what_is_kept_and_says_so() -> None:
    store, client = FakeStore(), RecordingClient()
    straddling = {
        "from": (NOW - timedelta(days=8)).isoformat(),
        "to": (NOW - timedelta(days=6)).isoformat(),
    }

    _, provider = await _ask(store, [calls("search_logs", straddling), ABSTAIN], client)

    [(_, query, _)] = client.asked
    kept = NOW - timedelta(days=7)
    assert query["from"] == kept.isoformat()
    step = store.steps["step1"]
    assert (step["from_time"], step["state"]) == (kept, "read")
    assert step["failure"] == f"kept_since:{kept.date().isoformat()}"
    assert [read["from_time"] for read in store.reads] == [kept]
    assert "read from" in provider.requests[1].user_text()


async def test_a_path_tool_reads_its_slot_and_a_scope_routed_tool_sends_no_project() -> None:
    """T3.1: `get_finding` takes its id in the path and the project from the ticket."""
    store = FakeStore()
    finding = "0195f000-0000-7000-8000-0000000000aa"
    body = {"id": finding, "service_name": "checkout", "signals": [{"signal": "slower"}]}
    client = RecordingClient(answer_for=lambda tool: ApiAnswer(200, json.dumps(body).encode()))

    await _ask(store, [calls("get_finding", {"finding_id": finding.upper()}), ABSTAIN], client)

    [(tool, query, _)] = client.asked
    assert tool.name == "get_finding" and query == {}
    assert client.paths == [{"finding_id": finding}]
    assert store.steps["step1"]["row_count"] == 2


@pytest.mark.parametrize(
    ("status", "failure"),
    # The rate limiter refused the read; the telemetry store was down behind the API.
    # And an item the project does not hold: told, never read as the ticket refused.
    [(429, "limited"), (503, "unavailable"), (404, "not_found")],
)
async def test_a_read_the_api_refused_for_now_or_could_not_serve_says_which(
    status: int, failure: str
) -> None:
    """Spec *Error cases*: "Tool refused by rate limit" and "Telemetry store down" end the
    step with their own word, and the question goes on with what it has."""
    store = FakeStore()

    state, _ = await _ask(store, [SERVICES_CALL, ABSTAIN], RecordingClient(status=status))

    assert (store.steps["step1"]["state"], store.steps["step1"]["failure"]) == ("failed", failure)
    assert state == "abstained", "the question went on to answer with what it had"


# ------------------------------------------------------------------ Step 6: meaning search

ABSTAIN = answers({"sentences": [], "abstained": "not_enough_evidence"})
EMBED_TARGET = EmbeddingTarget(
    "chat", "openai_chat", "http://10.0.0.5:11434/v1", "fake-embed", None
)
READY = Ready(
    org_id="0195f000-0000-7000-8000-000000000001",
    target=EMBED_TARGET,
    key=None,
    call_seconds=CALL_SECONDS,
    monthly_token_limit=10_000_000,
    mask_emails=True,
)


@dataclass
class _Embedder:
    batch: int = 64
    sent: list[tuple[str, ...]] = field(default_factory=list)
    fail: EmbedFailed | None = None

    def body_of(self, texts: tuple[str, ...]) -> bytes:
        return json.dumps({"input": list(texts)}).encode()

    def embed(self, texts: tuple[str, ...]) -> EmbedResponse:
        self.sent.append(texts)
        if self.fail is not None:
            raise self.fail
        return EmbedResponse("ok", vectors=((1.0, 0.0),), input_tokens=7)


def _knowledge_client(search: str) -> RecordingClient:
    body = {"chunks": [], "search": search}
    return RecordingClient(answer_for=lambda tool: ApiAnswer(200, json.dumps(body).encode()))


async def _search(
    store: FakeStore, client: RecordingClient, embedder: _Embedder | None, phrase: str
) -> str | None:
    provider = ScriptedProvider([calls("search_knowledge", {"q": phrase}), ABSTAIN])
    parts = _parts(store, provider, client)
    if embedder is not None:
        parts = replace(parts, embedder_for=lambda target, key, seconds: embedder)
    asked = store.the_question
    assert asked is not None
    return await answer(asked.org_id, asked.id, parts)


async def test_a_knowledge_search_embeds_its_phrase_and_names_its_step() -> None:
    """T6.2: the phrase's vector goes on its step with its own model, the route is told the
    step, never the vector, and the call is metered from the question's reservation."""
    store, client, embedder = (
        FakeStore(embedding_ready=READY),
        _knowledge_client("hybrid"),
        _Embedder(),
    )

    state = await _search(store, client, embedder, "checkout slow")

    assert state == "abstained"
    [(tool, query, _ticket)] = client.asked
    assert tool.name == "search_knowledge"
    assert query["step_id"] == "step1" and "vector" not in str(query)
    assert store.query_embeddings == {"step1": ((1.0, 0.0), "fake-embed")}
    assert embedder.sent == [("checkout slow",)]
    [embed_run] = [run for run in store.runs if run.get("purpose") == "embed"]
    assert (embed_run["model"], embed_run["outcome"]) == ("fake-embed", "done")
    assert (7, False) in store.spent
    assert store.steps["step1"]["failure"] == "search:hybrid"


async def test_without_embeddings_the_search_goes_by_keyword_and_the_step_says_so() -> None:
    """AC41, FR27: no model, no extension or no consent: keyword alone, and said."""
    store, client, embedder = FakeStore(), _knowledge_client("keyword"), _Embedder()

    await _search(store, client, embedder, "checkout slow")

    [(_tool, query, _ticket)] = client.asked
    assert "step_id" not in query
    assert embedder.sent == [] and store.query_embeddings == {}
    assert all(run.get("purpose") != "embed" for run in store.runs)
    assert store.steps["step1"]["failure"] == "search:keyword"


async def test_a_failed_phrase_embedding_searches_by_keyword_and_charges_what_may_be_billed() -> (
    None
):
    """A failed embedding never ends the question; a call that may have reached the
    provider is charged its estimate, flagged."""
    embedder = _Embedder(fail=EmbedFailed("timed_out", reached=True))
    store, client = FakeStore(embedding_ready=READY), _knowledge_client("keyword")

    state = await _search(store, client, embedder, "checkout slow")

    assert state == "abstained"
    [(_tool, query, _ticket)] = client.asked
    assert "step_id" not in query
    estimate = -(-len(embedder.body_of(("checkout slow",))) // 3)
    assert (estimate, True) in store.spent
    [embed_run] = [run for run in store.runs if run.get("purpose") == "embed"]
    assert embed_run["outcome"] == "unavailable"


async def test_the_phrase_is_masked_before_it_is_embedded() -> None:
    """FR40: the same send-time masks as every other text that leaves."""
    store, client, embedder = (
        FakeStore(embedding_ready=READY),
        _knowledge_client("hybrid"),
        _Embedder(),
    )

    await _search(store, client, embedder, "ops@example.com slow")

    [(sent,)] = embedder.sent
    assert "ops@example.com" not in sent and "[masked]" in sent
