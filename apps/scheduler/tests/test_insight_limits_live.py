"""The month's limit, at the point of spending, against real Postgres as `nexovue_insight`
(F3 plan T5.3; AC53, AC55; spec BR13, FR40).

Runs are wording runs against a fake provider (`wording_fixtures`); no real provider is
called. A run reserves its input estimate plus the most it may write before it calls, so
parallel runs cannot overspend; a run that reports more than it reserved is still counted,
passing the limit by at most that one run, and the next is refused.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from typing import Final

from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from nexovue_platform.ai.contract import HttpCall
from nexovue_platform.ai.explain_prompt import SAMPLE_MODEL_INPUT, explain_request
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.ai.limits import input_estimate, reservation
from recording_site import RecordingSite
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from wording_fixtures import (
    CHAT_PATH,
    INSIGHT_ASYNC_DSN,
    MODEL,
    Connected,
    answer,
    connect,
    finding,
    one,
    openai_reply,
    run_word,
    version,
)

from nexovue_scheduler.insight import transport
from nexovue_scheduler.probes.addresses import PrivateEndpoints

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_insight_limits_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


def _reserved_per_run(base_url: str) -> int:
    body = (
        wire_format("openai_chat")
        .build(explain_request(SAMPLE_MODEL_INPUT), model=MODEL, base_url=base_url, key="k")
        .body
    )
    return reservation(input_estimate(body))


async def _usage(ai_install: AsyncEngine, tenant: AlertTenant) -> tuple[int, int]:
    row = await one(
        ai_install,
        "SELECT used_tokens, reserved_tokens FROM ai_usage_monthly WHERE org_id = :org",
        org=tenant.org_id,
    )
    return (0, 0) if row is None else (row.used_tokens, row.reserved_tokens)


# Long enough for eight runs to be admitted or refused on a loaded machine; a run held past
# it is let through, and the test fails on `all_decided` rather than hanging.
HOLD_SECONDS: Final = 30.0


class _ProviderHold:
    """Holds every run that reaches the provider until each run has either reached it or
    ended, so every reservation is decided before any run settles its smaller, honest count.
    Without it, a run admitted after two had settled would rightly find room for a third."""

    def __init__(self, runs: int) -> None:
        self._undecided = runs
        self._changed = threading.Condition()
        self.all_decided = True

    def decided(self) -> None:
        with self._changed:
            self._undecided -= 1
            self._changed.notify_all()

    def send(
        self, call: HttpCall, *, timeout_seconds: int, private_endpoints: PrivateEndpoints
    ) -> transport.Answer:
        self.decided()
        with self._changed:
            if not self._changed.wait_for(lambda: self._undecided == 0, timeout=HOLD_SECONDS):
                self.all_decided = False
        return transport.send(
            call, timeout_seconds=timeout_seconds, private_endpoints=private_endpoints
        )


def _run_apart(
    connected: Connected,
    explanation_id: str,
    *,
    start: threading.Barrier,
    hold: _ProviderHold,
    at: datetime,
) -> str:
    """One run as production runs it, in a process of its own: here its own thread, event
    loop and database connection, so its blocking provider call holds up no other run."""
    reached_provider = False

    def send(
        call: HttpCall, *, timeout_seconds: int, private_endpoints: PrivateEndpoints
    ) -> transport.Answer:
        nonlocal reached_provider
        reached_provider = True
        return hold.send(call, timeout_seconds=timeout_seconds, private_endpoints=private_endpoints)

    async def run() -> str:
        engine = create_async_engine(INSIGHT_ASYNC_DSN, poolclass=NullPool)
        try:
            start.wait(timeout=HOLD_SECONDS)
            return await run_word(engine, connected, explanation_id, send=send, at=at)
        finally:
            await engine.dispose()

    try:
        return asyncio.run(run())
    finally:
        if not reached_provider:
            hold.decided()


async def test_parallel_runs_past_the_limit_leave_used_and_reserved_under_it(
    ai_install: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC53: eight runs at once against a limit that admits two reservations: two call, the
    rest are `over_limit` and spend nothing, and used plus reserved never passes the limit.

    The runs start together and run truly in parallel, each on its own connection, and the
    clock is this test's own, so the stack's AI sweep never takes a run as died."""
    per_run = _reserved_per_run(provider_site.url("/v1"))
    limit = int((2 * per_run + per_run // 2) / 0.8)
    connected = await connect(ai_install, ai_tenant, provider_site, limit=limit)
    versions = [
        await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant)) for _ in range(8)
    ]
    provider_site.replies[CHAT_PATH] = openai_reply(
        answer(), prompt_tokens=per_run // 2, completion_tokens=10, delay=0.5
    )
    start = threading.Barrier(len(versions))
    hold = _ProviderHold(len(versions))
    at = datetime.now(UTC).replace(microsecond=0)

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=len(versions)) as threads:
        runs = asyncio.gather(
            *(
                loop.run_in_executor(
                    threads, partial(_run_apart, connected, each, start=start, hold=hold, at=at)
                )
                for each in versions
            )
        )
        highest = 0
        while not runs.done():
            used, reserved = await _usage(ai_install, ai_tenant)
            highest = max(highest, used + reserved)
            await asyncio.sleep(0.02)
        outcomes = await runs

    assert hold.all_decided
    assert sorted(outcomes).count("done") == 2
    assert sorted(outcomes).count("refused:over_limit") == 6
    assert len(provider_site.requests) == 2
    used, reserved = await _usage(ai_install, ai_tenant)
    assert reserved == 0 and used == 2 * (per_run // 2 + 10)
    assert highest <= int(limit * 0.8) and used + reserved <= limit


async def test_a_run_past_its_reservation_is_counted_and_the_next_is_refused(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC53, BR13: the provider reports more than was reserved; the actual is counted, usage
    passes the limit by that one run, and the next run is refused `over_limit`."""
    per_run = _reserved_per_run(provider_site.url("/v1"))
    limit = int(per_run * 1.5 / 0.8)
    connected = await connect(ai_install, ai_tenant, provider_site, limit=limit)
    first = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    second = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    provider_site.replies[CHAT_PATH] = openai_reply(
        answer(), prompt_tokens=limit, completion_tokens=500
    )

    assert await run_word(ai_engine, connected, first) == "done"
    used, reserved = await _usage(ai_install, ai_tenant)
    assert (used, reserved) == (limit + 500, 0) and used > limit
    assert await run_word(ai_engine, connected, second) == "refused:over_limit"
    assert len(provider_site.requests) == 1


async def test_warning_findings_stop_at_80_percent_and_a_critical_one_still_runs(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC55, FR40: with 85% of the limit used, a warning finding's run is refused and a
    critical finding's runs, from the 20% kept for it."""
    per_run = _reserved_per_run(provider_site.url("/v1"))
    limit = per_run * 10
    connected = await connect(ai_install, ai_tenant, provider_site, limit=limit)
    spent = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    provider_site.replies[CHAT_PATH] = openai_reply(
        answer(), prompt_tokens=int(limit * 0.85) - 10, completion_tokens=10
    )
    assert await run_word(ai_engine, connected, spent) == "done"
    provider_site.replies[CHAT_PATH] = openai_reply(answer())

    warning = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    critical_finding = await finding(ai_install, ai_tenant, severity="critical")
    critical = await version(ai_install, ai_tenant, critical_finding, severity="critical")

    assert await run_word(ai_engine, connected, warning) == "refused:over_limit"
    assert await run_word(ai_engine, connected, critical) == "done"
