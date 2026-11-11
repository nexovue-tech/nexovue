"""F4 plan T2.5, AC35: chat's rows in the expiry sweep, against real Postgres.

- a question older than its project's derived retention goes, even in a conversation kept
  active since; the conversation goes with its last message;
- a question in flight, or still holding tokens, stays whatever its age;
- a deleted project's conversations go at the next sweep, once nothing in them is held;
- one table that fails is logged, and the tables after it are still cleared.

Each test makes its own organization (`assistant_rows.made`); the sweep is the real one.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from assistant_rows import (
    Asked,
    ask,
    ask_again,
    install,
    made,
    message,
    reachable,
    run,
)
from live import require_stack
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from nexovue_scheduler.jobs import expiry

require_stack(
    reachable=reachable(),
    suite="chat's expiry suite",
    remedy="start the dev stack so Postgres answers (`just dev`)",
)

__all__ = ["install", "made"]


async def _derived_days(install: AsyncEngine, asked: Asked) -> int:
    row = await run(
        install,
        "SELECT derived_retention_days FROM project_retention WHERE project_id = :p",
        p=asked.project_id,
    )
    return int(row.derived_retention_days)


async def _conversation_of(install: AsyncEngine, asked: Asked) -> str:
    return str((await message(install, asked.message_id)).conversation_id)


async def _exists(install: AsyncEngine, table: str, row_id: str) -> bool:
    return (await run(install, f"SELECT 1 FROM {table} WHERE id = :id", id=row_id)) is not None


async def test_a_question_past_derived_retention_goes_and_its_conversation_with_the_last(
    install: AsyncEngine, made: list[str]
) -> None:
    first = await ask(install, made, state="answered")
    days = await _derived_days(install, first)
    conversation = await _conversation_of(install, first)
    old = await ask_again(
        install,
        first,
        state="answered",
        conversation_id=conversation,
        created_ago=timedelta(days=days + 1),
    )
    # The conversation stayed active: its newest question is today's.

    await expiry.sweep(install)

    assert not await _exists(install, "assistant_messages", old.message_id)
    assert await _exists(install, "assistant_messages", first.message_id)
    assert await _exists(install, "assistant_conversations", conversation)

    await run(
        install,
        "UPDATE assistant_messages SET created_at = now() - make_interval(days => :d) "
        "WHERE id = :id",
        d=days + 1,
        id=first.message_id,
    )
    await expiry.sweep(install)

    assert not await _exists(install, "assistant_messages", first.message_id)
    assert not await _exists(install, "assistant_conversations", conversation)


@pytest.mark.parametrize(("state", "reserved"), [("running", 0), ("unavailable", 60_000)])
async def test_a_question_in_flight_or_holding_tokens_stays_whatever_its_age(
    install: AsyncEngine, made: list[str], state: str, reserved: int
) -> None:
    first = await ask(install, made, state=state, reserved=reserved)
    days = await _derived_days(install, first)
    await run(
        install,
        "UPDATE assistant_messages SET created_at = now() - make_interval(days => :d) "
        "WHERE id = :id",
        d=days + 1,
        id=first.message_id,
    )

    await expiry.sweep(install)

    assert await _exists(install, "assistant_messages", first.message_id)


async def test_a_deleted_project_s_conversations_go_once_nothing_in_them_is_held(
    install: AsyncEngine, made: list[str]
) -> None:
    settled = await ask(install, made, state="answered")
    held = await ask_again(install, settled, state="running")
    await run(install, "UPDATE projects SET deleted_at = now() WHERE id = :p", p=settled.project_id)

    await expiry.sweep(install)

    assert not await _exists(install, "assistant_messages", settled.message_id)
    assert await _exists(install, "assistant_messages", held.message_id)

    await run(
        install,
        "UPDATE assistant_messages SET state = 'unavailable', ticket_hash = NULL, "
        "sealed_ticket = NULL WHERE id = :id",
        id=held.message_id,
    )
    await expiry.sweep(install)

    assert not await _exists(install, "assistant_messages", held.message_id)


async def test_one_failing_table_never_stops_the_tables_after_it(
    install: AsyncEngine, made: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await ask(install, made, state="answered")
    await run(
        install,
        "UPDATE assistant_messages SET created_at = now() - make_interval(days => :d) "
        "WHERE id = :id",
        d=await _derived_days(install, first) + 1,
        id=first.message_id,
    )
    broken = ("assistant_no_such_table", "id", "true")
    monkeypatch.setattr(expiry, "_EXPIRED", (broken, *expiry._EXPIRED))

    with capture_logs() as logs:
        removed = await expiry.sweep(install)

    assert not await _exists(install, "assistant_messages", first.message_id)
    assert "jobs" in removed
    failed = [
        line for line in logs if line["event"] == "an expiry table failed; the rest still run"
    ]
    assert [line["table"] for line in failed] == ["assistant_no_such_table"]
