"""Rung 3's request: what the provider is sent, byte for byte (F5 plan T4.3; spec FR15a, U2;
AC42, AC65).

The fake provider records every byte. Trusted: the instructions, F3's facts, the frames as
numbers, each file's number, extension and lines. Untrusted: each file's text and the
person's hint, each JSON-encoded inside a fence drawn fresh for the call, with hidden
characters stripped. **Never:** a file path, the real names F3 keeps apart, a stack's words,
or GitHub's. The fence helps a little and is not counted as the defence.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from alert_fixtures import AlertTenant, postgres_reachable
from general_fix_fixtures import MARKERS, MODEL_INPUT
from live import require_stack
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.fixes.source_prompt import (
    LAST_ROUND_INSTRUCTIONS,
    NEEDS_FILES_INSTRUCTIONS,
    SentFile,
)
from propose_fix_fixtures import (
    FOLDER,
    HANDLER,
    HINT,
    PLANTED,
    a_proposing_fix,
    edits_answer,
    run_propose,
)
from recording_site import RecordingSite
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import CHAT_PATH, MODEL, connect, finding, openai_reply

from nexovue_scheduler.insight.propose_fix import source_request

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_model_request.py",
    remedy="Postgres is not reachable — run `just up`",
)

_FENCE = re.compile(r'<(untrusted-[0-9a-f]{24}) label="([a-z0-9 ]+)">\n.*?\n(".*?")\n</\1>', re.S)


async def _sent(
    install: AsyncEngine,
    engine: AsyncEngine,
    site: RecordingSite,
    tenant: AlertTenant,
    at: datetime,
) -> list[str]:
    connected = await connect(install, tenant, site, now=at)
    made = await a_proposing_fix(install, tenant, await finding(install, tenant, now=at), at=at)
    site.replies[CHAT_PATH] = openai_reply(edits_answer())
    await run_propose(engine, connected, made.fix_id, at=at)
    [sent] = site.requests
    return [message["content"] for message in json.loads(sent.body)["messages"]]


async def test_ac42_files_and_the_hint_go_fenced_and_nothing_names_a_path(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    instructions, user = await _sent(ai_install, ai_engine, provider_site, ai_tenant, now)

    trusted = json.loads(user.split("\n\n", 1)[0])
    assert trusted["model_input"] == MODEL_INPUT
    assert trusted["frames"] == [{"file": "file_1", "line": 9}, {"file": "file_2", "line": 2}]
    assert trusted["files"][0] == {"file": "file_1", "extension": ".py", "lines": [1, 10]}
    fences = _FENCE.findall(user)
    assert [(label) for _, label, _ in fences] == ["file 1", "file 2", "person hint"]
    assert len({tag for tag, _, _ in fences}) == 3
    assert json.loads(fences[0][2]) == HANDLER
    assert json.loads(fences[2][2]) == HINT
    # The planted line and the hint appear only inside their fences.
    outside = _FENCE.sub("", user)
    assert PLANTED not in outside and "PERSON_HINT_MARKER" not in outside
    assert FOLDER not in user and FOLDER not in instructions
    assert NEEDS_FILES_INSTRUCTIONS in instructions


async def test_ac65_no_real_name_or_stack_text_reaches_the_provider(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    instructions, user = await _sent(ai_install, ai_engine, provider_site, ai_tenant, now)

    for marker in MARKERS:
        assert marker not in user and marker not in instructions, marker
    assert "service_1" in user


def test_u2_hidden_characters_are_stripped_and_each_call_draws_new_fences() -> None:
    sent = [SentFile("file_1", "x = 1\u202e\u200b # \u0000end\n", ".py", 1, 1)]

    first = source_request(
        model_input=None, sent=sent, frames=[], hint=None, round_number=1, retry_code=None
    )
    second = source_request(
        model_input=None, sent=sent, frames=[], hint=None, round_number=3, retry_code=None
    )

    assert not first.trusted_only
    body = first.user_text()
    assert "\\u202e" not in body and "\\u200b" not in body and "\\u0000" not in body
    assert "x = 1 # end" in body
    assert _FENCE.findall(body)[0][0] != _FENCE.findall(second.user_text())[0][0]
    assert LAST_ROUND_INSTRUCTIONS in second.instructions
    assert "needs_files" not in json.dumps(second.response_schema)
    built = wire_format("openai_chat").build(
        first, model=MODEL, base_url="https://provider.example/v1", key=None
    )
    assert b"file_1" in built.body
