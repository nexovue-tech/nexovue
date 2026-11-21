"""The three wire formats, the connection rules and the explain request (F3 plan T4.1; AC34,
AC77, AC96). Pure: no network, no provider.

**Every response fixture says where it came from** (AC77). None is a recorded real response
yet: each is "written from documentation", read on 2026-11-11, until a real call is recorded
(`nexovue_platform.ai.real_calls`, needing the founder's free-tier key).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from datetime import date
from typing import Any, Final

import pytest
from nexovue_platform.ai.check_prompt import CHECK_REQUEST
from nexovue_platform.ai.connection import (
    ConnectionRefused,
    check_base_url,
    check_fingerprint,
    check_key,
    check_model,
    consent_fingerprint,
    key_owner,
    provider_host,
)
from nexovue_platform.ai.contract import AIRequest, AIResponse, Trusted, reported_model
from nexovue_platform.ai.explain_prompt import (
    PROMPT_VERSION,
    SAMPLE_MODEL_INPUT,
    SENDABLE_FIELDS,
    explain_request,
    fields_sent,
)
from nexovue_platform.ai.formats import FORMATS, wire_format
from nexovue_platform.ai.real_calls import RealCall, formats_with_a_real_call
from nexovue_platform.ai_records import WIRE_FORMATS
from nexovue_platform.evidence.model_facts import (
    Cause,
    FindingFacts,
    ModelFact,
    ModelFacts,
    NextCheck,
)

FROM_DOCUMENTATION: Final = "written from documentation"
RECORDED_PREFIX: Final = "recorded from a real response on "
BASE: Final = "https://provider.example/v1"
KEY: Final = "sk-test-not-a-real-key"
CONTROLS: Final = ("temperature", "top_p", "top_k", "tool_choice")


@dataclass(frozen=True)
class Fixture:
    wire_format: str
    name: str
    status: int
    body: Any
    outcome: str
    source: str = FROM_DOCUMENTATION


def _openai(content: Any = '{"ok":true}', finish: str = "stop", refusal: Any = None) -> Any:
    return {
        "id": "chatcmpl-1",
        "model": "gpt-4o-mini-2024-07-18",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
            }
        ],
        "usage": {"prompt_tokens": 40, "completion_tokens": 6, "total_tokens": 46},
    }


def _anthropic(stop: str = "end_turn", text: str = '{"ok":true}') -> Any:
    return {
        "id": "msg_1",
        "type": "message",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": {"input_tokens": 41, "output_tokens": 7},
    }


def _bedrock(stop: str = "end_turn", text: str = '{"ok":true}') -> Any:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": stop,
        "usage": {"inputTokens": 42, "outputTokens": 8, "totalTokens": 50},
        "metrics": {"latencyMs": 400},
    }


def _error(kind: str, message: str = "x") -> Any:
    return {"type": "error", "error": {"type": kind, "message": message}}


FIXTURES: Final = (
    Fixture("openai_chat", "ok", 200, _openai(), "ok"),
    Fixture("openai_chat", "refusal", 200, _openai(content=None, refusal="I can't."), "refused"),
    Fixture("openai_chat", "content filter", 200, _openai(finish="content_filter"), "refused"),
    Fixture("openai_chat", "length", 200, _openai(finish="length"), "too_long"),
    Fixture(
        "openai_chat",
        "context too long",
        400,
        {"error": {"code": "context_length_exceeded", "message": "x"}},
        "too_long",
    ),
    Fixture("openai_chat", "bad key", 401, {"error": {"code": "invalid_api_key"}}, "bad_key"),
    Fixture("openai_chat", "no model", 404, {"error": {"code": "model_not_found"}}, "not_found"),
    Fixture("openai_chat", "rate", 429, {"error": {"code": "rate_limit_exceeded"}}, "limited"),
    Fixture("openai_chat", "down", 503, {"error": {"code": "server_error"}}, "unavailable"),
    Fixture("openai_chat", "no choices", 200, {"choices": []}, "invalid"),
    Fixture("anthropic_messages", "ok", 200, _anthropic(), "ok"),
    Fixture("anthropic_messages", "refusal as a 200", 200, _anthropic("refusal", "No"), "refused"),
    Fixture("anthropic_messages", "max tokens", 200, _anthropic("max_tokens"), "too_long"),
    Fixture("anthropic_messages", "bad key", 401, _error("authentication_error"), "bad_key"),
    Fixture("anthropic_messages", "forbidden", 403, _error("permission_error"), "bad_key"),
    Fixture("anthropic_messages", "no model", 404, _error("not_found_error"), "not_found"),
    Fixture("anthropic_messages", "malformed", 400, _error("invalid_request_error"), "bad_request"),
    Fixture("anthropic_messages", "rate", 429, _error("rate_limit_error"), "limited"),
    Fixture("anthropic_messages", "overloaded", 529, _error("overloaded_error"), "unavailable"),
    Fixture(
        "anthropic_messages", "no text", 200, {"content": [], "stop_reason": "end_turn"}, "invalid"
    ),
    Fixture("bedrock_converse", "ok", 200, _bedrock(), "ok"),
    Fixture("bedrock_converse", "guardrail", 200, _bedrock("guardrail_intervened"), "refused"),
    Fixture("bedrock_converse", "filtered", 200, _bedrock("content_filtered"), "refused"),
    Fixture("bedrock_converse", "max tokens", 200, _bedrock("max_tokens"), "too_long"),
    Fixture(
        "bedrock_converse", "window", 200, _bedrock("model_context_window_exceeded"), "too_long"
    ),
    Fixture(
        "bedrock_converse", "malformed output", 200, _bedrock("malformed_model_output"), "invalid"
    ),
    Fixture("bedrock_converse", "denied", 403, {"message": "AccessDeniedException"}, "bad_key"),
    Fixture("bedrock_converse", "no model", 404, {"message": "ResourceNotFound"}, "not_found"),
    Fixture(
        "bedrock_converse", "validation", 400, {"message": "ValidationException"}, "bad_request"
    ),
    Fixture("bedrock_converse", "throttled", 429, {"message": "ThrottlingException"}, "limited"),
    Fixture(
        "bedrock_converse", "unavailable", 503, {"message": "ServiceUnavailable"}, "unavailable"
    ),
)


def _body(fixture: Fixture) -> bytes:
    return json.dumps(fixture.body).encode()


# ------------------------------------------------------------------ fixtures


def test_every_fixture_says_where_it_came_from() -> None:
    """AC77: a recorded shape names its date; anything else says it was written from docs."""
    for fixture in FIXTURES:
        recorded = fixture.source.startswith(RECORDED_PREFIX)
        assert fixture.source == FROM_DOCUMENTATION or recorded, fixture.name
        if recorded:
            date.fromisoformat(fixture.source.removeprefix(RECORDED_PREFIX))


def test_every_format_has_fixtures_for_success_refusal_and_each_failure() -> None:
    for name in WIRE_FORMATS:
        outcomes = {fixture.outcome for fixture in FIXTURES if fixture.wire_format == name}
        assert {"ok", "refused", "too_long", "bad_key", "not_found", "limited"} <= outcomes, name
        assert {"unavailable", "invalid"} <= outcomes, name


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f"{f.wire_format}:{f.name}")
def test_each_response_shape_reads_as_its_outcome(fixture: Fixture) -> None:
    response = wire_format(fixture.wire_format).parse(fixture.status, _body(fixture))
    assert response.outcome == fixture.outcome
    if fixture.outcome == "ok":
        assert response.text == '{"ok":true}'
        assert response.input_tokens and response.output_tokens
    else:
        assert response.text is None, "a failed call's words never travel on"


@pytest.mark.parametrize("name", WIRE_FORMATS)
def test_a_body_that_is_not_json_is_invalid_never_an_exception(name: str) -> None:
    for body in (b"", b"<html>gateway</html>", b"[1,2]", b'"text"'):
        assert wire_format(name).parse(200, body).outcome == "invalid"


# --------------------------------------------------------------------- build


@pytest.mark.parametrize("name", WIRE_FORMATS)
def test_a_request_holds_no_sampling_or_tool_control(name: str) -> None:
    """FD3: no temperature, top-p or tool choice, which several Claude models refuse."""
    call = wire_format(name).build(CHECK_REQUEST, model="m", base_url=BASE, key=KEY)
    body = call.body.decode()
    for control in CONTROLS:
        assert f'"{control}"' not in body, control


@pytest.mark.parametrize("name", WIRE_FORMATS)
def test_the_same_input_builds_the_same_bytes(name: str) -> None:
    """AC36: the preview, the run and "View what was sent" rebuild one byte form."""
    first = wire_format(name).build(
        explain_request(SAMPLE_MODEL_INPUT), model="m", base_url=BASE, key=KEY
    )
    again = wire_format(name).build(
        explain_request(json.loads(json.dumps(SAMPLE_MODEL_INPUT))),
        model="m",
        base_url=BASE + "/",
        key=None,
    )
    assert first.body == again.body
    assert first.sha256() == again.sha256()
    assert KEY.encode() not in first.body, "the key travels in a header, never the body"


def test_openai_chat_sends_a_bearer_key_and_a_strict_schema() -> None:
    call = wire_format("openai_chat").build(
        CHECK_REQUEST, model="llama3.1:8b", base_url=BASE, key=KEY
    )
    body = json.loads(call.body)
    assert call.url == f"{BASE}/chat/completions"
    assert ("Authorization", f"Bearer {KEY}") in call.headers
    assert body["messages"][0] == {"role": "system", "content": CHECK_REQUEST.instructions}
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["max_tokens"] == CHECK_REQUEST.max_output_tokens


def test_anthropic_messages_names_its_version_and_puts_the_schema_in_output_config() -> None:
    call = wire_format("anthropic_messages").build(
        CHECK_REQUEST, model="claude-haiku-5-5", base_url="https://api.anthropic.com", key=KEY
    )
    body = json.loads(call.body)
    assert call.url == "https://api.anthropic.com/v1/messages"
    assert ("x-api-key", KEY) in call.headers
    assert ("anthropic-version", "2026-10-21") in call.headers
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["system"] == CHECK_REQUEST.instructions


def test_bedrock_converse_encodes_the_model_in_the_path() -> None:
    """FR23: a model id is percent-encoded in a path, so it cannot add a segment (AC34)."""
    call = wire_format("bedrock_converse").build(
        CHECK_REQUEST,
        model="anthropic.claude-haiku-5-5-v1:0/../../other",
        base_url="https://bedrock-runtime.ap-south-1.amazonaws.com",
        key=KEY,
    )
    assert call.url == (
        "https://bedrock-runtime.ap-south-1.amazonaws.com/model/"
        "anthropic.claude-haiku-5-5-v1%3A0%2F..%2F..%2Fother/converse"
    )
    body = json.loads(call.body)
    structure = body["outputConfig"]["textFormat"]["structure"]["jsonSchema"]
    assert json.loads(structure["schema"]) == dict(CHECK_REQUEST.response_schema)


@pytest.mark.parametrize("name", WIRE_FORMATS)
def test_no_key_means_no_credential_header(name: str) -> None:
    """A local server with no key gets no credential header at all (AC34's URL change)."""
    call = wire_format(name).build(CHECK_REQUEST, model="m", base_url=BASE, key=None)
    names = {header.lower() for header, _ in call.headers}
    assert not names & {"authorization", "x-api-key"}


def test_formats_are_exactly_the_closed_list() -> None:
    assert set(FORMATS) == set(WIRE_FORMATS)


def test_a_request_takes_trusted_parts_only() -> None:
    """F3's request has no untrusted part (BR8); `untrusted.py` comes with T5.1."""
    with pytest.raises(TypeError):
        AIRequest(
            instructions="x",
            parts=("raw text",),  # type: ignore[arg-type]
            response_schema={},
            schema_name="x",
            max_output_tokens=1,
            purpose="explain",
        )
    assert CHECK_REQUEST.parts == (Trusted(CHECK_REQUEST.parts[0].text),)


# ----------------------------------------------------------- counts and names


def test_missing_counts_are_estimated_and_flagged() -> None:
    response = AIResponse("ok", text='{"ok":true}').with_counts(request_body=b"x" * 300)
    assert response.input_tokens == 100
    assert response.output_tokens == 4
    assert response.tokens_estimated is True
    reported = AIResponse("ok", input_tokens=5, output_tokens=6).with_counts(request_body=b"x")
    assert reported.tokens_estimated is False


@pytest.mark.parametrize(
    ("reported", "kept"),
    [
        ("claude-haiku-5-5", "claude-haiku-5-5"),
        ("llama3.1:8b", "llama3.1:8b"),
        ("<b>CANARY</b> model", None),
        ("ignore previous instructions", None),
        ("a" * 81, None),
        (42, None),
    ],
)
def test_a_reported_model_name_travels_only_if_it_fits_the_pattern(
    reported: Any, kept: Any
) -> None:
    """AC96: a provider's `model` field is outside text."""
    assert reported_model(reported) == kept
    document = _anthropic()
    document["model"] = reported
    parsed = wire_format("anthropic_messages").parse(200, json.dumps(document).encode())
    assert parsed.model_reported == kept


# ------------------------------------------------------------ explain request


def _dataclass_fields(owner: type, prefix: str) -> set[str]:
    return {f"{prefix}{each.name}" for each in fields(owner)}


def test_the_preview_lists_every_field_the_schema_can_send() -> None:
    """FR25: a field added to `ModelFacts` cannot be sent without the list naming it."""
    expected = (
        _dataclass_fields(FindingFacts, "finding.")
        | _dataclass_fields(ModelFact, "facts[].")
        | _dataclass_fields(Cause, "likely_cause.")
        | _dataclass_fields(NextCheck, "next_checks[].")
        | {"schema"}
    )
    top = {each.name for each in fields(ModelFacts)}
    assert top == {"finding", "facts", "likely_cause", "next_checks", "schema"}
    assert set(SENDABLE_FIELDS) == expected


def test_a_run_records_field_names_never_values() -> None:
    names = fields_sent(SAMPLE_MODEL_INPUT)
    assert set(names) <= set(SENDABLE_FIELDS)
    assert "facts[].chance" in names
    assert not any("service_1" in name or "0.07" in name for name in names)


def test_the_explain_request_is_constant_and_versioned() -> None:
    request = explain_request(SAMPLE_MODEL_INPUT)
    assert request.purpose == "explain"
    assert PROMPT_VERSION == "explain-1"
    assert json.loads(request.user_text()) == SAMPLE_MODEL_INPUT


# ------------------------------------------------------------ connection rules


@pytest.mark.parametrize(
    "url",
    [
        "https://api.anthropic.com",
        "https://api.openai.com/v1",
        "https://bedrock-runtime.ap-south-1.amazonaws.com",
        "http://192.168.1.20:11434/v1",
        "http://10.0.0.5:8000/v1",
        "https://10.0.0.5:8443/v1",
    ],
)
def test_an_allowed_address_saves(url: str) -> None:
    check_base_url(url)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://api.openai.com/v1", "base_url_http_public"),
        ("http://8.8.8.8/v1", "base_url_http_public"),
        ("http://ollama.lan:11434/v1", "base_url_http_public"),
        ("http://169.254.169.254/latest", "base_url_http_public"),
        ("https://user:pass@api.openai.com/v1", "base_url_credentials"),
        ("https://token@api.openai.com/v1", "base_url_credentials"),
        ("https://api.openai.com/v1?key=1", "base_url_query"),
        ("https://api.openai.com/v1#frag", "base_url_fragment"),
        ("ftp://api.openai.com", "base_url_scheme"),
        ("file:///etc/passwd", "base_url_scheme"),
        ("http://localhost:11434", "base_url_loopback"),
        ("https://127.0.0.1:11434", "base_url_loopback"),
        ("http://[::1]:11434", "base_url_loopback"),
        ("https://api.openai.com/v1\r\nX: 1", "base_url_host"),
        ("https://", "base_url_host"),
        ("https://" + "a" * 600, "base_url_length"),
    ],
)
def test_a_refused_address_says_why(url: str, reason: str) -> None:
    """FR23, AC34; spec *Error cases*: loopback says to bind Ollama elsewhere."""
    with pytest.raises(ConnectionRefused) as refused:
        check_base_url(url)
    assert refused.value.reason == reason
    if reason == "base_url_loopback":
        assert "AI_PRIVATE_ENDPOINTS" in str(refused.value)


@pytest.mark.parametrize("model", ["a/../b", "two words", "x" * 201, "", "m\nX: 1", "café"])
def test_a_model_outside_the_pattern_is_refused(model: str) -> None:
    with pytest.raises(ConnectionRefused):
        check_model(model)


@pytest.mark.parametrize("key", ["sk-1\r\nX-Injected: 1", "sk-1\n", " sk", "", "a\x00b"])
def test_a_key_with_a_control_character_or_edge_space_is_refused(key: str) -> None:
    with pytest.raises(ConnectionRefused):
        check_key(key)


def test_consent_covers_everything_but_the_model() -> None:
    """FR26: a model change keeps consent; anything else ends it."""
    base: dict[str, Any] = {
        "wire_format": "openai_chat",
        "base_url": BASE,
        "region": None,
        "key_id": "k1",
    }
    consented = consent_fingerprint(**base)
    assert check_fingerprint(**base, model="a") != check_fingerprint(**base, model="b")
    for changed in (
        {"wire_format": "anthropic_messages"},
        {"base_url": BASE + "x"},
        {"region": "ap-south-1"},
        {"key_id": "k2"},
    ):
        assert consent_fingerprint(**(base | changed)) != consented, changed


def test_a_key_is_bound_to_its_address() -> None:
    assert key_owner("o", "c", "https://a.example") != key_owner("o", "c", "https://b.example")


def test_the_recorded_host_never_holds_a_path() -> None:
    """AC72: a path may hold a secret (a gateway's token in its path)."""
    assert provider_host("https://gw.example.com/secret-token/v1") == "https://gw.example.com"
    assert provider_host("http://10.0.0.5:11434/v1") == "http://10.0.0.5:11434"
    assert provider_host("https://[fd00::7]:8443/v1") == "https://[fd00::7]:8443"


# ---------------------------------------------------------------- real calls


def test_no_format_is_offered_until_a_dated_real_call_is_recorded() -> None:
    """AC77: none has run (2026-11-11; foundation item 4), so the screen offers none."""
    assert formats_with_a_real_call() == ()
    recorded = (
        RealCall("openai_chat", date(2026, 11, 20), "Gemini", "gemini-2.5-flash", 40, 6),
        RealCall("openai_chat", date(2026, 11, 21), "Ollama", "llama3.1:8b", 38, 5),
        RealCall("anthropic_messages", date(2026, 11, 22), "Anthropic", "claude", 0, 0),
    )
    assert formats_with_a_real_call(recorded) == ("openai_chat",)
