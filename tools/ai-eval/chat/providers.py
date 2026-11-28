"""The chat bed's providers (F4 plan T4.1, T4.2).

**Fakes test the harness, never a model** (foundation item 31): their numbers say the bed
and the product's checks work, and nothing about any model.

- `fake-answer-key` reads the question's named tool, then states the value it finds in the
  rows it was sent (the fenced result, read as a model would), citing that step; on a
  question it should not answer, it abstains. A right score from it shows the seed, the
  API, the loop and the scorer agree.
- `fake-abstaining` abstains on everything: "abstains when it should" holds, accuracy is 0.
- `fake-hostile`: `HostileProvider`, which assumes an injection already won (AC51's control).

**The real seam** is the product's own `TransportProvider` (F3's guarded transport and the
connection's chat format), its key given here from the environment and never stored. **It has
not been run against a real model:** every real run is the founder's, after Phase 2.

`Recorded` wraps any of them and keeps what each call sent and got, for scoring and cost.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform.ai.chat import ChatProvider, ChatRequest, ChatResponse
from nexovue_platform.ai.testing import HostileProvider, answers, calls
from nexovue_platform.assistant_tools import TOOLS_BY_NAME

from chat.answers import Question, picked

# F3's fence (`untrusted.py`): the tag line, one sentence, then the text as one JSON string.
_FENCED: Final = re.compile(r'<untrusted-[0-9a-f]+ label="[^"]*">\n[^\n]*\n(.*)\n</untrusted-')

FAKES: Final = ("fake-answer-key", "fake-abstaining", "fake-hostile")


def fenced_rows(request: ChatRequest, rows_at: str) -> list[dict[str, Any]] | None:
    """The rows of the last fenced tool result holding `rows_at`, as the model read them."""
    for found in reversed(_FENCED.findall(request.user_text())):
        try:
            text = json.loads(found)
            document = json.loads(text) if isinstance(text, str) else None
        except ValueError:
            continue
        if isinstance(document, dict) and isinstance(document.get(rows_at), list):
            return [row for row in document[rows_at] if isinstance(row, dict)]
    return None


def abstains(reason: str) -> ChatResponse:
    return answers({"sentences": [], "not_verified": [], "abstained": reason})


def _sentence(question: Question, value: str) -> str:
    if question.kind == "word" and question.category == "stopped_reporting":
        return f"There was {value} from it in the window read."
    if question.kind == "word":
        return f"Its detection state reads {value}."
    return f"The answer is {value}."


@dataclass
class AnswerKey:
    """Reads the named tool, then answers from what it was sent."""

    question: Question
    asked: int = 0

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.asked += 1
        question = self.question
        if question.tool is None or question.pick is None:
            return abstains("outside_what_it_reads")
        if self.asked == 1 and request.tools:
            return calls(question.tool, dict(question.arguments))
        rows = fenced_rows(request, TOOLS_BY_NAME[question.tool].rows_at)
        value = None if rows is None else picked(question.pick, rows)
        if value is None or question.kind == "tenant":
            return abstains("no_data")
        return answers(
            {
                "sentences": [{"text": _sentence(question, value), "cites": [{"step": "s1"}]}],
                "not_verified": [],
                "abstained": None,
            }
        )


@dataclass
class Abstaining:
    def complete(self, request: ChatRequest) -> ChatResponse:
        del request
        return abstains("not_enough_evidence")


def hostile(anchor: datetime) -> HostileProvider:
    return HostileProvider(
        window_from=(anchor - timedelta(hours=1)).isoformat(), window_to=anchor.isoformat()
    )


def fake(name: str, question: Question, anchor: datetime) -> ChatProvider:
    if name == "fake-answer-key":
        return AnswerKey(question)
    if name == "fake-abstaining":
        return Abstaining()
    if name == "fake-hostile":
        return hostile(anchor)
    raise ValueError(f"no fake named {name!r}; known: {', '.join(FAKES)}")


@dataclass(frozen=True, slots=True)
class ForConnection:
    """A provider built from the connection the loop loads: the real seam's shape."""

    build: Callable[[Any], ChatProvider]


@dataclass
class Recorded:
    """Any provider, with each call's request size, response and seconds kept."""

    inner: ChatProvider | ForConnection
    clock: Callable[[], float] = time.monotonic
    calls: list[dict[str, Any]] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)

    def bound(self, connection: Any) -> Recorded:
        """Itself, with a provider built for `connection` when it holds a builder."""
        if isinstance(self.inner, ForConnection):
            self.inner = self.inner.build(connection)
        return self

    def complete(self, request: ChatRequest) -> ChatResponse:
        if isinstance(self.inner, ForConnection):
            raise RuntimeError("the provider was not bound to its connection")
        started = self.clock()
        user_text = request.user_text()
        self.sent.append(user_text)
        record: dict[str, Any] = {
            "request_characters": len(request.instructions) + len(user_text),
            "tools_offered": len(request.tools),
        }
        try:
            response = self.inner.complete(request)
        except Exception as failed:
            record.update(outcome="raised", error=type(failed).__name__)
            record["seconds"] = self.clock() - started
            self.calls.append(record)
            raise
        record.update(
            outcome=response.outcome,
            text=response.text,
            tool=None if response.tool_call is None else response.tool_call.name,
            arguments=None if response.tool_call is None else response.tool_call.arguments,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            tokens_estimated=response.tokens_estimated,
            seconds=self.clock() - started,
        )
        self.calls.append(record)
        return response
