"""What crosses the provider interface (foundation FD3): a request, its bytes, a response.

`AIRequest` has no field for temperature, tool choice or top-p, on purpose: several current
Claude models refuse them, and the shared interface must work for every format. F3's own
request holds trusted parts only; `Untrusted` parts arrive with `untrusted.py` (plan T5.1),
for F4 and F5.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

Purpose = Literal["explain", "check", "chat", "fix"]

# How a call ended, as one word (FD3). `ok` is the only success; `invalid` is a 200 whose
# body could not be read; `not_found` a model or path the provider does not know; and
# `bad_request` a request it refused as malformed (a 400), which a check must tell apart
# from a bad key.
Outcome = Literal[
    "ok",
    "refused",
    "too_long",
    "limited",
    "unavailable",
    "bad_key",
    "not_found",
    "bad_request",
    "invalid",
]
OUTCOMES: Final[tuple[Outcome, ...]] = (
    "ok",
    "refused",
    "too_long",
    "limited",
    "unavailable",
    "bad_key",
    "not_found",
    "bad_request",
    "invalid",
)

# A provider's reported model name reaches a page or a run row only if it fits (AC96).
REPORTED_MODEL: Final = re.compile(r"[A-Za-z0-9._:/-]{1,80}")
# A count a server did not report is estimated from bytes, cautiously (design, step 3).
BYTES_PER_TOKEN: Final = 3


@dataclass(frozen=True, slots=True)
class Trusted:
    """Text nexovue's own code wrote: instructions' companions, or checked facts as JSON."""

    text: str


@dataclass(frozen=True, slots=True)
class AIRequest:
    """One model call, in no provider's words."""

    instructions: str
    parts: tuple[Trusted, ...]
    # A small JSON schema the answer must fit. Used where a format supports it and never
    # relied on: nexovue validates every answer itself (FD3).
    response_schema: Mapping[str, Any]
    # The schema's name, where a format asks for one (`openai_chat`, `bedrock_converse`).
    schema_name: str
    max_output_tokens: int
    purpose: Purpose

    def __post_init__(self) -> None:
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        if not all(isinstance(part, Trusted) for part in self.parts):
            raise TypeError("F3's request holds trusted parts only")

    def user_text(self) -> str:
        return "\n\n".join(part.text for part in self.parts)


@dataclass(frozen=True, slots=True)
class HttpCall:
    """The exact request: what the transport sends, and what the preview shows."""

    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def sha256(self) -> str:
        """The run row's `request_sha256`: of the body, which carries no key (AC36)."""
        return hashlib.sha256(self.body).hexdigest()


@dataclass(frozen=True, slots=True)
class AIResponse:
    outcome: Outcome
    text: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    # True when a count was estimated because the server reported none.
    tokens_estimated: bool = False
    model_reported: str | None = None

    def with_counts(self, *, request_body: bytes) -> AIResponse:
        """Missing counts estimated from bytes, and flagged (FR39)."""
        if self.input_tokens is not None and self.output_tokens is not None:
            return self
        input_tokens = self.input_tokens
        output_tokens = self.output_tokens
        if input_tokens is None:
            input_tokens = estimate_tokens(len(request_body))
        if output_tokens is None:
            output_tokens = estimate_tokens(len((self.text or "").encode()))
        return AIResponse(
            outcome=self.outcome,
            text=self.text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_estimated=True,
            model_reported=self.model_reported,
        )


def estimate_tokens(byte_count: int) -> int:
    return -(-byte_count // BYTES_PER_TOKEN)


def reported_model(value: object) -> str | None:
    """A provider's model name, or None when it does not fit the pattern (AC96)."""
    if isinstance(value, str) and REPORTED_MODEL.fullmatch(value):
        return value
    return None


def canonical_json(document: Any) -> bytes:
    """One byte form for a body, so a rebuild hashes the same (AC36)."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def outcome_of_status(status: int) -> Outcome | None:
    """What a non-200 status means in every format, or None for a 200."""
    if status == 200:
        return None
    if status in (401, 403):
        return "bad_key"
    if status == 404:
        return "not_found"
    if status == 429:
        return "limited"
    if status in (400, 422):
        return "bad_request"
    if status == 413:
        return "too_long"
    return "unavailable"


def json_object(body: bytes) -> dict[str, Any] | None:
    try:
        document = json.loads(body)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def count(value: object) -> int | None:
    """A token count, or None when the server sent no usable one."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class WireFormat(Protocol):
    """A provider's dialect: two pure functions (design, *Provider interface*)."""

    @property
    def name(self) -> str: ...

    def build(
        self, request: AIRequest, *, model: str, base_url: str, key: str | None
    ) -> HttpCall: ...

    def parse(self, status: int, body: bytes) -> AIResponse: ...
