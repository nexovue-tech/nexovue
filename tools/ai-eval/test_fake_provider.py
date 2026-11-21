"""F4 plan T2.0: the fake provider, through the real OpenAI-format chat client's bytes.

Each test serves it on a local port and sends what `OpenAIChatTools.build` builds, then reads
the answer with `OpenAIChatTools.parse`, as the AI role would. Reaching it from the running
`scheduler-insight` container on the `ai` network is not run here (status.md).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from urllib.request import Request, urlopen

import pytest
from fake_provider.server import serve
from nexovue_platform.ai.chat import ChatRequest, ChatResponse, ToolSpec
from nexovue_platform.ai.chat_formats import chat_format
from nexovue_platform.ai.contract import Trusted
from nexovue_platform.ai.testing import EVIL_HOST

TOOLS = (
    ToolSpec("list_services", "Services.", {"type": "object"}),
    ToolSpec("search_logs", "Logs.", {"type": "object"}),
)
FORMAT = chat_format("openai_chat")


@pytest.fixture
def provider(request: pytest.FixtureRequest) -> Iterator[str]:
    server = serve(port=0, mode=request.param)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def _ask(base_url: str, text: str, *, tools: tuple[ToolSpec, ...] = TOOLS) -> ChatResponse:
    call = FORMAT.build(
        ChatRequest(instructions="i", parts=(Trusted(text),), tools=tools, max_output_tokens=50),
        model="m",
        base_url=base_url,
        key=None,
    )
    sent = Request(call.url, data=call.body, method=call.method, headers=dict(call.headers))
    with urlopen(sent, timeout=5) as answered:
        return FORMAT.parse(answered.status, answered.read())


@pytest.mark.parametrize("provider", ["scripted"], indirect=True)
def test_scripted_reads_services_then_answers_citing_the_read(provider: str) -> None:
    first = _ask(provider, "What is failing right now?")
    assert first.outcome == "ok" and first.tool_call is not None
    assert first.tool_call.name == "list_services"
    assert set(first.tool_call.arguments or {}) == {"from", "to"}

    second = _ask(provider, "What is failing right now?\n\nStep s1: list_services read 2 rows")
    assert second.tool_call is None and second.text is not None
    assert json.loads(second.text)["sentences"][0]["cites"] == [{"step": "s1"}]
    assert second.input_tokens and second.input_tokens > 0


@pytest.mark.parametrize("provider", ["hostile"], indirect=True)
def test_hostile_tries_its_tricks_then_answers_with_every_way_out(provider: str) -> None:
    moves = [_ask(provider, "q") for _ in range(3)]
    assert [move.tool_call.name for move in moves if move.tool_call] == [
        "delete_project",
        "search_logs",
        "search_logs",
    ]
    final = _ask(provider, "q", tools=())
    assert final.text is not None and EVIL_HOST in final.text


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="FAKE_PROVIDER_MODE"):
        serve(port=0, mode="friendly")
