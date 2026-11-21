"""F4 plan T2.0a, the spike, and AC67: chat's worker statements, run for real as the AI
account (`nexovue_insight`), under migration 0073's row-level security.

Held here:

- the loop's real store (`PostgresQuestionStore`) runs end to end as the account: take a
  question, recheck, reserve, record a call, a step, a read and a count, spend, finish;
- a finished question and its steps are then hidden from the account, and it cannot read a
  ticket's hash or an answer at all;
- the account's own direct final write is refused by the policy, so only
  `assistant_finish_message` ends a question, and a second finish rewrites nothing;
- the sweep, as the account, ends a question past its clock `unavailable` and charges what
  it held, flagged estimated.

The non-superuser owner arm runs this same module against a database a non-superuser owns
(`NEXOVUE_POSTGRES_DSN` pointed at it); status.md records both runs.

**Tests own their rows:** each makes its own organization and deletes it after, so the
stack's own sweeps never meet a row they could settle first: every question here has a
clock far in the future unless the test is about the clock.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from assistant_rows import (
    LIMIT,
    RESERVE,
    as_insight,
    ask,
    install,
    made,
    message,
    reachable,
    run,
)
from live import require_stack
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.insight import assistant_sweep
from nexovue_scheduler.insight.assistant_store import PostgresQuestionStore, StepEnd

require_stack(
    reachable=reachable(),
    suite="chat's AI account suite",
    remedy="start the dev stack so Postgres answers (`just dev`)",
)

__all__ = ["as_insight", "install", "made"]


async def test_the_worker_s_real_statements_run_as_the_ai_account(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str]
) -> None:
    asked = await ask(install, made)
    store = PostgresQuestionStore(as_insight)
    now = datetime.now(UTC)

    question = await store.start(org_id=asked.org_id, message_id=asked.message_id)
    assert question is not None and question.question == "What is failing?"
    assert await store.start(org_id=asked.org_id, message_id=asked.message_id) is None
    assert await store.still_allowed(question) is None
    connection = await store.connection(question)
    assert connection is not None and connection.provider_label == "OpenAI-compatible"
    assert await store.scope(question) is not None
    assert await store.reserve(question, tokens=RESERVE, limit=LIMIT, now=now)
    assert await store.set_phase(question, "reading")
    run_id = await store.start_run(question, connection, prompt_version="chat-1")
    step_id = await store.add_step(
        question,
        position=1,
        tool="list_services",
        arguments={"from": "a"},
        display=None,
        from_time=now - timedelta(hours=1),
        to_time=now,
    )
    assert step_id is not None
    await store.finish_step(question, step_id, StepEnd("read", row_count=2), at=now)
    await store.log_read(
        question, tool="list_services", from_time=None, to_time=None, row_count=2, provider="x"
    )
    await store.finish_run(
        question,
        run_id,
        outcome="done",
        input_tokens=900,
        output_tokens=100,
        estimated=False,
        at=now,
    )
    await store.record_spend(question, tokens=1_000, estimated=False)
    await store.count(question, kind="answered", now=now)
    assert await store.finish(question, state="answered", answer={"sentences": []})

    row = await message(install, asked.message_id)
    assert (row.state, row.reserved_tokens, row.used_tokens) == ("answered", 0, 1_000)
    assert (row.ticket_hash, row.sealed_ticket, row.history) == (None, None, None)
    usage = await run(
        install,
        "SELECT used_tokens, reserved_tokens FROM ai_usage_monthly WHERE org_id = :o",
        o=asked.org_id,
    )
    assert tuple(usage) == (1_000, 0)
    # Finished: the account sees neither the question nor its steps any more.
    assert not await store.is_running(question)
    async with as_insight.connect() as db:
        seen = await db.execute(
            text("SELECT count(*) FROM assistant_steps WHERE org_id = :o"), {"o": asked.org_id}
        )
        assert seen.scalar_one() == 0


async def test_a_direct_final_write_by_the_account_is_refused(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str]
) -> None:
    asked = await ask(install, made, state="running")

    with pytest.raises(DBAPIError, match="row-level security"):
        async with as_insight.begin() as db:
            await db.execute(
                text(
                    "UPDATE assistant_messages SET state = 'answered' "
                    "WHERE org_id = :o AND id = :id"
                ),
                {"o": asked.org_id, "id": asked.message_id},
            )
    assert (await message(install, asked.message_id)).state == "running"


@pytest.mark.parametrize("column", ["ticket_hash", "answer", "wrong_reason"])
async def test_the_account_cannot_read_a_ticket_or_an_answer(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str], column: str
) -> None:
    asked = await ask(install, made)

    with pytest.raises(DBAPIError, match="permission denied"):
        async with as_insight.connect() as db:
            await db.execute(
                text(f"SELECT {column} FROM assistant_messages WHERE org_id = :o"),
                {"o": asked.org_id},
            )


async def test_a_finished_answer_is_never_rewritten(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str]
) -> None:
    asked = await ask(install, made, state="running")
    finish = text(
        "SELECT assistant_finish_message(:id, :state, CAST(:answer AS jsonb), NULL, NULL, NULL, "
        "'return')"
    )

    async with as_insight.begin() as db:
        first = await db.execute(
            finish, {"id": asked.message_id, "state": "answered", "answer": '{"n": 1}'}
        )
        assert first.scalar_one() is True
    async with as_insight.begin() as db:
        again = await db.execute(
            finish, {"id": asked.message_id, "state": "withheld", "answer": '{"n": 2}'}
        )
        assert again.scalar_one() is False

    row = await message(install, asked.message_id)
    assert (row.state, row.answer) == ("answered", {"n": 1})


async def test_the_account_cannot_insert_a_question_or_mint_a_ticket(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str]
) -> None:
    asked = await ask(install, made)

    with pytest.raises(DBAPIError, match="permission denied"):
        async with as_insight.begin() as db:
            await db.execute(
                text(
                    "UPDATE assistant_messages SET ticket_hash = 'mine' "
                    "WHERE org_id = :o AND id = :id"
                ),
                {"o": asked.org_id, "id": asked.message_id},
            )


async def test_the_sweep_settles_a_dead_question_and_charges_what_it_held(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str]
) -> None:
    asked = await ask(
        install, made, state="running", reserved=RESERVE, hard_ends_in=timedelta(minutes=-1)
    )

    await assistant_sweep.sweep(as_insight)

    row = await message(install, asked.message_id)
    assert (row.state, row.failure_reason) == ("unavailable", "died")
    assert (row.reserved_tokens, row.used_tokens, row.tokens_estimated) == (0, RESERVE, True)
    usage = await run(
        install,
        "SELECT used_tokens, reserved_tokens FROM ai_usage_monthly WHERE org_id = :o",
        o=asked.org_id,
    )
    assert tuple(usage) == (RESERVE, 0)
    # A second sweep finds nothing left to move.
    await assistant_sweep.sweep(as_insight)
    assert (await message(install, asked.message_id)).used_tokens == RESERVE


async def test_a_stopped_question_keeps_its_state_while_its_tokens_settle(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str]
) -> None:
    asked = await ask(install, made, state="stopped", reserved=RESERVE)
    store = PostgresQuestionStore(as_insight)
    question = await store.start(org_id=asked.org_id, message_id=asked.message_id)
    assert question is None  # nothing to take, only money to move

    async with as_insight.begin() as db:
        moved = await db.execute(
            text("SELECT assistant_record_spend(:id, 500, false)"), {"id": asked.message_id}
        )
        assert moved is not None
        await db.execute(
            text(
                "SELECT assistant_finish_message(:id, 'answered', NULL, NULL, NULL, NULL, 'return')"
            ),
            {"id": asked.message_id},
        )

    row = await message(install, asked.message_id)
    assert (row.state, row.reserved_tokens, row.used_tokens) == ("stopped", 0, 500)
    assert json.dumps(row.answer) == "null"
