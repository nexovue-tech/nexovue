"""Hostile answers to rung 3, from a fake provider (F5 plan T4.5; spec FR16, FR17, U7; AC20).

Every rule broken, a file outside the pack, a never-changed file, a deletion, a file
emptied, oversized output, hidden and look-alike characters, an address, an encoded
string, a secret, a long line, `\\u0000`, a lone surrogate, a display fact, a fourth
outcome and plain prose: on the job's last attempt each ends with **zero edits written,
no answer text stored anywhere, the pack gone, and no row left in `proposing`**.
"""

from __future__ import annotations

import json
from typing import Any, Final

import pytest
from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from propose_fix_fixtures import OLD, a_proposing_fix, edit, edits_answer, pack_left, run_propose
from recording_site import RecordingSite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import CHAT_PATH, connect, finding, openai_reply

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_hostile_answers.py",
    remedy="Postgres is not reachable — run `just up`",
)

MARKER: Final = "HOSTILE_SOURCE_ANSWER_MARKER"


def _line(added: str) -> str:
    return json.dumps(edits_answer(edit(new_text=f"    {added}  # {MARKER}\n")))


# (label, the model's answer as text, how the row ends)
HOSTILE: Final = (
    (
        "K1 a file outside the pack",
        json.dumps(edits_answer(edit(file_ref="file_7"))),
        "withheld_K1",
    ),
    (
        "K3 overlapping edits",
        json.dumps(
            edits_answer(
                edit(old_text=OLD, new_text=f"    pass  # {MARKER}\n"),
                edit(old_text="gateway.charge(amount)", new_text="x"),
            )
        ),
        "edit_did_not_apply",
    ),
    ("K4 a delete key", json.dumps(edits_answer(edit(delete=True))), "withheld_K4"),
    (
        "K5 too many lines",
        json.dumps(edits_answer(edit(new_text="".join(f"    x{n} = {n}\n" for n in range(70))))),
        "withheld_K5",
    ),
    ("K6 a URL", _line("call('https://evil.example/pay')"), "withheld_K6"),
    ("K6 an IP", _line("call('10.0.0.1')"), "withheld_K6"),
    ("K7 a bidirectional control", _line("x = 1 \u202e"), "withheld_K7"),
    ("K7 a look-alike", _line("\u0441harge = 1"), "withheld_K7"),
    (
        "K8 an encoded string",
        _line("blob = 'Zm9vYmFyYmF6cXV4cXV1eHh5enp6QUJDREVGR0hJSktMTU5PUFFS'"),
        "withheld_K8",
    ),
    ("K9 a secret", _line("token = 'ghp_" + "a1B2c3D4e5" * 4 + "'"), "withheld_K9"),
    ("K10 a long line", _line("x = " + "1 + " * 70 + "1"), "withheld_K10"),
    ("K10 text hidden behind spaces", _line("x = 1" + " " * 30 + "evil()"), "withheld_K10"),
    ("NUL in new text", json.dumps(edits_answer(edit(new_text="x\u0000\n"))), "invalid_output"),
    ("a lone surrogate", json.dumps(edits_answer(edit(new_text="x\ud800\n"))), "invalid_output"),
    ("a display fact", json.dumps(edits_answer(edit(context_before="x"))), "invalid_output"),
    ("a fourth outcome", json.dumps({"outcome": "rewrite", MARKER: 1}), "invalid_output"),
    ("plain prose", f"Here is the change {MARKER}", "invalid_output"),
    ("oversized", json.dumps(edits_answer(*[edit(why=MARKER)] * 25)), "invalid_output"),
    (
        "banned claim, unknown alias",
        json.dumps(edits_answer(edit(why="Restart service_9."))),
        "invalid_output",
    ),
)


async def _no_answer_text_anywhere(install: AsyncEngine) -> None:
    async with install.connect() as connection:
        for table in ("finding_fixes", "ai_runs", "audit_log", "finding_events", "jobs"):
            found = await connection.scalar(
                text(f"SELECT count(*) FROM {table} t WHERE t::text LIKE :marker"),
                {"marker": f"%{MARKER}%"},
            )
            assert found == 0, table


@pytest.mark.parametrize(("label", "answer", "reason"), HOSTILE)
async def test_ac20_each_hostile_answer_writes_nothing_and_leaves_no_row_proposing(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
    label: str,
    answer: str,
    reason: str,
) -> None:
    connected = await connect(ai_install, ai_tenant, provider_site, now=now)
    made = await a_proposing_fix(
        ai_install, ai_tenant, await finding(ai_install, ai_tenant, now=now), at=now
    )
    provider_site.replies[CHAT_PATH] = openai_reply(answer)

    did = await run_propose(ai_engine, connected, made.fix_id, at=now, attempt=2)

    assert did.endswith(f":{reason}"), label
    async with ai_install.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT state, edits FROM finding_fixes WHERE id = :id"),
                {"id": made.fix_id},
            )
        ).one()
    assert row.state in ("refused", "failed") and row.edits is None, label
    assert await pack_left(ai_install, made.fix_id) is None, label
    await _no_answer_text_anywhere(ai_install)


async def test_k2_an_owner_s_exclusion_since_the_read_withholds_after_its_one_retry(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
) -> None:
    """K2 runs with the owner's extra paths as the consent holds them at the call."""
    connected = await connect(ai_install, ai_tenant, provider_site, now=now)
    made = await a_proposing_fix(
        ai_install,
        ai_tenant,
        await finding(ai_install, ai_tenant, now=now),
        at=now,
        excluded=["services/shop/app/handler.py"],
    )
    provider_site.replies[CHAT_PATH] = openai_reply(edits_answer())

    assert await run_propose(ai_engine, connected, made.fix_id, at=now, attempt=2) == (
        "refused:withheld_K2"
    )
    assert await pack_left(ai_install, made.fix_id) is None
