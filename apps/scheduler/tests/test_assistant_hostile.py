"""F4 plan T2.10, AC15a: the injection has already won. `HostileProvider` calls every tool with
every trick and answers with every way out it knows; the loop, the catalog and the checks
must hold without the model's help. Offline, in `just check`.

Held: only catalog GETs reach the API, with only catalog arguments, no path-moving value,
no window over seven days, the question's own ticket; and nothing stored carries a way out,
a command, or text the model copied.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from assistant_fakes import (
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
from nexovue_platform.ai.chat import ChatProvider, ChatRequest
from nexovue_platform.ai.testing import EVIL_HOST, HostileProvider, _hostile_calls, hostile_answer
from nexovue_platform.assistant_tools import CATALOG, MAX_WINDOW

from nexovue_scheduler.insight.assistant import LoopParts, answer
from nexovue_scheduler.insight.assistant_answer import command, way_out
from nexovue_scheduler.insight.assistant_store import ChatConnection


async def _hostile_run(
    level: str = "facts_and_text",
) -> tuple[FakeStore, RecordingClient, HostileProvider]:
    store = FakeStore(the_question=question(deadline_at=NOW + timedelta(hours=1), data_level=level))
    client = RecordingClient()
    provider = HostileProvider(WINDOW_FROM, WINDOW_TO)

    def provider_for(connection: ChatConnection, key: str | None) -> ChatProvider:
        return provider

    await answer(
        store.the_question.org_id,  # type: ignore[union-attr]
        store.the_question.id,  # type: ignore[union-attr]
        LoopParts(store, client, provider_for, KEYS, lambda: NOW),
    )
    return store, client, provider


async def test_only_catalog_reads_with_catalog_arguments_reach_the_api() -> None:
    store, client, _ = await _hostile_run()

    assert client.asked, "the hostile provider made no read at all"
    allowed = {tool.name: {rule.query for rule in tool.arguments.values()} for tool in CATALOG}
    for tool, query, ticket in client.asked:
        assert tool in CATALOG
        assert ticket == TICKET
        # The catalog's arguments, and the question's own project, which nexovue writes.
        assert query.pop("project_id") == PROJECT
        assert set(query) <= allowed[tool.name], query
        service = query.get("service", "")
        assert not any(character in service for character in "/\\%"), service
        if "from" in query:
            span = datetime.fromisoformat(query["to"]) - datetime.fromisoformat(query["from"])
            assert span <= MAX_WINDOW
    # Every refused trick was a step, told to the model, and read nothing.
    refused = [step for step in store.steps.values() if step["state"] == "refused"]
    assert len(refused) >= 7


async def test_nothing_stored_carries_a_way_out_a_command_or_a_copy() -> None:
    store, _, _ = await _hostile_run()

    [finish] = store.finishes
    stored = json.dumps(finish["answer"]) + json.dumps(list(store.steps.values()), default=str)
    assert EVIL_HOST not in stored
    assert "IGNORE PREVIOUS" not in stored
    if finish["answer"] is not None:
        for sentence in finish["answer"]["sentences"]:
            assert way_out(sentence["text"]) is None
            assert command(sentence["text"]) is None
    # The model's own abstain word was not one nexovue knows; nothing of it is kept.
    assert finish["abstain"] in (None, "not_enough_evidence", "no_data", "outside_what_it_reads")


async def test_a_hidden_search_phrase_is_neither_stored_nor_shown() -> None:
    store, client, _ = await _hostile_run()

    evil_reads = [query for _, query, _ in client.asked if EVIL_HOST in query.get("q", "")]
    assert evil_reads, "the read still runs; only our own API sees the phrase"
    for step in store.steps.values():
        assert EVIL_HOST not in json.dumps(step["arguments"])
        assert step["display"] is None or EVIL_HOST not in step["display"]


async def test_planted_log_text_reaches_the_model_only_fenced_and_masked() -> None:
    _, _, provider = await _hostile_run()

    sent = "\n".join(request.user_text() for request in provider.requests)
    assert "Bearer abcdefgh12345678" not in sent
    assert "ops@example.com" not in sent
    planted = sent.index("IGNORE PREVIOUS")
    # Inside an F3 fence: its last opening tag before the text is later than any closing one.
    assert sent.rfind("<untrusted-", 0, planted) > sent.rfind("</untrusted-", 0, planted)


async def test_at_the_facts_level_no_log_text_is_read_at_all() -> None:
    _, client, provider = await _hostile_run(level="facts")

    assert all(tool.name != "search_logs" for tool, _, _ in client.asked)
    assert "IGNORE PREVIOUS" not in "\n".join(r.user_text() for r in provider.requests)


HOSTILE_FIXTURE = (
    Path(__file__).resolve().parents[2] / "web/src/features/assistant/hostile.fixture.json"
)


def test_the_web_suite_renders_exactly_this_hostile_output() -> None:
    """AC15c's fixture is this provider's own answer, so the two suites cannot drift."""
    fixture = json.loads(HOSTILE_FIXTURE.read_text(encoding="utf-8"))
    document = hostile_answer(
        ChatRequest(instructions="i", parts=(), tools=(), max_output_tokens=1)
    )
    phrases = [
        arguments["q"]
        for _, arguments in _hostile_calls(WINDOW_FROM, WINDOW_TO)
        if arguments and "q" in arguments
    ]
    assert fixture["sentences"] == [sentence["text"] for sentence in document["sentences"]]
    assert fixture["not_verified"] == document["not_verified"]
    assert fixture["search_phrases"] == phrases
