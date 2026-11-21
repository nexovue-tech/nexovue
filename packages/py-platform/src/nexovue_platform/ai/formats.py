"""The three wire formats (FD3; design, *Provider interface*), each a pure `build` and `parse`.

Field names were read from each provider's documentation on 2026-11-11 (status, *Validation*,
Step 4), and none has met a real response yet: a format shows on the settings screen only
after a dated real call (`real_calls.py`, AC77).

- `openai_chat`: OpenAI's Chat Completions, and the gateways and local servers that speak it
  (Ollama, vLLM, Gemini's OpenAI-style endpoint). `max_tokens`, not `max_completion_tokens`,
  because Ollama's compatibility list names only the former (unverified for every gateway).
- `anthropic_messages`: Claude's own Messages API, `output_config.format` for the schema.
  Version `2026-10-21`, which also refuses `temperature`; nothing here sends one.
- `bedrock_converse`: Amazon Bedrock's Converse, with a Bedrock API key as a bearer token
  (foundation, section 2). AWS signing is plan T4.9's spike, not built.

Every 200 is read defensively: a body that does not fit is `invalid`, never an exception, and
a parse never copies the provider's own words anywhere but `text`.
"""

from __future__ import annotations

import json
from typing import Any, Final
from urllib.parse import quote

from nexovue_platform.ai.contract import (
    AIRequest,
    AIResponse,
    HttpCall,
    Outcome,
    WireFormat,
    canonical_json,
    count,
    json_object,
    outcome_of_status,
    reported_model,
)

_JSON: Final = ("Content-Type", "application/json")
_ACCEPT: Final = ("Accept", "application/json")
ANTHROPIC_VERSION: Final = "2026-10-21"


def _bearer(key: str | None) -> tuple[tuple[str, str], ...]:
    return (("Authorization", f"Bearer {key}"),) if key else ()


def _joined(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text_of(blocks: object) -> str | None:
    """The text blocks of a content list, joined; None when there are none."""
    if not isinstance(blocks, list):
        return None
    texts = [
        block["text"]
        for block in blocks
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "".join(texts) if texts else None


class OpenAIChat:
    name: Final = "openai_chat"

    def build(self, request: AIRequest, *, model: str, base_url: str, key: str | None) -> HttpCall:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.instructions},
                {"role": "user", "content": request.user_text()},
            ],
            "max_tokens": request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": True,
                    "schema": dict(request.response_schema),
                },
            },
        }
        return HttpCall(
            method="POST",
            url=_joined(base_url, "/chat/completions"),
            headers=(_JSON, _ACCEPT, *_bearer(key)),
            body=canonical_json(body),
        )

    def parse(self, status: int, body: bytes) -> AIResponse:
        document = json_object(body)
        failed = outcome_of_status(status)
        if failed is not None:
            error = (document or {}).get("error")
            code = error.get("code") if isinstance(error, dict) else None
            return AIResponse("too_long" if code == "context_length_exceeded" else failed)
        if document is None:
            return AIResponse("invalid")
        usage = _object(document.get("usage"))
        choices = document.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(choice, dict) or not isinstance(message, dict):
            return AIResponse("invalid")
        finish = choice.get("finish_reason")
        outcome: Outcome
        if message.get("refusal") or finish == "content_filter":
            outcome = "refused"
        elif finish == "length":
            outcome = "too_long"
        elif isinstance(message.get("content"), str):
            outcome = "ok"
        else:
            outcome = "invalid"
        return AIResponse(
            outcome=outcome,
            text=message["content"] if outcome == "ok" else None,
            input_tokens=count(usage.get("prompt_tokens")),
            output_tokens=count(usage.get("completion_tokens")),
            model_reported=reported_model(document.get("model")),
        )


class AnthropicMessages:
    name: Final = "anthropic_messages"

    def build(self, request: AIRequest, *, model: str, base_url: str, key: str | None) -> HttpCall:
        body = {
            "model": model,
            "max_tokens": request.max_output_tokens,
            "system": request.instructions,
            "messages": [{"role": "user", "content": request.user_text()}],
            "output_config": {
                "format": {"type": "json_schema", "schema": dict(request.response_schema)}
            },
        }
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

    def parse(self, status: int, body: bytes) -> AIResponse:
        failed = outcome_of_status(status)
        if failed is not None:
            return AIResponse(failed)
        document = json_object(body)
        if document is None:
            return AIResponse("invalid")
        usage = _object(document.get("usage"))
        stop = document.get("stop_reason")
        text = _text_of(document.get("content"))
        # A refusal is a 200 and may not fit the schema (research, section 5).
        outcome: Outcome
        if stop == "refusal":
            outcome = "refused"
        elif stop == "max_tokens":
            outcome = "too_long"
        elif text is not None:
            outcome = "ok"
        else:
            outcome = "invalid"
        return AIResponse(
            outcome=outcome,
            text=text if outcome == "ok" else None,
            input_tokens=count(usage.get("input_tokens")),
            output_tokens=count(usage.get("output_tokens")),
            model_reported=reported_model(document.get("model")),
        )


class BedrockConverse:
    name: Final = "bedrock_converse"

    def build(self, request: AIRequest, *, model: str, base_url: str, key: str | None) -> HttpCall:
        body = {
            "system": [{"text": request.instructions}],
            "messages": [{"role": "user", "content": [{"text": request.user_text()}]}],
            "inferenceConfig": {"maxTokens": request.max_output_tokens},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            # Converse takes the schema as a JSON string.
                            "schema": json.dumps(dict(request.response_schema), sort_keys=True),
                            "name": request.schema_name,
                        }
                    },
                }
            },
        }
        # The model id sits in the path, so it is percent-encoded whole (FR23).
        return HttpCall(
            method="POST",
            url=_joined(base_url, f"/model/{quote(model, safe='')}/converse"),
            headers=(_JSON, _ACCEPT, *_bearer(key)),
            body=canonical_json(body),
        )

    def parse(self, status: int, body: bytes) -> AIResponse:
        failed = outcome_of_status(status)
        if failed is not None:
            return AIResponse(failed)
        document = json_object(body)
        if document is None:
            return AIResponse("invalid")
        usage = _object(document.get("usage"))
        output = document.get("output")
        message = output.get("message") if isinstance(output, dict) else None
        text = _text_of(message.get("content")) if isinstance(message, dict) else None
        stop = document.get("stopReason")
        outcome: Outcome
        if stop in ("guardrail_intervened", "content_filtered"):
            outcome = "refused"
        elif stop in ("max_tokens", "model_context_window_exceeded"):
            outcome = "too_long"
        elif stop == "malformed_model_output" or text is None:
            outcome = "invalid"
        else:
            outcome = "ok"
        return AIResponse(
            outcome=outcome,
            text=text if outcome == "ok" else None,
            input_tokens=count(usage.get("inputTokens")),
            output_tokens=count(usage.get("outputTokens")),
        )


FORMATS: Final[dict[str, WireFormat]] = {
    fmt.name: fmt for fmt in (OpenAIChat(), AnthropicMessages(), BedrockConverse())
}


def wire_format(name: str) -> WireFormat:
    """The format a connection names; a name outside the closed list is a bug upstream."""
    return FORMATS[name]
