"""F4 T6.0: the embedding formats, the setting's rules and the fake model (design, section 6;
D61, D81; AC41, AC85). Pure: no network, no provider.

**Every response fixture is written from documentation** (read 2026-11-21: OpenAI's Python
library's generated types, Ollama's compatibility page's source, AWS's InvokeModel reference
and its Titan V2 example). None is a recorded real response: the one real call per format is
the founder's, after Phase 2 (foundation item 31).
"""

from __future__ import annotations

import json
import math
from typing import Any, Final

import pytest
from nexovue_platform.ai.connection import consent_fingerprint
from nexovue_platform.ai.embed import (
    CHAT_SOURCE,
    EMBED_FORMATS,
    MAX_DIMENSIONS,
    OWN_SOURCE,
    BedrockTitan,
    EmbeddingSetting,
    OpenAIEmbeddings,
    embed_format,
    embedding_fingerprint,
    embedding_key_owner,
    vector_literal,
    vector_of,
)
from nexovue_platform.ai.testing import meaning_vector, openai_embeddings_reply

BASE: Final = "https://provider.example/v1"
KEY: Final = "sk-test-not-a-real-key"
CHAT: Final = {"chat_wire_format": "openai_chat", "chat_base_url": BASE, "chat_key_id": "k1"}


def _body(call: Any) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(call.body)
    return document


def test_openai_style_embeds_a_list_of_texts_at_the_embeddings_path() -> None:
    call = OpenAIEmbeddings().build(("one", "two"), model="text-embed", base_url=BASE, key=KEY)
    assert (call.method, call.url) == ("POST", f"{BASE}/embeddings")
    assert _body(call) == {
        "model": "text-embed",
        "input": ["one", "two"],
        "encoding_format": "float",
    }
    assert ("Authorization", f"Bearer {KEY}") in call.headers
    # A local server with no key sends no Authorization header at all.
    keyless = OpenAIEmbeddings().build(("one",), model="nomic-embed-text", base_url=BASE, key=None)
    assert all(name != "Authorization" for name, _ in keyless.headers)


def test_openai_style_reads_vectors_by_index_and_the_prompt_tokens() -> None:
    reply = {
        "object": "list",
        "data": [
            {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
            {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
        ],
        "model": "text-embed",
        "usage": {"prompt_tokens": 7, "total_tokens": 7},
    }
    parsed = OpenAIEmbeddings().parse(200, json.dumps(reply).encode(), expected=2)
    assert parsed.outcome == "ok"
    assert parsed.vectors == ((1.0, 0.0), (0.0, 1.0))
    assert (parsed.input_tokens, parsed.dimensions) == (7, 2)


@pytest.mark.parametrize(
    ("label", "data"),
    [
        ("one vector short", [{"index": 0, "embedding": [1.0]}]),
        ("an index twice", [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [1.0]}]),
        ("two lengths", [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}]),
        ("a word in a vector", [{"index": 0, "embedding": ["x"]}, {"index": 1, "embedding": [1]}]),
        ("a true in a vector", [{"index": 0, "embedding": [True]}, {"index": 1, "embedding": [1]}]),
        ("an empty vector", [{"index": 0, "embedding": []}, {"index": 1, "embedding": []}]),
    ],
)
def test_a_reply_that_does_not_fit_is_invalid_never_an_exception(
    label: str, data: list[dict[str, Any]]
) -> None:
    parsed = OpenAIEmbeddings().parse(200, json.dumps({"data": data}).encode(), expected=2)
    assert (parsed.outcome, parsed.vectors) == ("invalid", ()), label


def test_a_non_finite_or_oversized_vector_is_refused() -> None:
    assert vector_of([1.0, math.inf]) is None
    assert vector_of([math.nan]) is None
    assert vector_of([0.5] * (MAX_DIMENSIONS + 1)) is None
    assert vector_of([1, 2.5]) == (1.0, 2.5)


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (401, "bad_key"),
        (404, "not_found"),
        (429, "limited"),
        (400, "bad_request"),
        (500, "unavailable"),
    ],
)
def test_every_format_reads_a_failed_status_as_f3_s_closed_word(status: int, outcome: str) -> None:
    for fmt in EMBED_FORMATS.values():
        assert fmt.parse(status, b"{}", expected=1).outcome == outcome


def test_a_missing_count_is_estimated_from_the_request_and_flagged() -> None:
    call = OpenAIEmbeddings().build(("a" * 300,), model="m", base_url=BASE, key=None)
    reply = {"data": [{"index": 0, "embedding": [1.0]}]}
    parsed = OpenAIEmbeddings().parse(200, json.dumps(reply).encode(), expected=1)
    counted = parsed.with_counts(request_body=call.body)
    assert counted.tokens_estimated and counted.input_tokens == -(-len(call.body) // 3)
    assert counted.vectors == parsed.vectors


def test_titan_embeds_one_text_through_invoke_model_never_converse() -> None:
    """D81: Bedrock embeds through InvokeModel on Titan; the model id is in the path,
    percent-encoded whole, as Converse's is."""
    call = BedrockTitan().build(
        ("slow checkout",), model="amazon.titan-embed-text-v2:0", base_url=BASE, key=KEY
    )
    assert call.url == f"{BASE}/model/amazon.titan-embed-text-v2%3A0/invoke"
    assert _body(call) == {"inputText": "slow checkout"}
    with pytest.raises(ValueError, match="one text"):
        BedrockTitan().build(("a", "b"), model="m", base_url=BASE, key=KEY)
    reply = {"embedding": [0.25, 0.75], "inputTextTokenCount": 3}
    parsed = BedrockTitan().parse(200, json.dumps(reply).encode(), expected=1)
    assert (parsed.outcome, parsed.vectors, parsed.input_tokens) == ("ok", ((0.25, 0.75),), 3)
    assert BedrockTitan().parse(200, b'{"embedding": "no"}', expected=1).outcome == "invalid"


def test_anthropic_s_format_cannot_embed() -> None:
    """D61: Anthropic has no embedding model; a second connection serves instead."""
    assert embed_format("anthropic_messages") is None
    assert set(EMBED_FORMATS) == {"openai_chat", "bedrock_converse"}
    assert OpenAIEmbeddings().batch > 1 and BedrockTitan().batch == 1


def test_the_vector_text_is_pgvector_s_own_form() -> None:
    assert vector_literal((1.0, -0.5, 2e-05)) == "[1.0,-0.5,2e-05]"


def test_with_no_model_nothing_is_embedded() -> None:
    setting = EmbeddingSetting(model=None)
    assert setting.target(**CHAT) is None
    assert setting.keyword_only(None) == "no_model"


def test_the_chat_connection_serves_unless_a_second_one_is_saved() -> None:
    chat = EmbeddingSetting(model="text-embed")
    target = chat.target(**CHAT)
    assert target is not None
    assert (target.source, target.base_url, target.key_id) == (CHAT_SOURCE, BASE, "k1")
    own = EmbeddingSetting(
        model="text-embed",
        own_wire_format="openai_chat",
        own_base_url="https://embed.example/v1",
        own_key_id="k2",
    )
    owned = own.target(**CHAT)
    assert owned is not None
    assert (owned.source, owned.base_url, owned.key_id) == (
        OWN_SOURCE,
        "https://embed.example/v1",
        "k2",
    )


def test_a_check_counts_only_for_exactly_the_values_it_tested() -> None:
    """AC85: changing the model, the address or the key needs a new passing check."""
    setting = EmbeddingSetting(model="text-embed")
    target = setting.target(**CHAT)
    assert target is not None
    assert setting.keyword_only(target) == "not_tested"
    passed = EmbeddingSetting(
        model="text-embed", checked_fingerprint=target.fingerprint, checked_ok=True
    )
    assert passed.keyword_only(target) is None
    failed = EmbeddingSetting(
        model="text-embed", checked_fingerprint=target.fingerprint, checked_ok=False
    )
    assert failed.keyword_only(target) == "not_tested"
    other_model = EmbeddingSetting(
        model="other", checked_fingerprint=target.fingerprint, checked_ok=True
    )
    assert other_model.keyword_only(other_model.target(**CHAT)) == "not_tested"
    new_key = passed.target(chat_wire_format="openai_chat", chat_base_url=BASE, chat_key_id="k9")
    assert passed.keyword_only(new_key) == "not_tested"


def test_a_second_connection_needs_an_owner_s_own_agreement() -> None:
    """D61: its own consent line, F3's consent fingerprint for that address and key."""
    base = {
        "model": "text-embed",
        "own_wire_format": "openai_chat",
        "own_base_url": "https://embed.example/v1",
        "own_key_id": "k2",
    }
    unagreed = EmbeddingSetting(**base)  # type: ignore[arg-type]
    target = unagreed.target(**CHAT)
    assert target is not None
    assert unagreed.keyword_only(target) == "no_consent"
    agreed_to = consent_fingerprint(
        wire_format="openai_chat", base_url="https://embed.example/v1", region=None, key_id="k2"
    )
    assert unagreed.own_consent_fingerprint() == agreed_to
    agreed = EmbeddingSetting(
        **base,
        own_consented_fingerprint=agreed_to,
        checked_fingerprint=target.fingerprint,
        checked_ok=True,
    )
    assert agreed.keyword_only(target) is None


def test_a_chat_connection_that_cannot_embed_says_so() -> None:
    setting = EmbeddingSetting(model="text-embed")
    target = setting.target(
        chat_wire_format="anthropic_messages", chat_base_url=BASE, chat_key_id=None
    )
    assert setting.keyword_only(target) == "cannot_embed"


def test_fingerprints_and_key_owners_bind_their_address() -> None:
    first = embedding_fingerprint(
        source="chat", wire_format="openai_chat", base_url=BASE, model="m", key_id=None
    )
    assert first != embedding_fingerprint(
        source="own", wire_format="openai_chat", base_url=BASE, model="m", key_id=None
    )
    assert embedding_key_owner("o", BASE) != embedding_key_owner("o", "https://other.example/v1")


def test_the_fake_model_puts_slow_beside_latency_and_apart_from_disk() -> None:
    """The fake behind the judgeable claim: "why is it slow?" finds a runbook that says
    "latency"; it never pretends to be a real model."""

    def cosine(left: list[float], right: list[float]) -> float:
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        return dot / (math.hypot(*left) * math.hypot(*right))

    slow, latency, disk = (
        meaning_vector(text) for text in ("why is it slow?", "High latency", "disk full")
    )
    assert cosine(slow, latency) > cosine(slow, disk)
    assert any(meaning_vector("nothing known here"))
    reply = openai_embeddings_reply(["a", "b"])
    assert [item["index"] for item in reply["data"]] == [0, 1]
