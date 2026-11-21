"""F4 plan T2.9: chat's share of the month, against real Postgres, as the AI account.

- AC25: questions reserving in parallel at a nearly full month never pass chat's ceiling;
  the only way past it is the one overshoot F3's BR13 allows (a call spending more than its
  question still held), which the test reports, and after which the next question is refused.
- AC57: below 60,000 tokens left under 80% a question is refused while a critical
  explanation still reserves; at a 100 million limit and 79.9% used, a question reserves.

Each test makes its own organization and deletes it after (`assistant_rows.made`).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from assistant_rows import (
    Asked,
    as_insight,
    ask,
    ask_again,
    install,
    made,
    member,
    reachable,
    run,
)
from live import require_stack
from nexovue_platform.ai.limits import ceiling, chat_ceiling, month_of
from nexovue_platform.assistant_records import QUESTION_RESERVATION_TOKENS
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.insight.assistant_store import PostgresQuestionStore
from nexovue_scheduler.insight.store import reserve_month

require_stack(
    reachable=reachable(),
    suite="chat's budget suite",
    remedy="start the dev stack so Postgres answers (`just dev`)",
)

__all__ = ["as_insight", "install", "made"]

RESERVE = QUESTION_RESERVATION_TOKENS


async def _usage(install: AsyncEngine, org_id: str) -> tuple[int, int]:
    row = await run(
        install,
        "SELECT used_tokens, reserved_tokens FROM ai_usage_monthly "
        "WHERE org_id = :o AND month = date_trunc('month', now())::date",
        o=org_id,
    )
    return (int(row.used_tokens), int(row.reserved_tokens))


async def _used(install: AsyncEngine, org_id: str, tokens: int) -> None:
    await run(
        install,
        "INSERT INTO ai_usage_monthly (org_id, month, used_tokens) "
        "VALUES (:o, date_trunc('month', now())::date, :t) "
        "ON CONFLICT (org_id, month) DO UPDATE SET used_tokens = :t",
        o=org_id,
        t=tokens,
    )


async def _people_asking(install: AsyncEngine, first: Asked, count: int) -> list[Asked]:
    """`count` questions in flight at once, one per person (one in flight each, FR4)."""
    asked = [first]
    for _ in range(count - 1):
        asked.append(await ask_again(install, first, user_id=await member(install, first)))
    return asked


async def test_parallel_questions_never_pass_chat_s_ceiling(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str]
) -> None:
    limit = 1_000_000
    first = await ask(install, made, limit=limit)
    # Room for exactly three reservations under 80%, and a little over.
    already = chat_ceiling(limit) - 3 * RESERVE - 10_000
    await _used(install, first.org_id, already)
    asked = await _people_asking(install, first, 8)
    store = PostgresQuestionStore(as_insight)
    questions = [await store.start(org_id=one.org_id, message_id=one.message_id) for one in asked]
    assert all(question is not None for question in questions)
    now = datetime.now(UTC)

    taken = await asyncio.gather(
        *(
            store.reserve(question, tokens=RESERVE, limit=limit, now=now)
            for question in questions
            if question is not None
        )
    )

    assert sum(taken) == 3
    used, reserved = await _usage(install, first.org_id)
    assert (used, reserved) == (already, 3 * RESERVE)
    assert used + reserved <= chat_ceiling(limit)

    # BR13's one overshoot: each running question's call spends more than it held. Usage may
    # pass the ceiling by that much, and the next question is then refused.
    overspend = RESERVE + 15_000
    holders = [question for question, ok in zip(questions, taken, strict=True) if ok]
    for question in holders:
        assert question is not None
        await store.record_spend(question, tokens=overspend, estimated=False)
    used, reserved = await _usage(install, first.org_id)
    overshoot = used + reserved - chat_ceiling(limit)
    print(f"AC25 overshoot past the ceiling (BR13): {overshoot} tokens")  # noqa: T201
    # Each call's excess over its hold, less the 10,000 the month still had free.
    assert (reserved, overshoot) == (0, 3 * (overspend - RESERVE) - 10_000)
    refused = [question for question, ok in zip(questions, taken, strict=True) if not ok]
    assert refused[0] is not None
    assert not await store.reserve(refused[0], tokens=RESERVE, limit=limit, now=now)


async def test_a_question_is_refused_below_its_reservation_and_a_critical_run_still_reserves(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str]
) -> None:
    limit = 1_000_000
    asked = await ask(install, made, limit=limit)
    # 50,000 left below 80%: less than one question's reservation.
    await _used(install, asked.org_id, chat_ceiling(limit) - 50_000)
    store = PostgresQuestionStore(as_insight)
    question = await store.start(org_id=asked.org_id, message_id=asked.message_id)
    assert question is not None
    now = datetime.now(UTC)

    assert not await store.reserve(question, tokens=RESERVE, limit=limit, now=now)

    async with as_insight.begin() as connection:
        critical = await reserve_month(
            connection,
            org_id=asked.org_id,
            month=month_of(now),
            reserve=RESERVE,
            ceiling=ceiling(limit, "critical"),
        )
    assert critical
    used, reserved = await _usage(install, asked.org_id)
    assert (used, reserved) == (chat_ceiling(limit) - 50_000, RESERVE)


async def test_at_a_100_million_limit_and_79_9_percent_used_a_question_reserves(
    install: AsyncEngine, as_insight: AsyncEngine, made: list[str]
) -> None:
    limit = 100_000_000
    asked = await ask(install, made, limit=limit)
    await _used(install, asked.org_id, 79_900_000)
    store = PostgresQuestionStore(as_insight)
    question = await store.start(org_id=asked.org_id, message_id=asked.message_id)
    assert question is not None

    assert await store.reserve(question, tokens=RESERVE, limit=limit, now=datetime.now(UTC))
    assert await _usage(install, asked.org_id) == (79_900_000, RESERVE)
