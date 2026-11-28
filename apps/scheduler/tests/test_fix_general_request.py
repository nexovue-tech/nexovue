"""Rung 2b's request carries F3's facts and the language, and nothing else (F5 plan T2b.4;
spec FR34, AC68).

The fake provider records every byte. Telemetry text an attacker controls is planted in the
finding's real names and evidence, where the gather keeps it; none of it may reach the
provider, which sees F3's aliases instead. The request holds one trusted part, never an
untrusted one. No other address is called: rung 2b reads no repository and calls no GitHub.
"""

from __future__ import annotations

import json
from datetime import datetime

from alert_fixtures import AlertTenant, postgres_reachable
from general_fix_fixtures import MARKERS, MODEL_INPUT, a_general_fix, run_suggest, suggestion
from live import require_stack
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.fixes.general_prompt import INSTRUCTIONS, RETRY_SENTENCES
from nexovue_platform.fixes.records import RETRY_SHAPE
from recording_site import RecordingSite
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import CHAT_PATH, MODEL, connect, finding, openai_reply

from nexovue_scheduler.insight.suggest_general_fix import general_request

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_general_request.py",
    remedy="Postgres is not reachable — run `just up`",
)


async def test_ac68_the_body_is_the_instructions_the_facts_and_the_language_alone(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected = await connect(ai_install, ai_tenant, provider_site, now=now)
    fix = await a_general_fix(
        ai_install, ai_tenant, await finding(ai_install, ai_tenant, now=now), at=now, language="go"
    )
    provider_site.replies[CHAT_PATH] = openai_reply(
        suggestion(example={"language": "go", "code": "package main\n"})
    )

    await run_suggest(ai_engine, connected, fix, at=now)

    [sent] = provider_site.requests
    expected = wire_format("openai_chat").build(
        general_request(MODEL_INPUT, "go", None), model=MODEL, base_url=connected.base_url, key=None
    )
    assert sent.path == CHAT_PATH and sent.body == expected.body
    body = json.loads(sent.body)
    texts = [message["content"] for message in body["messages"]]
    assert texts[0] == INSTRUCTIONS
    assert json.loads(texts[1]) == {"model_input": MODEL_INPUT, "language": "go"}
    assert len(texts) == 2


async def test_ac68_no_planted_telemetry_text_reaches_the_provider(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected = await connect(ai_install, ai_tenant, provider_site, now=now)
    fix = await a_general_fix(
        ai_install, ai_tenant, await finding(ai_install, ai_tenant, now=now), at=now
    )
    provider_site.replies[CHAT_PATH] = openai_reply(suggestion())

    await run_suggest(ai_engine, connected, fix, at=now)

    [sent] = provider_site.requests
    for marker in MARKERS:
        assert marker.encode() not in sent.body, marker
    assert b"service_1" in sent.body and b"error_1" in sent.body


def test_the_request_holds_one_trusted_part_and_a_retry_says_only_its_fixed_sentence() -> None:
    first = general_request(MODEL_INPUT, "python", None)
    retry = general_request(MODEL_INPUT, "python", RETRY_SHAPE)

    assert first.trusted_only and retry.trusted_only and len(first.parts) == 1
    assert first.purpose == "fix"
    assert json.loads(retry.user_text()) == {
        "model_input": MODEL_INPUT,
        "language": "python",
        "retry": RETRY_SENTENCES[RETRY_SHAPE],
    }
