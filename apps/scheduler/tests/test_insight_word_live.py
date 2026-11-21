"""The wording job and the AI sweep, end to end against a fake provider and real Postgres, as
`nexovue_insight` (F3 plan T5.4, T5.5; AC36's bytes, AC39, AC52, AC54, AC56, AC57's spend,
AC58, AC59, AC60, AC81, AC82, AC94's spending, AC96).

No real provider is called: the provider is `recording_site` (see `wording_fixtures`). The
clock is pinned. This suite fails rather than skips when Postgres is down.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta
from typing import Any

import pytest
import structlog
from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from nexovue_platform.ai.explain_prompt import PROMPT_VERSION, SAMPLE_MODEL_INPUT, explain_request
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.evidence import (
    INSIGHT_QUEUE,
    WORD_EXPLANATION_TASK,
    word_explanation_request,
)
from recording_site import RecordingSite, Reply
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import (
    CHAT_PATH,
    GOOD,
    MODEL,
    NOW,
    PROVIDER_KEY,
    answer,
    connect,
    every,
    finding,
    one,
    openai_reply,
    run_word,
    version,
    wording_of,
)

from nexovue_scheduler.insight import sweep, transport
from nexovue_scheduler.insight.word import RETRY_SAYS, WordingFailed
from nexovue_scheduler.jobs import registry
from nexovue_scheduler.jobs.registry import Retry

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_insight_word_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

MARKER = "PROVIDER-MARKER-9c41"


async def _usage(ai_install: AsyncEngine, ai_tenant: AlertTenant) -> tuple[int, int]:
    row = await one(
        ai_install,
        "SELECT used_tokens, reserved_tokens FROM ai_usage_monthly WHERE org_id = :org",
        org=ai_tenant.org_id,
    )
    return (0, 0) if row is None else (row.used_tokens, row.reserved_tokens)


async def _runs(ai_install: AsyncEngine, ai_tenant: AlertTenant) -> list[Any]:
    return await every(
        ai_install,
        "SELECT * FROM ai_runs WHERE org_id = :org ORDER BY started_at, attempt",
        org=ai_tenant.org_id,
    )


# ------------------------------------------------------------------- written


async def test_a_run_writes_checked_words_and_records_and_meters_the_call(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC36: the bytes the provider receives are `WireFormat.build` over the stored input;
    the run row holds their hash and the prompt version and no content; the tokens move from
    reserved to used (BR13)."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    provider_site.replies[CHAT_PATH] = openai_reply(answer(picks=["c2", "c9"]))

    did = await run_word(ai_engine, connected, explanation)

    assert did == "done"
    [sent] = provider_site.requests
    expected = wire_format("openai_chat").build(
        explain_request(SAMPLE_MODEL_INPUT), model=MODEL, base_url=connected.base_url, key=None
    )
    assert sent.body == expected.body and sent.path == CHAT_PATH
    assert sent.headers["authorization"] == f"Bearer {PROVIDER_KEY}"
    stored = await wording_of(ai_install, explanation)
    assert stored.wording_state == "done" and stored.wording_from_version == 1
    assert stored.wording == {
        "claims": [{"type": "timing", "text": GOOD, "evidence_ids": ["e1"]}],
        "next_checks": ["c2"],
        "abstain": False,
        "model": MODEL,
    }
    [run] = await _runs(ai_install, ai_tenant)
    assert (run.purpose, run.outcome, run.attempt, run.generation) == ("explain", "done", 1, 1)
    assert str(run.explanation_id) == explanation and run.started_at == NOW
    assert (run.input_tokens, run.output_tokens, run.tokens_estimated) == (400, 60, False)
    assert run.request_sha256 == hashlib.sha256(sent.body).hexdigest()
    assert run.prompt_version == PROMPT_VERSION
    assert "facts[].kind" in run.fields_sent and b"checkout" not in sent.body
    assert await _usage(ai_install, ai_tenant) == (460, 0)
    # "View what was sent" rebuilds these bytes from the stored input, and says they match.
    from sqlalchemy.ext.asyncio import AsyncSession

    from nexovue_api.ai import service as ai_settings

    async with AsyncSession(ai_install) as session:
        shown = await ai_settings.sent(session, org_id=ai_tenant.org_id, run_id=run.id)
    assert shown.match == "matches" and shown.body == sent.body.decode()


async def test_the_job_carries_ids_and_its_generation_only() -> None:
    """AC60: the job reads `model_input` itself; its limits follow the connection."""
    request = word_explanation_request(
        explanation_id="e", org_id="o", generation=3, requested_by=None, call_seconds=45
    )
    assert request.task == WORD_EXPLANATION_TASK and request.queue == INSIGHT_QUEUE
    assert request.kwargs == {
        "explanation_id": "e",
        "org_id": "o",
        "generation": 3,
        "requested_by": None,
    }
    assert (request.soft_limit_seconds, request.hard_limit_seconds) == (65, 85)
    assert request.max_attempts == 2 and request.expires_seconds == 3600
    assert WORD_EXPLANATION_TASK in registry.registered(registry.TASK_SETS["insight"])


async def test_an_abstention_is_stored_with_no_words(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    connected = await connect(ai_install, ai_tenant, provider_site)
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    provider_site.replies[CHAT_PATH] = openai_reply(answer(abstain=True))

    assert await run_word(ai_engine, connected, explanation) == "abstained"
    stored = await wording_of(ai_install, explanation)
    assert stored.wording_state == "abstained" and stored.wording is None
    assert [run.outcome for run in await _runs(ai_install, ai_tenant)] == ["abstained"]


# ---------------------------------------------------------- nothing is spent


@pytest.mark.parametrize(
    ("change", "left"),
    [
        pytest.param(
            "UPDATE ai_connections SET enabled = false WHERE org_id = :org",
            "off",
            id="ai turned off",
        ),
        pytest.param(
            "INSERT INTO ai_project_opt_outs (org_id, project_id) VALUES (:org, :project)",
            "off",
            id="project opted out",
        ),
        pytest.param(
            "UPDATE projects SET deleted_at = now() WHERE id = :project",
            "off",
            id="project deleted",
        ),
        pytest.param(
            "UPDATE ai_connections SET consented_fingerprint = repeat('0', 64) WHERE org_id = :org",
            "off",
            id="consent no longer covers the values",
        ),
        pytest.param(
            "UPDATE ai_connections SET base_url = 'http://8.8.8.8/v1', consented_base_url = "
            "'http://8.8.8.8/v1' WHERE org_id = :org",
            "off",
            id="an address failing FR23",
        ),
        pytest.param(
            "UPDATE finding_explanations SET wording_state = 'held' WHERE org_id = :org",
            "held",
            id="muted while queued",
        ),
        pytest.param(
            "UPDATE finding_explanations SET wording_generation = 2 WHERE org_id = :org",
            "pending",
            id="a newer click",
        ),
    ],
)
async def test_a_queued_run_that_is_no_longer_wanted_sends_nothing_and_spends_nothing(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    change: str,
    left: str,
) -> None:
    """AC39, AC81: an opt-out, a deleted project, AI turned off, a mute, or a click since:
    the fake provider receives nothing, no run row is written and nothing is reserved; the
    older job of a newer click stops without overwriting it."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    async with ai_install.begin() as connection:
        await connection.execute(
            text(change), {"org": ai_tenant.org_id, "project": ai_tenant.project_id}
        )

    await run_word(ai_engine, connected, explanation)

    assert provider_site.requests == []
    assert (await wording_of(ai_install, explanation)).wording_state == left
    assert await _runs(ai_install, ai_tenant) == [] and await _usage(ai_install, ai_tenant) == (
        0,
        0,
    )


async def test_only_absences_abstain_without_a_call(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """FR33: with only absences, no model is called."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    only_absences = dict(SAMPLE_MODEL_INPUT, likely_cause=None)
    only_absences["facts"] = [f for f in SAMPLE_MODEL_INPUT["facts"] if f["kind"] == "absence"]
    explanation = await version(
        ai_install, ai_tenant, await finding(ai_install, ai_tenant), model_input=only_absences
    )

    assert await run_word(ai_engine, connected, explanation) == "abstained"
    assert provider_site.requests == [] and await _runs(ai_install, ai_tenant) == []


async def test_a_superseded_version_s_job_stops_without_spending(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC56: a non-material version over a pending one gets its own job; the older one sees
    the newer version and stops."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    found = await finding(ai_install, ai_tenant)
    older = await version(ai_install, ai_tenant, found)
    newer = await version(ai_install, ai_tenant, found, number=2)
    provider_site.replies[CHAT_PATH] = openai_reply(answer())

    assert await run_word(ai_engine, connected, older) == "stale"
    assert provider_site.requests == []
    assert await run_word(ai_engine, connected, newer) == "done"


async def test_a_key_that_will_not_open_for_this_address_fails_without_a_call(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """Spec, *Error cases*: a key sealed for another address, or missed by a rotation, is
    `bad_key`, and nothing is sent."""
    connected = await connect(
        ai_install, ai_tenant, provider_site, sealed_for="https://elsewhere.example/v1"
    )
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))

    assert await run_word(ai_engine, connected, explanation) == "failed:bad_key"
    assert provider_site.requests == []


async def test_an_input_over_the_limit_ends_too_long_before_it_reserves(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC94: a run over `MAX_INPUT_TOKENS` ends `too_long` and reserves nothing."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    huge = dict(SAMPLE_MODEL_INPUT)
    huge["facts"] = [
        dict(SAMPLE_MODEL_INPUT["facts"][1], id=f"e{number}") for number in range(1, 400)
    ]
    explanation = await version(
        ai_install, ai_tenant, await finding(ai_install, ai_tenant), model_input=huge
    )

    assert await run_word(ai_engine, connected, explanation) == "refused:too_long"
    assert provider_site.requests == [] and await _runs(ai_install, ai_tenant) == []
    assert await _usage(ai_install, ai_tenant) == (0, 0)
    assert (await wording_of(ai_install, explanation)).wording_reason == "too_long"


# ----------------------------------------------------------------- the cap


async def _decided(
    ai_install: AsyncEngine, ai_tenant: AlertTenant, finding_id: Any, count: int
) -> None:
    async with ai_install.begin() as connection:
        for hours in range(count):
            await connection.execute(
                text(
                    "INSERT INTO ai_runs (org_id, purpose, subject_id, wire_format, model, "
                    "provider_host, attempt, started_at, outcome) VALUES (:org, 'explain', "
                    ":finding, 'openai_chat', 'm', 'https://p.example', 1, :at, 'done')"
                ),
                {
                    "org": ai_tenant.org_id,
                    "finding": finding_id,
                    "at": NOW - timedelta(hours=hours),
                },
            )


async def test_an_automatic_run_over_the_cap_shows_its_reason_and_spends_nothing(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC58: three decided runs in 24 hours, and a fourth is `failed`, reason `run_cap`."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    found = await finding(ai_install, ai_tenant)
    await _decided(ai_install, ai_tenant, found, 3)
    explanation = await version(ai_install, ai_tenant, found)

    assert await run_word(ai_engine, connected, explanation) == "refused:run_cap"
    assert provider_site.requests == [] and len(await _runs(ai_install, ai_tenant)) == 3
    stored = await wording_of(ai_install, explanation)
    assert (stored.wording_state, stored.wording_reason) == ("failed", "run_cap")


async def test_four_parallel_jobs_of_one_version_make_one_call_and_one_result(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC56, AC58: a job run twice (or four times at once) under the cap's row lock: one call,
    one stored result, and the cap not passed."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    found = await finding(ai_install, ai_tenant)
    await _decided(ai_install, ai_tenant, found, 2)
    explanation = await version(ai_install, ai_tenant, found)
    provider_site.replies[CHAT_PATH] = openai_reply(answer(), delay=0.5)

    outcomes = await asyncio.gather(
        *(run_word(ai_engine, connected, explanation) for _ in range(4))
    )

    assert sorted(outcomes) == ["done", "stale", "stale", "stale"]
    assert len(provider_site.requests) == 1 and len(await _runs(ai_install, ai_tenant)) == 3
    assert (await wording_of(ai_install, explanation)).wording_state == "done"


async def test_an_invalid_answer_and_its_retry_are_one_decided_run(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC58, FR38: invalid output asks for one more attempt, with its own run row and
    reservation; the cap counts the first attempt only."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    found = await finding(ai_install, ai_tenant)
    await _decided(ai_install, ai_tenant, found, 2)
    explanation = await version(ai_install, ai_tenant, found)
    provider_site.replies[CHAT_PATH] = openai_reply(
        answer({"type": "timing", "text": "x", "evidence_ids": []})
    )

    with pytest.raises(Retry) as asked:
        await run_word(ai_engine, connected, explanation)
    assert str(asked.value) == RETRY_SAYS["invalid"]
    assert (await wording_of(ai_install, explanation)).wording_state == "pending"
    provider_site.replies[CHAT_PATH] = openai_reply(answer())
    assert await run_word(ai_engine, connected, explanation, attempt=2) == "done"

    runs = [run for run in await _runs(ai_install, ai_tenant) if run.explanation_id is not None]
    assert [(run.attempt, run.outcome) for run in runs] == [(1, "invalid"), (2, "done")]
    assert await _usage(ai_install, ai_tenant) == (920, 0)


async def test_an_invalid_answer_on_the_last_attempt_fails_and_keeps_the_facts(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC52: no claim left and no abstention: `failed`, reason `invalid`."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    provider_site.replies[CHAT_PATH] = openai_reply(
        answer({"type": "timing", "text": GOOD, "evidence_ids": []})
    )

    assert await run_word(ai_engine, connected, explanation, attempt=2) == "failed:invalid"


# ------------------------------------------------------------ the provider


@pytest.mark.parametrize(
    ("reply", "outcome", "countdown"),
    [
        pytest.param(Reply(status=503, body=b"{}"), "unavailable", 60.0, id="a 5xx"),
        pytest.param(
            Reply(status=429, body=b"{}", headers={"Retry-After": "17"}),
            "limited",
            17.0,
            id="a 429 with Retry-After",
        ),
    ],
)
async def test_a_5xx_and_a_429_get_one_more_attempt_then_fail(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    reply: Reply,
    outcome: str,
    countdown: float,
) -> None:
    """AC82: one further attempt, waiting `Retry-After` for a 429, then `failed`."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    provider_site.replies[CHAT_PATH] = reply

    with pytest.raises(Retry) as asked:
        await run_word(ai_engine, connected, explanation)
    assert asked.value.countdown == countdown
    assert await run_word(ai_engine, connected, explanation, attempt=2) == f"failed:{outcome}"
    assert [run.outcome for run in await _runs(ai_install, ai_tenant)] == [outcome, outcome]


async def test_a_429_asking_past_the_job_s_life_fails_now(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    connected = await connect(ai_install, ai_tenant, provider_site)
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    provider_site.replies[CHAT_PATH] = Reply(
        status=429, body=b"{}", headers={"Retry-After": "7200"}
    )

    assert await run_word(ai_engine, connected, explanation) == "failed:limited"


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        pytest.param(openai_reply("", refusal="I will not."), "refused", id="a 200 refusal"),
        pytest.param(Reply(status=401, body=b"{}"), "bad_key", id="a refused key"),
        pytest.param(Reply(status=404, body=b"{}"), "not_found", id="model not found"),
        pytest.param(Reply(status=400, body=b"{}"), "bad_request", id="a malformed request"),
        pytest.param(openai_reply(answer(), finish="length"), "too_long", id="cut off"),
    ],
)
async def test_an_answer_no_retry_can_change_fails_at_once_with_its_reason(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    reply: Reply,
    reason: str,
) -> None:
    """Design, *Failure handling*: a refusal (a 200 read before parsing, AC51), a bad key, a
    missing model, a bad request or a cut-off answer is not retried."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    provider_site.replies[CHAT_PATH] = reply

    assert await run_word(ai_engine, connected, explanation) == f"failed:{reason}"
    stored = await wording_of(ai_install, explanation)
    assert (stored.wording_state, stored.wording_reason, stored.wording) == ("failed", reason, None)


# ------------------------------------------------- killed, failed, swept


class _Killed(BaseException):
    """What the hard limit does to a child: nothing after it runs."""


def _killed(*_: Any, **__: Any) -> transport.Answer:
    raise _Killed


async def test_a_killed_run_keeps_its_reservation_and_the_sweep_marks_it_died(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC54: recorded as started before the call; the sweep, as `nexovue_insight`, marks it
    `died`, moves its reservation to used, flagged estimated, and fails the explanation."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))

    with pytest.raises(_Killed):
        await run_word(ai_engine, connected, explanation, send=_killed)
    [run] = await _runs(ai_install, ai_tenant)
    assert run.outcome == "started" and run.reserved_tokens > 0
    assert await _usage(ai_install, ai_tenant) == (0, run.reserved_tokens)

    await sweep.sweep(ai_engine, clock=lambda: NOW + sweep.DIED_AFTER + timedelta(minutes=1))

    [died] = await _runs(ai_install, ai_tenant)
    assert (died.outcome, died.tokens_estimated, died.input_tokens) == (
        "died",
        True,
        run.reserved_tokens,
    )
    assert await _usage(ai_install, ai_tenant) == (run.reserved_tokens, 0)
    stored = await wording_of(ai_install, explanation)
    assert (stored.wording_state, stored.wording_reason) == ("failed", "died")


async def test_a_click_after_a_killed_run_is_not_overwritten_by_the_sweep(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC81: the sweep acts only on a matching generation."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    with pytest.raises(_Killed):
        await run_word(ai_engine, connected, explanation, send=_killed)
    async with ai_install.begin() as connection:
        await connection.execute(
            text(
                "UPDATE finding_explanations SET wording_generation = 2, wording_changed_at = "
                ":at WHERE id = :id"
            ),
            {"id": explanation, "at": NOW + sweep.DIED_AFTER},
        )

    await sweep.sweep(ai_engine, clock=lambda: NOW + sweep.DIED_AFTER + timedelta(minutes=1))

    stored = await wording_of(ai_install, explanation)
    assert (stored.wording_state, stored.wording_generation) == ("pending", 2)


async def test_wording_left_pending_past_its_job_s_life_is_failed_expired_by_the_sweep(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC81: the container was stopped and the job expired: once the sweep runs, as
    `nexovue_insight`, the row is `failed`, reason `expired`; a fresh one is left alone."""
    await connect(ai_install, ai_tenant, provider_site)
    found = await finding(ai_install, ai_tenant)
    stuck = await version(ai_install, ai_tenant, found, changed_at=NOW - timedelta(hours=2))
    fresh = await version(ai_install, ai_tenant, found, number=2)

    swept = await sweep.sweep(ai_engine, clock=lambda: NOW)

    assert swept.expired >= 1
    stored = await wording_of(ai_install, stuck)
    assert (stored.wording_state, stored.wording_reason) == ("failed", "expired")
    assert (await wording_of(ai_install, fresh)).wording_state == "pending"


async def test_an_uncaught_error_writes_failed_before_it_is_raised_with_no_body(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
) -> None:
    """AC54, AC59: the worker logs what is raised, so it holds one closed word."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))

    def broken(*_: Any, **__: Any) -> transport.Answer:
        raise RuntimeError(f"the provider said {MARKER}")

    with pytest.raises(WordingFailed) as raised:
        await run_word(ai_engine, connected, explanation, send=broken)

    assert str(raised.value) == "error" and raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    stored = await wording_of(ai_install, explanation)
    assert (stored.wording_state, stored.wording_reason) == ("failed", "error")


# ------------------------------------------------- nothing a provider says is kept


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(
            openai_reply(
                answer(
                    {"type": "timing", "text": f"See {MARKER}.example now", "evidence_ids": ["e1"]}
                )
            ),
            id="a 200 whose words fail the checks",
        ),
        pytest.param(Reply(status=500, body=MARKER.encode()), id="a 500"),
        pytest.param(Reply(status=429, body=MARKER.encode()), id="a 429"),
        pytest.param(openai_reply(f"{{not json {MARKER}"), id="invalid JSON"),
        pytest.param(openai_reply("", refusal=MARKER), id="a refusal"),
        pytest.param(openai_reply(answer(), model=MARKER + " <b>"), id="a planted model name"),
        pytest.param(openai_reply(answer(), model=MARKER), id="a model name fitting the pattern"),
    ],
)
async def test_no_table_or_log_holds_what_the_provider_said(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    reply: Reply,
) -> None:
    """AC59, AC96: a marker in every kind of answer reaches no row outside the wording the
    checks passed, no job's error, no result and no log; the label is the configured model
    (FR19, AC51)."""
    connected = await connect(ai_install, ai_tenant, provider_site)
    explanation = await version(ai_install, ai_tenant, await finding(ai_install, ai_tenant))
    provider_site.replies[CHAT_PATH] = reply

    with structlog.testing.capture_logs() as logs:
        for attempt in (1, 2):
            try:
                await run_word(ai_engine, connected, explanation, attempt=attempt)
            except Retry as retry:
                assert MARKER not in str(retry)
                continue
            break

    stored = await wording_of(ai_install, explanation)
    if stored.wording is not None:
        assert stored.wording["model"] == MODEL
    assert MARKER not in json.dumps(stored.wording or {})
    assert MARKER not in repr(logs)
    for table in ("ai_runs", "ai_usage_monthly", "jobs", "job_results", "finding_explanations"):
        rows = await every(
            ai_install,
            f"SELECT row_to_json(t)::text AS row FROM {table} AS t WHERE t.org_id = :org",
            org=ai_tenant.org_id,
        )
        assert not any(MARKER in row.row for row in rows), table


async def test_the_sweep_s_statements_run_as_the_ai_account() -> None:
    """T5.5: the sweep is registered only for the AI role; its own grants run it above."""
    assert "nexovue_scheduler.insight.sweep" in registry.TASK_SETS["insight"]
    assert sweep.SWEEP_SECONDS == 300
    assert timedelta(seconds=300 + 40) < sweep.DIED_AFTER
