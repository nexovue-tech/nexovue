"""Rung 2b's routes against real Postgres (F5 plan T2b.7; spec FR31 to FR38; AC67, AC71,
AC72's API half, AC73's route half, AC74, AC75's click half, AC77's listing half).

Every row is this test's own, inside a transaction rolled back at the end. The routes read
the real clock; each test sets its rows from its own `now`, taken once.

This suite fails rather than skips when Postgres is down; see tests/helpers/live.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import general_fix_rows as rows
import pytest
from general_fix_rows import (
    BASE_URL,
    CAUSE,
    NO_CAUSE_INPUT,
    POSTGRES_DSN,
    REAL_NAME,
    STORED,
)
from live import require_stack
from nexovue_platform.fixes.records import FIX_QUEUE, SUGGEST_GENERAL_FIX_TASK
from nexovue_platform.fixes.sentences import (
    ELIGIBILITY_SENTENCES,
    GENERAL_ACTION,
    GENERAL_EARLIER_READING,
    GENERAL_HAS_NO_CODE,
    GENERAL_HEADING,
    GENERAL_LABEL,
    GENERAL_NONE_HEADING,
    GENERAL_PROPOSING_REQUESTER,
    GENERAL_PROPOSING_VIEWER,
    NO_CODE_LOCATION_YET,
    REASON_SENTENCES,
    SECTION_HEADING_UNAVAILABLE,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import AuditLogEntry, FindingFix, Job, Notification
from nexovue_api.settings import API_PREFIX

# The suite's rolled-back session and its own moment, shared with the other rung 2b suites.
db = rows.db
now = rows.now

require_stack(
    reachable=rows.reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_general_routes_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


# ------------------------------------------------------------------ AC67


async def test_ac67_offered_with_the_send_sentence_tries_left_and_the_line_it_stands_under(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding = await rows.ready(db, now)

    body = await rows.read(db, rows.person(acme), finding)

    assert body["line"] == {
        "heading": SECTION_HEADING_UNAVAILABLE,
        "sentence": NO_CODE_LOCATION_YET,
    }
    offer = body["offer"]
    assert offer["action"] == GENERAL_ACTION
    assert offer["send_sentence"] == (
        "Sends this finding's facts, and the language you picked, to provider.example. "
        "No code is read or sent."
    )
    assert offer["languages"][0] == "python" and len(offer["languages"]) == 10
    assert offer["preselected"] is None and offer["tries_left"] == 3
    assert body["latest"] is None and body["earlier"] == []


@pytest.mark.parametrize(
    ("label", "change"),
    [
        ("causes held", {"settings": {"SHOW_HELD_CAUSES": "false"}}),
        ("neither a cause nor an error alias", {"input": NO_CAUSE_INPUT, "cause": None}),
        ("no model_input", {"no_input": True}),
    ],
)
async def test_ac67_with_nothing_to_send_nothing_about_rung_2b_renders(
    db: AsyncSession, now: datetime, label: str, change: dict[str, Any]
) -> None:
    acme = await rows.tenant(db)
    await rows.connect(db, acme)
    finding = await rows.finding(db, acme, now)
    version = await rows.version(
        db, finding, now, model_input=change.get("input"), cause=change.get("cause", CAUSE)
    )
    if change.get("no_input"):
        version.model_input = None
        await db.flush()

    body = await rows.read(db, rows.person(acme), finding, **change.get("settings", {}))

    assert (body["line"], body["offer"], body["latest"]) == (None, None, None), label


@pytest.mark.parametrize(
    ("label", "fields", "reason"),
    [
        (
            "a project finding",
            {"scope": "project", "service_name": None, "services": ["a"]},
            "project_finding",
        ),
        (
            "closed as a false alarm",
            {"state": "closed", "closed_reason": "false_alarm", "closed_at": "now"},
            "closed_as_not_a_problem",
        ),
    ],
)
async def test_ac67_an_earlier_reason_hides_the_action_and_says_why(
    db: AsyncSession, now: datetime, label: str, fields: dict[str, Any], reason: str
) -> None:
    acme = await rows.tenant(db)
    await rows.connect(db, acme)
    if fields.get("closed_at") == "now":
        fields = {**fields, "closed_at": now}
    finding = await rows.finding(db, acme, now, **fields)
    await rows.version(db, finding, now)

    body = await rows.read(db, rows.person(acme), finding)

    assert body["offer"] is None, label
    assert body["line"]["sentence"] == ELIGIBILITY_SENTENCES[reason].requester


async def test_ac67_ai_off_is_said_to_those_who_can_set_it_up_and_to_nobody_else(
    db: AsyncSession, now: datetime
) -> None:
    acme = await rows.tenant(db)
    await rows.connect(db, acme, enabled=False)
    finding = await rows.finding(db, acme, now)
    await rows.version(db, finding, now)

    owner = await rows.read(db, rows.person(acme, "owner"), finding)
    member = await rows.read(db, rows.person(acme, "member"), finding)

    assert owner["line"]["sentence"] == ELIGIBILITY_SENTENCES["ai_off"].requester
    assert owner["offer"] is None
    assert (member["line"], member["offer"]) == (None, None)


async def test_ac11a_the_hosted_service_shows_nothing_about_rung_2b(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding = await rows.ready(db, now)

    body = await rows.read(db, rows.person(acme, "owner"), finding, NEXOVUE_DEPLOYMENT="hosted")

    assert (body["line"], body["offer"], body["latest"]) == (None, None, None)


async def test_ac67_a_finding_at_its_three_tries_today_says_when_the_next_is_allowed(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding = await rows.ready(db, now)
    for minutes_ago in (300, 200, 100):
        await rows.fix(db, finding, now, minutes_ago=minutes_ago)
    # A failure the model never answered uses no try (D32).
    await rows.fix(db, finding, now, state="failed", reason="provider_unavailable", minutes_ago=50)

    body = await rows.read(db, rows.person(acme), finding)

    assert body["offer"] is None
    lifts = (now - timedelta(minutes=300) + timedelta(hours=24)).date().isoformat()
    assert body["line"]["sentence"] == ELIGIBILITY_SENTENCES["limit_reached"].requester.format(
        date=lifts
    )


async def test_d32_a_try_the_model_never_answered_uses_none(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding = await rows.ready(db, now)
    await rows.fix(db, finding, now, minutes_ago=300)
    for minutes_ago in (200, 100):
        await rows.fix(
            db, finding, now, state="failed", reason="provider_unavailable", minutes_ago=minutes_ago
        )

    body = await rows.read(db, rows.person(acme), finding)

    assert body["offer"]["tries_left"] == 2


# ------------------------------------------------------------------ the click


async def test_ac75_the_click_stores_the_row_audits_it_and_adds_one_job_with_ids_only(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding = await rows.ready(db, now)

    response = await rows.ask(db, rows.person(acme), finding, language="go")

    assert response.status_code == 202, response.text
    latest = response.json()["latest"]
    assert latest["state"] == "proposing"
    assert latest["sentence"] == GENERAL_PROPOSING_REQUESTER
    [row] = (await db.scalars(select(FindingFix).where(FindingFix.finding_id == finding.id))).all()
    assert (row.kind, row.state, row.general_language, row.explanation_version) == (
        "general",
        "proposing",
        "go",
        1,
    )
    assert row.requested_by == acme.user.id
    [job] = (
        await db.scalars(
            select(Job).where(Job.org_id == acme.organization.id, Job.queue == FIX_QUEUE)
        )
    ).all()
    assert job.task == SUGGEST_GENERAL_FIX_TASK
    assert job.kwargs == {
        "fix_id": str(row.id),
        "org_id": str(acme.organization.id),
        "requested_by": str(acme.user.id),
    }
    [entry] = (
        await db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.org_id == acme.organization.id,
                AuditLogEntry.action == "fix.general_requested",
            )
        )
    ).all()
    assert entry.actor_user_id == acme.user.id
    assert entry.detail == {
        "fix_id": str(row.id),
        "provider_host": BASE_URL,
        "language": "go",
    }


async def test_br8_a_second_click_while_one_is_in_flight_is_refused_with_its_code(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding = await rows.ready(db, now)
    assert (await rows.ask(db, rows.person(acme), finding)).status_code == 202

    second = await rows.ask(db, rows.person(acme), finding)

    assert second.status_code == 409
    assert second.json()["code"] == "already_running"


async def test_ac67_a_click_when_not_offered_is_refused_with_the_reason_s_code(
    db: AsyncSession, now: datetime
) -> None:
    acme = await rows.tenant(db)
    await rows.connect(db, acme, enabled=False)
    finding = await rows.finding(db, acme, now)
    await rows.version(db, finding, now)

    response = await rows.ask(db, rows.person(acme), finding)

    assert response.status_code == 409 and response.json()["code"] == "ai_off"
    assert (
        await db.scalars(select(FindingFix).where(FindingFix.finding_id == finding.id))
    ).all() == []


async def test_ac74_a_viewer_is_refused_on_the_click(db: AsyncSession, now: datetime) -> None:
    acme, finding = await rows.ready(db, now)

    response = await rows.ask(db, rows.person(acme, "viewer"), finding)

    assert response.status_code == 403


# ------------------------------------------------------------------ the read


async def test_ac72_a_proposed_suggestion_reads_label_first_with_names_restored(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding = await rows.ready(db, now)
    await rows.fix(db, finding, now)

    latest = (await rows.read(db, rows.person(acme), finding))["latest"]

    content = latest["content"]
    assert latest["heading"] == GENERAL_HEADING and latest["sentence"] is None
    assert content["label"] == GENERAL_LABEL
    assert content["lead"] == (
        "Written from this finding's facts only, in python. Not compiled and not run."
    )
    assert {"name": REAL_NAME, "text": None, "at": None} in content["summary"]
    assert all("service_1" not in (segment["text"] or "") for segment in content["summary"])
    assert content["code"] == STORED["example"]["code"]
    assert content["evidence"] == [{"evidence_id": "deploy:d-1", "number": 2}]
    assert content["earlier_reading"] is None


async def test_ac74_a_viewer_and_a_support_view_read_a_proposed_suggestion_without_the_offer(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding = await rows.ready(db, now)
    await rows.fix(db, finding, now)

    viewer = await rows.read(db, rows.person(acme, "viewer"), finding)
    support = await rows.read(db, rows.support_view(acme, now), finding)

    for body in (viewer, support):
        assert body["latest"]["content"]["label"] == GENERAL_LABEL
        assert body["offer"] is None


async def test_a_viewer_reads_the_viewer_s_sentences(db: AsyncSession, now: datetime) -> None:
    acme, finding = await rows.ready(db, now)
    await rows.fix(db, finding, now, state="proposing", suggestion=None)

    latest = (await rows.read(db, rows.person(acme, "viewer"), finding))["latest"]

    assert latest["sentence"] == GENERAL_PROPOSING_VIEWER


@pytest.mark.parametrize(
    ("label", "broken", "reason"),
    [
        ("a URL added", {"summary": "Read https://evil.example first."}, "general_withheld_G4"),
        ("an unknown key", {"confidence": 1}, "invalid_output"),
        ("a shell command", {"steps": ["Run curl first."]}, "general_withheld_G5"),
    ],
)
async def test_ac71_a_row_edited_to_break_a_rule_shows_nothing_and_raises_no_alert(
    db: AsyncSession, now: datetime, label: str, broken: dict[str, Any], reason: str
) -> None:
    acme, finding = await rows.ready(db, now)
    row = await rows.fix(db, finding, now, suggestion={**STORED, **broken})

    latest = (await rows.read(db, rows.person(acme), finding))["latest"]

    assert latest["content"] is None, label
    assert latest["heading"] == GENERAL_NONE_HEADING
    assert latest["sentence"] == REASON_SENTENCES[reason].requester
    await db.refresh(row)
    assert row.state == "proposed"
    assert (
        await db.scalars(select(Notification).where(Notification.org_id == acme.organization.id))
    ).all() == []


async def test_ac77_older_suggestions_are_listed_quietly_and_an_earlier_reading_is_said(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding = await rows.ready(db, now)
    older = await rows.fix(
        db, finding, now, state="refused", reason="general_withheld_G6", minutes_ago=90
    )
    await rows.version(db, finding, now, number=2)
    await rows.fix(db, finding, now, minutes_ago=10)

    body = await rows.read(db, rows.person(acme), finding)

    assert body["latest"]["content"]["earlier_reading"] == GENERAL_EARLIER_READING
    assert body["earlier"] == [
        {
            "id": str(older.id),
            "state": "refused",
            "requested_at": older.requested_at.isoformat().replace("+00:00", "Z"),
        }
    ]


# ------------------------------------------------------------------ AC73


@pytest.mark.parametrize("action", ["check", "patch", "draft-pull-request", "run-ci"])
async def test_ac73_a_general_row_refuses_every_action_on_code_with_a_named_code(
    db: AsyncSession, now: datetime, action: str
) -> None:
    acme, finding = await rows.ready(db, now)
    row = await rows.fix(db, finding, now)

    async with rows.client(db, rows.person(acme)) as client:
        refused = await client.post(f"{API_PREFIX}/findings/{finding.id}/fixes/{row.id}/{action}")
        missing = await client.post(
            f"{API_PREFIX}/findings/{finding.id}/fixes/{uuid.uuid4()}/{action}"
        )

    assert refused.status_code == 409
    assert refused.json()["code"] == "general_has_no_code"
    assert refused.json()["detail"] == GENERAL_HAS_NO_CODE
    assert missing.status_code == 404


async def test_another_organization_s_finding_and_suggestion_are_not_found(
    db: AsyncSession, now: datetime
) -> None:
    _, finding = await rows.ready(db, now)
    row = await rows.fix(db, finding, now)
    other = await rows.tenant(db)

    async with rows.client(db, rows.person(other)) as client:
        read = await client.get(f"{API_PREFIX}/findings/{finding.id}/fixes/general")
        action = await client.post(f"{API_PREFIX}/findings/{finding.id}/fixes/{row.id}/check")

    assert (read.status_code, action.status_code) == (404, 404)
