"""F4 plan T2.9: chat's loop against a scripted provider, an in-memory store and a recording
API client (AC28, AC55, AC56, AC58, AC60; Stop during the last call).

Every test pins the clock. What the loop writes is read from `FakeStore`: exactly one finish
per question, what it holds returned or charged, steps and reads recorded, tool results
never stored.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
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
from nexovue_platform.ai.testing import ScriptedProvider, answers, calls
from nexovue_platform.assistant_records import MAX_STEPS, QUESTION_RESERVATION_TOKENS

from nexovue_scheduler.insight.assistant import LoopParts, ProviderFailed, answer
from nexovue_scheduler.insight.assistant_prompt import StepResult, build_request
from nexovue_scheduler.insight.assistant_store import ChatConnection

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
