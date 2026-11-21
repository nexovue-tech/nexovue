"""F4: what crosses the provider interface for chat, beside F3's `AIRequest` (design,
*Differences* 12; status, open decision 2).

Chat's request carries tools, which F3's does not. The transport, the guarded client and the
three wire formats' helpers are F3's, unchanged, and so is the fence: every part written
outside nexovue is F3's `Untrusted` (`untrusted.py`), stripped of hidden characters, encoded
as one JSON string and fenced by a tag drawn for that part alone. The fence limits
confusion; it is not a defence on its own (design, *5*).

**Every call is stateless.** nexovue writes the whole user turn each time: the question,
earlier answers and the last tool results, each fenced (design, *The loop*). No
provider-specific tool-result message is ever sent, so the three formats differ only in how
tools are offered and how a call comes back.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

from nexovue_platform.ai.connection import is_private_address
from nexovue_platform.ai.contract import HttpCall, Outcome, Trusted, estimate_tokens
from nexovue_platform.ai.untrusted import Untrusted

# Tool names every format accepts (Anthropic's documented pattern is the narrowest).
TOOL_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool as offered to a model: a name, what it chooses by, and its arguments."""

    name: str
    description: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if TOOL_NAME.fullmatch(self.name) is None:
            raise ValueError(f"tool name {self.name!r} does not fit every format")


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """One call of the loop, in no provider's words. No temperature, tool choice or top-p,
    as for F3: several providers refuse them, and nexovue validates everything itself."""

    instructions: str
    parts: tuple[Trusted | Untrusted, ...]
    tools: tuple[ToolSpec, ...]
    max_output_tokens: int

    def __post_init__(self) -> None:
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("two tools share a name")

    def user_text(self) -> str:
        return "\n\n".join(
            part.text if isinstance(part, Trusted) else part.render() for part in self.parts
        )


@dataclass(frozen=True, slots=True)
class ToolCall:
    """The model asked for a tool. `arguments` is None when they could not be read as an
    object; the loop tells the model so, and it counts as a step."""

    name: str
    arguments: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    outcome: Outcome
    text: str | None = None
    tool_call: ToolCall | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tokens_estimated: bool = False
    model_reported: str | None = None

    def with_counts(self, *, request_body: bytes) -> ChatResponse:
        """Missing counts estimated from bytes, and flagged, as F3 does."""
        if self.input_tokens is not None and self.output_tokens is not None:
            return self
        spoken = (self.text or "") + json.dumps(
            None if self.tool_call is None else dict(self.tool_call.arguments or {})
        )
        return ChatResponse(
            outcome=self.outcome,
            text=self.text,
            tool_call=self.tool_call,
            input_tokens=self.input_tokens
            if self.input_tokens is not None
            else estimate_tokens(len(request_body)),
            output_tokens=self.output_tokens
            if self.output_tokens is not None
            else estimate_tokens(len(spoken.encode())),
            tokens_estimated=True,
            model_reported=self.model_reported,
        )

    @property
    def tokens(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class ChatFormat(Protocol):
    """A provider's dialect for chat: two pure functions, as F3's `WireFormat`."""

    @property
    def name(self) -> str: ...

    def build(
        self, request: ChatRequest, *, model: str, base_url: str, key: str | None
    ) -> HttpCall: ...

    def parse(self, status: int, body: bytes) -> ChatResponse: ...


def arguments_of(value: object) -> Mapping[str, Any] | None:
    """A tool call's arguments as an object, from a JSON string or an object."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


class ChatProvider(Protocol):
    """One model call, as the loop sees it: the real one sends bytes through F3's guarded
    transport; the fakes in `testing.py` answer in process."""

    def complete(self, request: ChatRequest) -> ChatResponse: ...


# The safe name of where answers go, never the address (F4 design, *States*).
PROVIDER_LABELS: Final[dict[str, str]] = {
    "openai_chat": "OpenAI-compatible",
    "anthropic_messages": "Anthropic",
    "bedrock_converse": "Amazon Bedrock",
}
OWN_SERVER_LABEL: Final = "a model on your own server"


def provider_label(wire_format: str, base_url: str) -> str:
    if is_private_address(base_url):
        return OWN_SERVER_LABEL
    return PROVIDER_LABELS.get(wire_format, "your AI provider")
