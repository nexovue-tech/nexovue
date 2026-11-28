"""F4 plan T2.7a: tool calling in F3's three wire formats, each a pure `build` and `parse`.

The same endpoints, headers and token fields as F3's `formats.py`, whose helpers this reuses.
Only how tools are offered, and how a call comes back, are new:

- `openai_chat`: `tools: [{type: function, function: {name, description, parameters}}]`;
  a call is `message.tool_calls[].function` with `arguments` as a JSON **string**. From
  memory of OpenAI's reference: its page could not be reached from this session on
  2026-11-11 (status, *Validation*), so this is **unverified** until a real call.
- `anthropic_messages`: `tools: [{name, description, input_schema}]`; a call is a
  `tool_use` content block with `input` an object. Read from Anthropic's *Define tools*
  page on 2026-11-11.
- `bedrock_converse`: `toolConfig.tools[].toolSpec{name, description, inputSchema.json}`;
  a call is a `toolUse` content block. From memory: AWS's page could not be reached on
  2026-11-11, so **unverified** until a real call.

A response carrying a call and text together uses the call (design, section 6); one that
ignores the tools has its text returned for the loop to validate as a final answer (AC94).
Chat sends no response schema: nexovue validates every answer itself, and a schema beside
tools is a pairing not every provider accepts (unverified, so not risked).
"""

from __future__ import annotations

from typing import Any, Final
from urllib.parse import quote

from nexovue_platform.ai.chat import ChatFormat, ChatRequest, ChatResponse, ToolCall, arguments_of
from nexovue_platform.ai.contract import (
    HttpCall,
    Outcome,
    canonical_json,
    count,
    json_object,
    outcome_of_status,
    reported_model,
)
from nexovue_platform.ai.formats import (
    _ACCEPT,
    _JSON,
    ANTHROPIC_VERSION,
    _bearer,
    _joined,
    _object,
    _text_of,
)


def _first(values: object) -> dict[str, Any] | None:
    if isinstance(values, list):
        for value in values:
            if isinstance(value, dict):
                return value
    return None


def _call(name: object, arguments: object) -> ToolCall | None:
    if not isinstance(name, str) or not name:
        return None
    return ToolCall(name=name, arguments=arguments_of(arguments))


class OpenAIChatTools:
    name: Final = "openai_chat"

    def build(
        self, request: ChatRequest, *, model: str, base_url: str, key: str | None
    ) -> HttpCall:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.instructions},
                {"role": "user", "content": request.user_text()},
            ],
            "max_tokens": request.max_output_tokens,
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.parameters),
                    },
                }
                for tool in request.tools
            ]
        return HttpCall(
            method="POST",
            url=_joined(base_url, "/chat/completions"),
            headers=(_JSON, _ACCEPT, *_bearer(key)),
            body=canonical_json(body),
        )

    def parse(self, status: int, body: bytes) -> ChatResponse:
        document = json_object(body)
        failed = outcome_of_status(status)
        if failed is not None:
            error = (document or {}).get("error")
            code = error.get("code") if isinstance(error, dict) else None
            return ChatResponse("too_long" if code == "context_length_exceeded" else failed)
        if document is None:
            return ChatResponse("invalid")
        usage = _object(document.get("usage"))
        choice = _first(document.get("choices"))
        message = choice.get("message") if choice is not None else None
        if choice is None or not isinstance(message, dict):
            return ChatResponse("invalid")
        function = _object(_object(_first(message.get("tool_calls"))).get("function"))
        call = _call(function.get("name"), function.get("arguments"))
        content = message.get("content")
        finish = choice.get("finish_reason")
        outcome: Outcome
        if message.get("refusal") or finish == "content_filter":
            outcome = "refused"
        elif finish == "length":
            outcome = "too_long"
        elif call is not None or isinstance(content, str):
            outcome = "ok"
        else:
            outcome = "invalid"
        return ChatResponse(
            outcome=outcome,
            text=content if outcome == "ok" and call is None and isinstance(content, str) else None,
            tool_call=call if outcome == "ok" else None,
            input_tokens=count(usage.get("prompt_tokens")),
            output_tokens=count(usage.get("completion_tokens")),
            model_reported=reported_model(document.get("model")),
        )


class AnthropicMessagesTools:
    name: Final = "anthropic_messages"

    def build(
        self, request: ChatRequest, *, model: str, base_url: str, key: str | None
    ) -> HttpCall:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_output_tokens,
            "system": request.instructions,
            "messages": [{"role": "user", "content": request.user_text()}],
        }
        if request.tools:
            body["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": dict(tool.parameters),
                }
                for tool in request.tools
            ]
        headers: tuple[tuple[str, str], ...] = (
            _JSON,
            _ACCEPT,
            ("anthropic-version", ANTHROPIC_VERSION),
        )
        if key:
            headers = (*headers, ("x-api-key", key))
        return HttpCall(
            method="POST",
            url=_joined(base_url, "/v1/messages"),
            headers=headers,
            body=canonical_json(body),
        )

    def parse(self, status: int, body: bytes) -> ChatResponse:
        failed = outcome_of_status(status)
        if failed is not None:
            return ChatResponse(failed)
        document = json_object(body)
        if document is None:
            return ChatResponse("invalid")
        usage = _object(document.get("usage"))
        blocks = document.get("content")
        call: ToolCall | None = None
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    call = _call(block.get("name"), block.get("input"))
                    break
        text = _text_of(blocks)
        stop = document.get("stop_reason")
        outcome: Outcome
        if stop == "refusal":
            outcome = "refused"
        elif stop == "max_tokens":
            outcome = "too_long"
        elif call is not None or text is not None:
            outcome = "ok"
        else:
            outcome = "invalid"
        return ChatResponse(
            outcome=outcome,
            text=text if outcome == "ok" and call is None else None,
            tool_call=call if outcome == "ok" else None,
            input_tokens=count(usage.get("input_tokens")),
            output_tokens=count(usage.get("output_tokens")),
            model_reported=reported_model(document.get("model")),
        )


class BedrockConverseTools:
    name: Final = "bedrock_converse"

    def build(
        self, request: ChatRequest, *, model: str, base_url: str, key: str | None
    ) -> HttpCall:
        body: dict[str, Any] = {
            "system": [{"text": request.instructions}],
            "messages": [{"role": "user", "content": [{"text": request.user_text()}]}],
            "inferenceConfig": {"maxTokens": request.max_output_tokens},
        }
        if request.tools:
            body["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": {"json": dict(tool.parameters)},
                        }
                    }
                    for tool in request.tools
                ]
            }
        return HttpCall(
            method="POST",
            url=_joined(base_url, f"/model/{quote(model, safe='')}/converse"),
            headers=(_JSON, _ACCEPT, *_bearer(key)),
            body=canonical_json(body),
        )

    def parse(self, status: int, body: bytes) -> ChatResponse:
        failed = outcome_of_status(status)
        if failed is not None:
            return ChatResponse(failed)
        document = json_object(body)
        if document is None:
            return ChatResponse("invalid")
        usage = _object(document.get("usage"))
        message = _object(_object(document.get("output")).get("message"))
        content = message.get("content")
        call: ToolCall | None = None
        if isinstance(content, list):
            for block in content:
                use = block.get("toolUse") if isinstance(block, dict) else None
                if isinstance(use, dict):
                    call = _call(use.get("name"), use.get("input"))
                    break
        text = _text_of(content)
        stop = document.get("stopReason")
        outcome: Outcome
        if stop in ("guardrail_intervened", "content_filtered"):
            outcome = "refused"
        elif stop in ("max_tokens", "model_context_window_exceeded"):
            outcome = "too_long"
        elif call is not None or (text is not None and stop != "malformed_model_output"):
            outcome = "ok"
        else:
            outcome = "invalid"
        return ChatResponse(
            outcome=outcome,
            text=text if outcome == "ok" and call is None else None,
            tool_call=call if outcome == "ok" else None,
            input_tokens=count(usage.get("inputTokens")),
            output_tokens=count(usage.get("outputTokens")),
        )


CHAT_FORMATS: Final[dict[str, ChatFormat]] = {
    fmt.name: fmt for fmt in (OpenAIChatTools(), AnthropicMessagesTools(), BedrockConverseTools())
}


def chat_format(name: str) -> ChatFormat:
    """The chat dialect a connection names; a name outside the closed list is a bug upstream."""
    return CHAT_FORMATS[name]
