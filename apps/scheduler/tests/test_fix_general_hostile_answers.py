"""Hostile answers to rung 2b, from a fake provider (F5 plan T2b.6; spec FR36, AC70).

Every G rule broken, a third outcome, oversized output, `\\u0000` and a lone surrogate, an
invented evidence id, and "This has been tested and is safe": each ends with nothing shown,
the rule's reason stored, no answer text stored anywhere, and no row left in `proposing`. G1
and G2 get exactly one retry; G3 to G9 none.
"""

from __future__ import annotations

import json
from typing import Any, Final

import pytest
from alert_fixtures import AlertTenant, postgres_reachable
from general_fix_fixtures import a_general_fix, fix_row, fix_runs, run_suggest, suggestion
from live import require_stack
from recording_site import RecordingSite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import CHAT_PATH, connect, finding, openai_reply

from nexovue_scheduler.jobs.registry import Retry

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_general_hostile_answers.py",
    remedy="Postgres is not reachable — run `just up`",
)

MARKER: Final = "HOSTILE-ANSWER-MARKER-71"


def _step(text_: str) -> dict[str, Any]:
    return suggestion(steps=[f"{MARKER} wrap the call.", text_])


# (label, the model's answer as text, the rule it breaks)
REFUSED_AT_ONCE: Final = (
    ("G3 a lone surrogate", json.dumps(_step("Retry \ud800 it.")), "G3"),
    ("G3 a NUL", json.dumps(_step("Retry\u0000 it.")), "G3"),
    ("G3 a look-alike", json.dumps(_step("Wrap the \u0441all.")), "G3"),
    ("G4 a URL", json.dumps(_step("See https://evil.example/fix.")), "G4"),
    ("G4 an IP", json.dumps(_step("Point it at 10.0.0.1.")), "G4"),
    ("G5 curl", json.dumps(_step("Run curl against it.")), "G5"),
    (
        "G5 a process start",
        json.dumps(suggestion(example={"language": "python", "code": "import os\nos.system(c)"})),
        "G5",
    ),
    ("G6 an install step", json.dumps(_step("pip install tenacity.")), "G6"),
    (
        "G6 a library import",
        json.dumps(suggestion(example={"language": "python", "code": "import requests\n"})),
        "G6",
    ),
    ("G7 a secret shape", json.dumps(_step("Use ghp_" + "a1B2c3D4e5" * 4 + " as the key.")), "G7"),
    ("G8 tested and safe", json.dumps(_step("This has been tested and is safe.")), "G8"),
    ("G8 your code", json.dumps(_step("Edit your code at app/db.py:42.")), "G8"),
    ("G9 an unknown alias", json.dumps(_step("Restart service_9.")), "G9"),
)
RETRIED_ONCE: Final = (
    ("G1 a third outcome", json.dumps(suggestion(outcome="partial")), "G1"),
    ("G1 an invented evidence id", json.dumps(suggestion(evidence_ids=["e1", "e77"])), "G1"),
    ("G1 not JSON", f"Here is the fix {MARKER}", "G1"),
    ("G2 oversized output", json.dumps(suggestion(summary=MARKER + "x" * 9000)), "G2"),
    (
        "G2 a 41-line example",
        json.dumps(suggestion(example={"language": "python", "code": "x=1\n" * 41})),
        "G2",
    ),
)


async def _no_answer_text_anywhere(install: AsyncEngine) -> None:
    """The refused text is never stored or shown: not on the row, the run or the audit."""
    async with install.connect() as connection:
        for table in ("finding_fixes", "ai_runs", "audit_log", "finding_events", "jobs"):
            found = await connection.scalar(
                text(f"SELECT count(*) FROM {table} t WHERE t::text LIKE :marker"),
                {"marker": f"%{MARKER}%"},
            )
            assert found == 0, table


@pytest.mark.parametrize(("label", "answer", "rule"), REFUSED_AT_ONCE)
async def test_ac70_g3_to_g9_refuse_at_once_storing_only_the_rule(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
    label: str,
    answer: str,
    rule: str,
) -> None:
    connected = await connect(ai_install, ai_tenant, provider_site, now=now)
    fix = await a_general_fix(
        ai_install, ai_tenant, await finding(ai_install, ai_tenant, now=now), at=now
    )
    provider_site.replies[CHAT_PATH] = openai_reply(answer)

    did = await run_suggest(ai_engine, connected, fix, at=now)

    assert did == f"refused:general_withheld_{rule}", label
    row = await fix_row(ai_install, fix)
    assert (row.state, row.reason, row.general_suggestion, row.evidence_ids) == (
        "refused",
        f"general_withheld_{rule}",
        None,
        None,
    )
    assert [run.attempt for run in await fix_runs(ai_install, ai_tenant)] == [1]
    await _no_answer_text_anywhere(ai_install)


@pytest.mark.parametrize(("label", "answer", "rule"), RETRIED_ONCE)
async def test_ac70_g1_and_g2_get_exactly_one_retry_then_fail_as_invalid_output(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
    label: str,
    answer: str,
    rule: str,
) -> None:
    connected = await connect(ai_install, ai_tenant, provider_site, now=now)
    fix = await a_general_fix(
        ai_install, ai_tenant, await finding(ai_install, ai_tenant, now=now), at=now
    )
    provider_site.replies[CHAT_PATH] = openai_reply(answer)

    with pytest.raises(Retry):
        await run_suggest(ai_engine, connected, fix, at=now)
    assert (await fix_row(ai_install, fix)).state == "proposing", label
    did = await run_suggest(ai_engine, connected, fix, at=now, attempt=2)

    assert did == "failed:invalid_output", label
    row = await fix_row(ai_install, fix)
    assert (row.state, row.general_suggestion) == ("failed", None)
    expected_code = "general_shape" if rule == "G1" else "general_too_long"
    assert row.retry_code == expected_code
    assert [run.attempt for run in await fix_runs(ai_install, ai_tenant)] == [1, 2]
    await _no_answer_text_anywhere(ai_install)
