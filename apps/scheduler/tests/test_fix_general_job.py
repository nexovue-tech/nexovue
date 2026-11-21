"""Rung 2b's job as `nexovue_insight`, against the fake provider (F5 plan T2b.6; spec FR34,
FR35, FR38, D32, D51; AC75's run half).

A usable answer is stored after G1 to G9 with its evidence ids and tokens, its call writes
one `ai_runs` row with purpose `fix` and `fields_sent` of `model_input` and `language`, and
its tokens move from reserved to used under F5's share. Everything that must still hold is
read again before anything is sent: AI on, the project not opted out, the consent covering
these values, and F5's share of the month. A row that moved on is never overwritten.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
import structlog
from alert_fixtures import AlertTenant, postgres_reachable
from general_fix_fixtures import (
    a_general_fix,
    fix_row,
    fix_runs,
    run_suggest,
    suggestion,
    usage,
)
from live import require_stack
from nexovue_platform.ai.contract import HttpCall
from nexovue_platform.fixes.general_prompt import MAX_OUTPUT_TOKENS, PROMPT_VERSION
from recording_site import RecordingSite, json_reply
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import (
    CHAT_PATH,
    SWEPT_RUN_AGE,
    Killed,
    connect,
    finding,
    just_past,
    killed,
    openai_reply,
    sweep_apart,
)

from nexovue_scheduler.insight import transport
from nexovue_scheduler.insight.suggest_general_fix import LATE_FINISH, GeneralFixFailed
from nexovue_scheduler.jobs.registry import Retry
from nexovue_scheduler.probes.addresses import PrivateEndpoints

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_general_job.py",
    remedy="Postgres is not reachable — run `just up`",
)


async def _ready(
    install: AsyncEngine, tenant: AlertTenant, site: RecordingSite, at: Any, **connect_with: Any
) -> tuple[Any, str]:
    connected = await connect(install, tenant, site, now=at, **connect_with)
    fix = await a_general_fix(install, tenant, await finding(install, tenant, now=at), at=at)
    return connected, fix


async def test_ac75_a_usable_answer_is_stored_metered_and_recorded_as_a_fix_run(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    provider_site.replies[CHAT_PATH] = openai_reply(suggestion())

    did = await run_suggest(ai_engine, connected, fix, at=now)

    assert did == "proposed"
    row = await fix_row(ai_install, fix)
    assert row.state == "proposed" and row.reason is None and row.retry_code is None
    assert row.general_suggestion == suggestion()
    assert row.evidence_ids == ["e1", "e2"]
    assert row.tokens_used == 460 and row.finished_at == now
    [run] = await fix_runs(ai_install, ai_tenant)
    assert (run.purpose, run.outcome, run.attempt) == ("fix", "done", 1)
    assert run.fields_sent == ["model_input", "language"]
    assert run.prompt_version == PROMPT_VERSION
    assert run.reserved_tokens > MAX_OUTPUT_TOKENS
    assert await usage(ai_install, ai_tenant) == (460, 0)


async def test_an_abstention_is_a_refusal_with_its_reason_and_nothing_stored(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    provider_site.replies[CHAT_PATH] = openai_reply(
        suggestion(
            outcome="abstain",
            abstain_reason="evidence_too_thin",
            summary=None,
            steps=[],
            example=None,
            evidence_ids=[],
        )
    )

    assert await run_suggest(ai_engine, connected, fix, at=now) == "refused:evidence_too_thin"
    row = await fix_row(ai_install, fix)
    assert (row.state, row.reason, row.general_suggestion) == (
        "refused",
        "evidence_too_thin",
        None,
    )


@pytest.mark.parametrize(
    ("label", "connect_with", "reason"),
    [
        ("AI switched off", {"enabled": False}, "ai_off"),
        ("consent no longer covering the values", {"consented": False}, "consent_ended_in_flight"),
        ("F5's share of the month spent", {"limit": 1}, "limit_reached"),
    ],
)
async def test_what_must_still_hold_is_read_again_and_nothing_is_sent(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
    label: str,
    connect_with: dict[str, Any],
    reason: str,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now, **connect_with)
    provider_site.replies[CHAT_PATH] = openai_reply(suggestion())

    assert await run_suggest(ai_engine, connected, fix, at=now) == f"failed:{reason}", label
    assert provider_site.requests == []
    assert (await fix_row(ai_install, fix)).reason == reason
    assert await usage(ai_install, ai_tenant) == (0, 0)


async def test_an_opted_out_project_sends_nothing(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    async with ai_install.begin() as connection:
        await connection.execute(
            text("INSERT INTO ai_project_opt_outs (org_id, project_id) VALUES (:org, :project)"),
            {"org": ai_tenant.org_id, "project": ai_tenant.project_id},
        )

    assert await run_suggest(ai_engine, connected, fix, at=now) == "failed:project_opted_out"
    assert provider_site.requests == []


async def test_a_row_that_left_proposing_is_never_overwritten_and_spends_nothing(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
) -> None:
    connected = await connect(ai_install, ai_tenant, provider_site, now=now)
    fix = await a_general_fix(
        ai_install, ai_tenant, await finding(ai_install, ai_tenant, now=now), at=now, state="failed"
    )

    assert await run_suggest(ai_engine, connected, fix, at=now) == "stale"
    assert provider_site.requests == []
    assert (await fix_row(ai_install, fix)).reason == "timed_out"


async def test_d51_a_wrong_shape_retries_as_a_new_attempt_reading_its_fixed_code(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    provider_site.replies[CHAT_PATH] = openai_reply("not json at all")

    with pytest.raises(Retry):
        await run_suggest(ai_engine, connected, fix, at=now)
    kept = await fix_row(ai_install, fix)
    assert (kept.state, kept.retry_code, kept.tokens_used) == ("proposing", "general_shape", 460)

    provider_site.replies[CHAT_PATH] = openai_reply(suggestion())
    assert await run_suggest(ai_engine, connected, fix, at=now, attempt=2) == "proposed"
    second = provider_site.requests[1].body
    assert b"did not fit the schema" in second and b"not json at all" not in second
    runs = await fix_runs(ai_install, ai_tenant)
    assert [(run.attempt, run.outcome) for run in runs] == [(1, "invalid"), (2, "done")]
    assert (await fix_row(ai_install, fix)).tokens_used == 920


async def test_a_5xx_gets_one_more_attempt_then_fails_as_the_provider_not_answering(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    provider_site.replies[CHAT_PATH] = json_reply({"error": "down"}, status=503)

    with pytest.raises(Retry):
        await run_suggest(ai_engine, connected, fix, at=now)
    assert (
        await run_suggest(ai_engine, connected, fix, at=now, attempt=2)
        == "failed:provider_unavailable"
    )
    assert await usage(ai_install, ai_tenant) != (0, 0)


async def test_a_provider_refusal_ends_at_once(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    provider_site.replies[CHAT_PATH] = openai_reply("", refusal="I will not help with that.")

    assert await run_suggest(ai_engine, connected, fix, at=now) == "failed:provider_refused"


async def test_an_uncaught_error_writes_failed_before_it_is_raised_with_no_body(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: Any,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)

    def broken(*_: Any, **__: Any) -> transport.Answer:
        raise RuntimeError("provider said PROVIDER-SECRET-BODY")

    with pytest.raises(GeneralFixFailed) as raised:
        await run_suggest(ai_engine, connected, fix, at=now, send=broken)

    assert str(raised.value) == "error" and "PROVIDER" not in repr(raised.value)
    assert (await fix_row(ai_install, fix)).reason == "nexovue_error"


async def test_a_call_finishing_after_f3_s_sweep_ended_its_run_changes_nothing(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    """F3's rule (docs/changes/wording-settles-once): a child that outlives its hard limit
    answers after F3's sweep called its `fix` run `died`. The sweep's charge stands, its
    reservation is not subtracted again, so another run's reservation survives, and the
    answer is never stored: the row stays `proposing` for rung 2b's own sweep to end."""
    connected, holding = await _ready(ai_install, ai_tenant, provider_site, now)
    with pytest.raises(Killed):
        await run_suggest(ai_engine, connected, holding, at=now, send=killed)
    started = now - SWEPT_RUN_AGE
    late = await a_general_fix(
        ai_install, ai_tenant, await finding(ai_install, ai_tenant, now=started), at=started
    )
    provider_site.replies[CHAT_PATH] = openai_reply(suggestion())

    def swept_during_the_call(
        call: HttpCall, *, timeout_seconds: int, private_endpoints: PrivateEndpoints
    ) -> transport.Answer:
        sweep_apart(just_past(started))
        return transport.send(
            call, timeout_seconds=timeout_seconds, private_endpoints=private_endpoints
        )

    with structlog.testing.capture_logs() as logs:
        did = await run_suggest(ai_engine, connected, late, at=started, send=swept_during_the_call)

    died, held = await fix_runs(ai_install, ai_tenant)
    assert (died.outcome, died.input_tokens, died.output_tokens) == (
        "died",
        died.reserved_tokens,
        0,
    )
    assert held.outcome == "started" and held.reserved_tokens > 0
    assert await usage(ai_install, ai_tenant) == (died.reserved_tokens, held.reserved_tokens)
    row = await fix_row(ai_install, late)
    assert (row.state, row.general_suggestion, row.evidence_ids, row.tokens_used) == (
        "proposing",
        None,
        None,
        0,
    )
    assert did == "stale" and len(provider_site.requests) == 1
    assert [log["log_level"] for log in logs if log["event"] == LATE_FINISH] == ["warning"]
