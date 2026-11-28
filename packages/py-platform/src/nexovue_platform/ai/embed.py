"""F4 T6.0: embeddings in F3's provider-interface style, each format a pure `build` and
`parse` (design, section 6, *Provider differences*; D81; ADR-047: plain HTTP, no SDK).

A connection's wire format decides how it embeds, or that it cannot:

- `openai_chat` → `openai_embeddings`: `POST {base}/embeddings` with `model`, `input` as a list
  of strings and `encoding_format: float`; the answer is `data[].embedding` with each item's
  `index`, and `usage.prompt_tokens`. Read on 2026-11-21 from OpenAI's own Python library's
  generated types (`embedding_create_params.py`, `create_embedding_response.py`,
  `embedding.py`), since its reference page could not be reached from this session. Ollama's
  compatibility page lists `/v1/embeddings` with `input` as a string or a list of strings
  (read the same day from its documentation's source on GitHub), so a local model on Ollama
  takes this format too.
- `bedrock_converse` → `bedrock_titan`: `POST {base}/model/{model}/invoke` with `inputText`, one
  text a call, answered by `embedding` and `inputTextTokenCount`. The path is AWS's
  InvokeModel reference, the fields AWS's own Titan Text Embeddings V2 example, both read on
  2026-11-21. Converse is a chat API and cannot embed (D81).
- `anthropic_messages` → none: Anthropic has no embedding model; an organization adds a
  second connection for embeddings only (D61).

**None of these has met a real response** (status, *Step 6*): every fixture is written from
documentation until the founder's real call (foundation item 31). Every 200 is read
defensively: a body that does not fit is `invalid`, never an exception, and a vector is
accepted only as finite numbers of one length, the same for every text of the call.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol
from urllib.parse import quote

from nexovue_platform.ai.connection import consent_fingerprint
from nexovue_platform.ai.contract import (
    HttpCall,
    Outcome,
    canonical_json,
    count,
    estimate_tokens,
    json_object,
    outcome_of_status,
)
from nexovue_platform.ai.formats import _ACCEPT, _JSON, _bearer, _joined

# pgvector stores at most 16,000 dimensions in a `vector` (its README, read 2026-11-21); a
# reply claiming more could never be stored, so it is refused as invalid here.
MAX_DIMENSIONS: Final = 16_000
# What one call carries at most (guesses under each provider's documented limits: OpenAI's
# 2,048 inputs; Titan's one text). A chunk is at most 2,000 characters (`knowledge.py`).
OPENAI_BATCH: Final = 64
TITAN_BATCH: Final = 1


@dataclass(frozen=True, slots=True)
class EmbedResponse:
    """One call's vectors, in the order of its texts, or the one word for why there are none."""

    outcome: Outcome
    vectors: tuple[tuple[float, ...], ...] = ()
    input_tokens: int | None = None
    tokens_estimated: bool = False

    def with_counts(self, *, request_body: bytes) -> EmbedResponse:
        """A missing count estimated from the request's bytes, and flagged, as F3 does."""
        if self.input_tokens is not None:
            return self
        return EmbedResponse(
            outcome=self.outcome,
            vectors=self.vectors,
            input_tokens=estimate_tokens(len(request_body)),
            tokens_estimated=True,
        )

    @property
    def dimensions(self) -> int | None:
        return len(self.vectors[0]) if self.vectors else None


class EmbedFormat(Protocol):
    """A provider's dialect for embeddings: two pure functions, as F3's `WireFormat`."""

    @property
    def name(self) -> str: ...

    @property
    def batch(self) -> int: ...

    def build(
        self, texts: tuple[str, ...], *, model: str, base_url: str, key: str | None
    ) -> HttpCall: ...

    def parse(self, status: int, body: bytes, *, expected: int) -> EmbedResponse: ...


def vector_of(value: object) -> tuple[float, ...] | None:
    """A vector as finite numbers, or None when anything in it is not one."""
    if not isinstance(value, list) or not 0 < len(value) <= MAX_DIMENSIONS:
        return None
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float):
            return None
        number = float(item)
        if not math.isfinite(number):
            return None
        numbers.append(number)
    return tuple(numbers)


def _all_one_length(vectors: list[tuple[float, ...]]) -> bool:
    return len({len(vector) for vector in vectors}) == 1


class OpenAIEmbeddings:
    name: Final = "openai_embeddings"
    batch: Final = OPENAI_BATCH

    def build(
        self, texts: tuple[str, ...], *, model: str, base_url: str, key: str | None
    ) -> HttpCall:
        if not 0 < len(texts) <= self.batch:
            raise ValueError("an embedding call carries one to OPENAI_BATCH texts")
        body = {"model": model, "input": list(texts), "encoding_format": "float"}
        return HttpCall(
            method="POST",
            url=_joined(base_url, "/embeddings"),
            headers=(_JSON, _ACCEPT, *_bearer(key)),
            body=canonical_json(body),
        )

    def parse(self, status: int, body: bytes, *, expected: int) -> EmbedResponse:
        failed = outcome_of_status(status)
        if failed is not None:
            return EmbedResponse(failed)
        document = json_object(body)
        data = None if document is None else document.get("data")
        if not isinstance(data, list) or len(data) != expected:
            return EmbedResponse("invalid")
        placed: dict[int, tuple[float, ...]] = {}
        for item in data:
            index = item.get("index") if isinstance(item, dict) else None
            vector = vector_of(item.get("embedding")) if isinstance(item, dict) else None
            if not isinstance(index, int) or isinstance(index, bool) or vector is None:
                return EmbedResponse("invalid")
            placed[index] = vector
        if sorted(placed) != list(range(expected)):
            return EmbedResponse("invalid")
        vectors = [placed[position] for position in range(expected)]
        if not _all_one_length(vectors):
            return EmbedResponse("invalid")
        usage = document.get("usage") if document is not None else None
        tokens = count(usage.get("prompt_tokens")) if isinstance(usage, dict) else None
        return EmbedResponse("ok", vectors=tuple(vectors), input_tokens=tokens)


class BedrockTitan:
    name: Final = "bedrock_titan"
    batch: Final = TITAN_BATCH

    def build(
        self, texts: tuple[str, ...], *, model: str, base_url: str, key: str | None
    ) -> HttpCall:
        if len(texts) != self.batch:
            raise ValueError("Titan embeds one text a call")
        # The model id sits in the path, so it is percent-encoded whole, as Converse's is.
        return HttpCall(
            method="POST",
            url=_joined(base_url, f"/model/{quote(model, safe='')}/invoke"),
            headers=(_JSON, _ACCEPT, *_bearer(key)),
            body=canonical_json({"inputText": texts[0]}),
        )

    def parse(self, status: int, body: bytes, *, expected: int) -> EmbedResponse:
        failed = outcome_of_status(status)
        if failed is not None:
            return EmbedResponse(failed)
        document = json_object(body)
        vector = None if document is None else vector_of(document.get("embedding"))
        if expected != 1 or document is None or vector is None:
            return EmbedResponse("invalid")
        return EmbedResponse(
            "ok", vectors=(vector,), input_tokens=count(document.get("inputTextTokenCount"))
        )


# A chat connection's wire format, and how it embeds; Anthropic's has no entry (D61).
EMBED_FORMATS: Final[dict[str, EmbedFormat]] = {
    "openai_chat": OpenAIEmbeddings(),
    "bedrock_converse": BedrockTitan(),
}


def embed_format(wire_format: str) -> EmbedFormat | None:
    """How a connection of this wire format embeds, or None when it cannot."""
    return EMBED_FORMATS.get(wire_format)


def vector_literal(vector: tuple[float, ...]) -> str:
    """pgvector's text form, `[1,2,3]`, cast in SQL; no Python package is needed (design)."""
    return "[" + ",".join(repr(number) for number in vector) + "]"


def batches(items: list[Any], size: int) -> list[list[Any]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


# ---------------------------------------------------------------- the embedding connection

# The purpose an embedding-only key is sealed under, under the `ai` key ring (D61).
EMBEDDING_KEY_PURPOSE: Final = "assistant-embedding-key"
# Where vectors come from: the chat connection, or a second one for embeddings only.
CHAT_SOURCE: Final = "chat"
OWN_SOURCE: Final = "own"


def embedding_key_owner(org_id: str, base_url: str) -> str:
    """Whom an embedding-only key is sealed for: the organization and a hash of the address,
    so a key sealed for one address cannot open for another (as F3's `key_owner`)."""
    return f"{org_id}:embedding:{hashlib.sha256(base_url.encode()).hexdigest()}"


def embedding_fingerprint(
    *, source: str, wire_format: str, base_url: str, model: str, key_id: str | None
) -> str:
    """What a passing embedding check covers (AC85): a change to any of these needs a new one
    before anything is embedded. Never reads the key."""
    return hashlib.sha256(
        json.dumps(["embedding", source, wire_format, base_url, model, key_id]).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class EmbeddingTarget:
    """Where an organization's texts go to be embedded, and with which model."""

    source: str
    wire_format: str
    base_url: str
    model: str
    key_id: str | None

    @property
    def fingerprint(self) -> str:
        return embedding_fingerprint(
            source=self.source,
            wire_format=self.wire_format,
            base_url=self.base_url,
            model=self.model,
            key_id=self.key_id,
        )

    @property
    def format(self) -> EmbedFormat | None:
        return embed_format(self.wire_format)


# Why nothing is embedded, in the order they are judged; the settings section says each.
KeywordOnly = Literal["no_model", "cannot_embed", "no_consent", "not_tested"]


@dataclass(frozen=True, slots=True)
class EmbeddingSetting:
    """`assistant_settings`' embedding columns, as both the API and the AI role read them."""

    model: str | None
    own_wire_format: str | None = None
    own_base_url: str | None = None
    own_key_id: str | None = None
    own_consented_fingerprint: str | None = None
    checked_fingerprint: str | None = None
    checked_ok: bool | None = None

    def target(
        self, *, chat_wire_format: str, chat_base_url: str, chat_key_id: str | None
    ) -> EmbeddingTarget | None:
        """The second connection when one is saved, else the chat connection's; None with
        no model."""
        if self.model is None:
            return None
        if self.own_wire_format is not None and self.own_base_url is not None:
            return EmbeddingTarget(
                OWN_SOURCE, self.own_wire_format, self.own_base_url, self.model, self.own_key_id
            )
        return EmbeddingTarget(
            CHAT_SOURCE, chat_wire_format, chat_base_url, self.model, chat_key_id
        )

    def own_consent_fingerprint(self) -> str | None:
        """What an owner agrees to for the second connection: F3's consent fingerprint."""
        if self.own_wire_format is None or self.own_base_url is None:
            return None
        return consent_fingerprint(
            wire_format=self.own_wire_format,
            base_url=self.own_base_url,
            region=None,
            key_id=self.own_key_id,
        )

    def keyword_only(self, target: EmbeddingTarget | None) -> KeywordOnly | None:
        """Why nothing may be embedded with this setting, or None when texts may go. Chat's
        own switch, consent and data level are the caller's, read beside it."""
        if target is None:
            return "no_model"
        if target.format is None:
            return "cannot_embed"
        if target.source == OWN_SOURCE and (
            self.own_consented_fingerprint != self.own_consent_fingerprint()
        ):
            return "no_consent"
        if not self.checked_ok or self.checked_fingerprint != target.fingerprint:
            return "not_tested"
        return None
