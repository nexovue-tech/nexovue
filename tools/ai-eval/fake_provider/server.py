"""F4 plan T2.0: a fake chat provider on the dev stack's `ai` network, so chat has a provider
to reach without anyone's key or model.

It speaks the OpenAI-compatible chat format (`POST …/chat/completions`), the one F3 and F4
send through `chat_formats.OpenAIChatTools`, and answers from `nexovue_platform.ai.testing`:

- `scripted` (the default): reads services over the last 30 minutes, then answers one
  sentence citing that step. Enough for the panel's whole path, end to end. The answer takes
  `FAKE_PROVIDER_ANSWER_SECONDS` (3 by default), as a model's takes seconds: the panel reads
  a running question every 1.5 s, and the step must be on screen for it to be seen live.
- `hostile`: `HostileProvider`, which assumes an injection already won: every trick as a
  tool call, then every way out in its answer (design, *Injection suite*).
- `week-reads`: AC80's hostile question (F4 plan T8.1, `tools/fault-bed/chat_load.py`): every
  step a 7-day read, logs by a phrase no body holds (so each is read) then traces, until the
  loop's last call offers no tool, when it abstains. The most a question may cost ClickHouse.

Plain HTTP from the standard library: no SDK, no framework (FD3, ADR-047). It holds no key and
checks none. **Development and tests only:** the Compose profile `fake-provider` starts it,
and an operator never points a real install at it.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

from nexovue_platform.ai.chat import ChatRequest, ChatResponse, ToolSpec
from nexovue_platform.ai.contract import Trusted
from nexovue_platform.ai.testing import FAKE_INPUT_TOKENS, HostileProvider, answers, calls

PORT: Final = int(os.environ.get("FAKE_PROVIDER_PORT", "8080"))
MODE: Final = os.environ.get("FAKE_PROVIDER_MODE", "scripted")
MODES: Final = ("scripted", "hostile", "week-reads")
# Two of the panel's reads (`POLL_MS`, 1.5 s, in apps/web/src/features/assistant/api.ts).
ANSWER_SECONDS: Final = float(os.environ.get("FAKE_PROVIDER_ANSWER_SECONDS", "3"))
READ_BACK: Final = timedelta(minutes=30)
MODEL: Final = "nexovue-fake-provider"
SCRIPTED_ANSWER: Final = {
    "sentences": [
        {"text": "Every service that reported is listed in step s1.", "cites": [{"step": "s1"}]}
    ],
    "not_verified": ["Logs were not read."],
    "abstained": None,
}


# The longest window a tool takes (`assistant_tools.MAX_WINDOW`).
WEEK: Final = timedelta(days=7)
WEEK_READS: Final = ("search_logs", "list_traces")
# A phrase no log body holds, so the search reads every body in the week.
NO_SUCH_PHRASE: Final = "nexovue-week-reads-no-such-phrase"
_STEP_LINE: Final = re.compile(r"^Step s\d+:", re.MULTILINE)


def _window(now: datetime) -> tuple[str, str]:
    return ((now - READ_BACK).isoformat(), now.isoformat())


def request_of(body: dict[str, Any]) -> ChatRequest:
    """The parts of an OpenAI-style body a fake needs: the user turn and the tools offered."""
    messages: list[dict[str, Any]] = body.get("messages") or []
    nothing: dict[str, Any] = {}
    user = next((m for m in reversed(messages) if m.get("role") == "user"), nothing)
    system = next((m for m in messages if m.get("role") == "system"), nothing)
    tools = tuple(
        ToolSpec(
            name=tool["function"]["name"],
            description=tool["function"].get("description", ""),
            parameters=tool["function"].get("parameters", {}),
        )
        for tool in body.get("tools") or []
    )
    return ChatRequest(
        instructions=str(system.get("content", "")),
        parts=(Trusted(str(user.get("content", ""))),),
        tools=tools,
        max_output_tokens=int(body.get("max_tokens") or 1),
    )


def scripted(request: ChatRequest, now: datetime) -> ChatResponse:
    """A read first, then one answer citing it. Nothing is remembered between calls: the
    user turn says whether step s1 ran, as nexovue writes every call whole."""
    if request.tools and "Step s1" not in request.user_text():
        start, end = _window(now)
        return calls("list_services", {"from": start, "to": end})
    return answers(SCRIPTED_ANSWER)


def week_reads(request: ChatRequest, now: datetime) -> ChatResponse:
    """A 7-day read at every step, logs and traces in turn, then an abstention once the loop
    offers no tool. Nothing is remembered: the steps already run are counted from the user turn,
    as nexovue writes each call whole."""
    if not request.tools:
        return answers({"sentences": [], "not_verified": [], "abstained": "not_enough_evidence"})
    tool = WEEK_READS[len(_STEP_LINE.findall(request.user_text())) % len(WEEK_READS)]
    arguments = {"from": (now - WEEK).isoformat(), "to": now.isoformat()}
    if tool == "search_logs":
        arguments["q"] = NO_SUCH_PHRASE
    return calls(tool, arguments)


def openai_body(response: ChatResponse) -> dict[str, Any]:
    """`response` in the OpenAI chat-completions shape `OpenAIChatTools.parse` reads."""
    message: dict[str, Any] = {"role": "assistant", "content": response.text}
    finish = "stop"
    if response.tool_call is not None:
        message["tool_calls"] = [
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": response.tool_call.name,
                    "arguments": json.dumps(dict(response.tool_call.arguments or {})),
                },
            }
        ]
        finish = "tool_calls"
    return {
        "model": MODEL,
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {
            "prompt_tokens": response.input_tokens or FAKE_INPUT_TOKENS,
            "completion_tokens": response.output_tokens or 0,
        },
    }


class FakeProvider:
    """One mode's answers. `hostile` keeps its place across calls, as its tricks are a list."""

    def __init__(self, mode: str, *, answer_seconds: float = 0.0) -> None:
        if mode not in MODES:
            raise ValueError(f"FAKE_PROVIDER_MODE is one of {', '.join(MODES)}")
        self.mode = mode
        self.answer_seconds = answer_seconds
        start, end = _window(datetime.now(UTC))
        self._hostile = HostileProvider(window_from=start, window_to=end)
        self._lock = threading.Lock()

    def answer(self, body: dict[str, Any], now: datetime) -> dict[str, Any]:
        request = request_of(body)
        if self.mode == "scripted":
            response = scripted(request, now)
            if response.tool_call is None:
                time.sleep(self.answer_seconds)
            return openai_body(response)
        if self.mode == "week-reads":
            return openai_body(week_reads(request, now))
        with self._lock:
            return openai_body(self._hostile.complete(request))


def handler_for(provider: FakeProvider) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if not self.path.endswith("/chat/completions"):
                self._send(404, {"error": {"message": "only /chat/completions"}})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                self._send(400, {"error": {"message": "not JSON"}})
                return
            self._send(200, provider.answer(body, datetime.now(UTC)))

        def _send(self, status: int, document: dict[str, Any]) -> None:
            encoded = json.dumps(document).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_arguments: Any) -> None:
            """Silent: a request's body is the one thing worth reading, and it is not logged."""

    return Handler


def serve(
    port: int = PORT, mode: str = MODE, *, answer_seconds: float = ANSWER_SECONDS
) -> ThreadingHTTPServer:
    provider = FakeProvider(mode, answer_seconds=answer_seconds)
    return ThreadingHTTPServer(("0.0.0.0", port), handler_for(provider))


if __name__ == "__main__":
    serve().serve_forever()
