"""The real-call tool, against a fake provider: what it sends and what it records (F3 T4.10;
AC77). No real provider is called here; the dated real call itself needs the founder's
free-tier key (foundation item 4) and has not run.
"""

from __future__ import annotations

import json
from datetime import date

from nexovue_platform.ai.check_prompt import CHECK_REQUEST
from nexovue_platform.ai.contract import AIResponse, HttpCall
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.ai.real_calls import RealCall, formats_with_a_real_call
from real_call import call_once, record_line

from nexovue_scheduler.insight.transport import Answer
from nexovue_scheduler.probes.addresses import PrivateEndpoints

ANSWER = {
    "model": "gemini-2.5-flash",
    "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
    "usage": {"prompt_tokens": 40, "completion_tokens": 6},
}


def test_it_sends_the_constant_test_and_records_a_call_that_reported_its_counts() -> None:
    sent: list[HttpCall] = []

    def provider(
        call: HttpCall, *, timeout_seconds: int, private_endpoints: PrivateEndpoints
    ) -> Answer:
        sent.append(call)
        return Answer(200, json.dumps(ANSWER).encode())

    response = call_once(
        format_name="openai_chat",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash",
        key="k",
        send=provider,
    )

    assert isinstance(response, AIResponse) and response.outcome == "ok"
    expected = wire_format("openai_chat").build(
        CHECK_REQUEST, model="gemini-2.5-flash", base_url="https://x", key=None
    )
    assert sent[0].body == expected.body
    line = record_line(
        format_name="openai_chat",
        provider="Gemini",
        model="gemini-2.5-flash",
        response=response,
        on=date(2026, 11, 20),
    )
    assert line == (
        "RealCall('openai_chat', date(2026, 11, 20), 'Gemini', 'gemini-2.5-flash', 40, 6),"
    )
    assert line is not None
    recorded = eval(line.rstrip(","), {"RealCall": RealCall, "date": date})
    assert formats_with_a_real_call((recorded,)) == ("openai_chat",)


def test_a_call_with_estimated_or_no_counts_earns_no_record() -> None:
    estimated = AIResponse("ok", text="{}").with_counts(request_body=b"x" * 30)
    for response in (estimated, AIResponse("bad_key")):
        assert (
            record_line(
                format_name="openai_chat",
                provider="Gemini",
                model="m",
                response=response,
                on=date(2026, 11, 20),
            )
            is None
        )


def test_an_address_the_rules_refuse_is_never_called() -> None:
    def provider(call: HttpCall, **_: object) -> Answer:
        raise AssertionError("called")

    refused = call_once(
        format_name="openai_chat",
        base_url="http://api.openai.com/v1",
        model="m",
        key=None,
        send=provider,
    )
    assert refused == "base_url_http_public"
