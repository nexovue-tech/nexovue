"""F4 plan T8.1: chat_load's summary of its own log, and AC80's comparison of a chat-loaded
fault-bed run against a chat-idle one. Pure: run logs built by hand, no stack."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chat_load import (
    FINDING_COLUMNS,
    LATE_LIMIT,
    NOTIFICATION_COLUMNS,
    compare,
    memory_event,
    poll_event,
    question_event,
    read_log,
    render_comparison,
    render_summary,
    summarise,
)
from fault_model import (
    REFERENCE,
    BurnSeen,
    FindingSeen,
    Injection,
    MessageSeen,
    Plan,
    RecordedRun,
    append,
)

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
INJECTION_START = T0 + REFERENCE + timedelta(hours=1)


def _uuid7(at: datetime) -> str:
    """A UUIDv7 whose time is `at`, as `findings.id` is (fault_model.opened_at)."""
    millis = int(at.timestamp() * 1000)
    return str(uuid.UUID(int=(millis << 80) | (7 << 76) | (2 << 62) | 1))


def _finding(
    at: datetime, *, severity: str = "warning", services: tuple[str, ...] = ("payment",)
) -> FindingSeen:
    return FindingSeen(
        id=_uuid7(at),
        opened_at=at,
        scope="service",
        services=services,
        signals=("failing",),
        severity=severity,
        closed_reason=None,
    )


def _burn(at: datetime, *, state: str = "delivered") -> BurnSeen:
    return BurnSeen(
        id=str(uuid.uuid4()),
        opened_at=at,
        resolved_at=None,
        severity="page",
        label="payment availability",
        findings=(),
        messages=(MessageSeen("opened", state, "email", None, at),),
    )


def _run(findings: tuple[FindingSeen, ...], burns: tuple[BurnSeen, ...] = ()) -> RecordedRun:
    injection = Injection(
        "paymentFailure", 1, INJECTION_START, INJECTION_START + timedelta(minutes=30)
    )
    plan = Plan("project", "3.0.0", T0, {}, (injection,))
    return RecordedRun(plan, (), (), findings, None, burns)


def test_a_chat_run_within_a_minute_of_the_idle_one_holds() -> None:
    idle = _run((_finding(INJECTION_START + timedelta(minutes=4)),))
    chat = _run((_finding(INJECTION_START + timedelta(minutes=4, seconds=50)),))
    rows = compare(idle, chat)
    assert [(row.fault, row.round) for row in rows] == [("paymentFailure", 1)]
    assert rows[0].finding_lateness == timedelta(seconds=50)
    assert rows[0].holds


def test_a_finding_more_than_a_minute_later_under_chat_fails() -> None:
    idle = _run((_finding(INJECTION_START + timedelta(minutes=4)),))
    chat = _run((_finding(INJECTION_START + timedelta(minutes=5, seconds=1)),))
    (row,) = compare(idle, chat)
    assert row.finding_lateness == LATE_LIMIT + timedelta(seconds=1)
    assert not row.holds


def test_a_finding_that_differs_on_a_listed_column_fails_even_on_time() -> None:
    assert FINDING_COLUMNS == ("scope", "services", "signals", "severity")
    idle = _run((_finding(INJECTION_START + timedelta(minutes=4)),))
    chat = _run((_finding(INJECTION_START + timedelta(minutes=4), severity="critical"),))
    (row,) = compare(idle, chat)
    assert row.finding_lateness == timedelta(0)
    assert not row.findings_match and not row.holds


def test_a_finding_missed_under_chat_fails() -> None:
    idle = _run((_finding(INJECTION_START + timedelta(minutes=4)),))
    (row,) = compare(idle, _run(()))
    assert row.finding_lateness is None and not row.holds


def test_a_burn_is_judged_on_its_lateness_and_its_notifications() -> None:
    assert NOTIFICATION_COLUMNS == ("kind", "state", "channel_kind", "reason")
    found = (_finding(INJECTION_START + timedelta(minutes=4)),)
    idle = _run(found, (_burn(INJECTION_START + timedelta(minutes=6)),))
    late = _run(found, (_burn(INJECTION_START + timedelta(minutes=7, seconds=30)),))
    held = _run(found, (_burn(INJECTION_START + timedelta(minutes=6), state="held"),))
    (slow,) = compare(idle, late)
    assert slow.burn_lateness == timedelta(seconds=90) and not slow.holds
    (different,) = compare(idle, held)
    assert different.burn_lateness == timedelta(0)
    assert not different.notifications_match and not different.holds


def test_an_injection_in_only_one_run_is_not_judged() -> None:
    idle = _run((_finding(INJECTION_START + timedelta(minutes=4)),))
    other = Plan(
        "project",
        "3.0.0",
        T0,
        {},
        (Injection("cartFailure", 1, INJECTION_START, INJECTION_START + timedelta(minutes=30)),),
    )
    chat = RecordedRun(other, (), (), (), None, ())
    assert compare(idle, chat) == []
    assert "No injection is in both runs" in render_comparison([])


def test_the_comparison_table_says_which_injections_failed() -> None:
    idle = _run((_finding(INJECTION_START + timedelta(minutes=4)),))
    chat = _run((_finding(INJECTION_START + timedelta(minutes=6)),))
    text = render_comparison(compare(idle, chat))
    assert "paymentFailure" in text and "+120 s" in text
    assert "AC80's alert half: fails" in text


def test_the_summary_reads_questions_polls_and_peak_memory_from_the_log() -> None:
    at = T0
    log = [
        question_event(at, "q1", "abstained", steps=8, failed_steps=0, seconds=40.0),
        question_event(at, "q2", "failed", steps=3, failed_steps=1, seconds=20.0),
        poll_event(at, "message", milliseconds=12.0, size=9_000, messages=1),
        poll_event(at, "message", milliseconds=30.0, size=40_000, messages=20),
        poll_event(at, "conversation", milliseconds=80.0, size=300_000, messages=20),
        memory_event(at, "insight", rss=300 * 2**20, pss=200 * 2**20, processes=9),
        memory_event(at, "insight", rss=420 * 2**20, pss=260 * 2**20, processes=9),
    ]
    summary = summarise(log)
    assert summary.questions == {"abstained": 1, "failed": 1}
    assert summary.steps == 11 and summary.failed_steps == 1
    assert summary.peak_pss_bytes == {"insight": 260 * 2**20}
    assert summary.peak_rss_bytes == {"insight": 420 * 2**20}
    assert summary.longest_conversation == 20
    conversation = summary.polls["conversation"]
    assert conversation.count == 1 and conversation.largest_bytes == 300_000
    text = render_summary(summary)
    assert "260 MiB" in text and "conversation" in text


def test_a_run_log_written_to_a_file_reads_back(tmp_path: Path) -> None:
    """`run` appends its own events; the summary reads them from the file, not as a fault-bed
    run log (whose reader refuses any event it does not know)."""
    path = tmp_path / "load.jsonl"
    for event in (
        question_event(T0, "q1", "abstained", steps=8, failed_steps=0, seconds=40.0),
        poll_event(T0, "message", milliseconds=12.0, size=9_000, messages=1),
        memory_event(T0, "insight", rss=2**20, pss=2**20, processes=9),
    ):
        append(path, event)
    assert summarise(read_log(path)).questions == {"abstained": 1}
