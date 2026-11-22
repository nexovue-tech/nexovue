"""The chat bed's seed and answer file, with no store (F4 plan T4.1; AC23, AC76).

`test_chat_seed_live.py` checks every answer against the real API; this file holds what can
be held offline: the committed answer file is the seed's, the question set covers what the
plan names, a drifting window reads the same rows, and zones move the range.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from chat import answers, seed
from chat.answers import (
    ANSWER_FILE,
    CANONICAL_ANCHOR,
    MINIMUM_QUESTIONS,
    ROLLUP_TOOLS,
    Pick,
    answer_file,
    picked,
    questions,
)

REQUIRED_CATEGORIES = {
    "how_many",
    "not_enough_evidence",
    "zones",
    "no_telemetry",
    "still_learning",
    "stopped_reporting",
    "slow_endpoint",
    "disk",
    "custom_metric",
    "third_party_host",
    "across_tenants",
}


def test_the_committed_answer_file_is_the_seeds_own() -> None:
    assert ANSWER_FILE.read_text(encoding="utf-8") == answer_file(), (
        "answers.json is stale: run `just chat-bed answers`"
    )


def test_there_are_enough_questions_and_every_kind_the_plan_names() -> None:
    made = questions(CANONICAL_ANCHOR)
    assert len(made) >= MINIMUM_QUESTIONS
    assert len({each.id for each in made}) == len(made)
    assert {each.category for each in made} >= REQUIRED_CATEGORIES


def test_a_how_many_answer_may_come_only_from_a_rollup_tool() -> None:
    counted = [each for each in questions(CANONICAL_ANCHOR) if each.kind == "number"]
    assert counted
    for each in counted:
        assert "list_traces" not in each.tools, each.id
        if each.unit == "count" and each.category.startswith("how_many"):
            assert set(each.tools) <= set(ROLLUP_TOOLS), each.id


def test_every_answerable_question_names_its_tool_its_pick_and_an_answer() -> None:
    for each in questions(CANONICAL_ANCHOR):
        if each.kind in ("abstain",):
            assert each.tool is None and not each.expected, each.id
            continue
        assert each.tool in each.tools, each.id
        assert each.pick is not None and each.expected, each.id


def test_a_window_drifting_less_than_the_slack_reads_the_same_rows() -> None:
    anchor = CANONICAL_ANCHOR
    for minutes in seed.RELATIVE_WINDOWS:
        wanted = seed.sums(
            anchor,
            service=seed.CHECKOUT,
            start=anchor - timedelta(minutes=minutes),
            end=anchor,
        )
        for drift in range(seed.SLACK):
            moved = anchor + timedelta(minutes=drift, seconds=30)
            assert (
                seed.sums(
                    anchor,
                    service=seed.CHECKOUT,
                    start=moved - timedelta(minutes=minutes),
                    end=moved,
                )
                == wanted
            ), (minutes, drift)


def test_the_persons_yesterday_at_14_is_five_and_a_half_hours_before_the_projects() -> None:
    by_id = {each.id: each for each in questions(CANONICAL_ANCHOR)}
    theirs, projects = by_id["zone-14"].window, by_id["zone-same-14"].window
    assert theirs is not None and projects is not None
    assert projects[0] - theirs[0] == timedelta(hours=5, minutes=30)
    assert by_id["zone-14"].zone == seed.PERSON_ZONE
    assert by_id["zone-14"].expected != by_id["zone-same-14"].expected


def test_answers_follow_the_anchor() -> None:
    later = CANONICAL_ANCHOR + timedelta(hours=7)
    first = {each.id: each.expected for each in questions(CANONICAL_ANCHOR)}
    second = {each.id: each.expected for each in questions(later)}
    assert first["requests-hour-checkout"] != second["requests-hour-checkout"]
    assert first["errors-hour-payment"] == second["errors-hour-payment"]


def test_inventory_stopped_an_hour_ago_and_search_only_started() -> None:
    anchor = datetime(2026, 11, 22, 3, 0, tzinfo=UTC)
    hour = (anchor - timedelta(hours=1), anchor)
    assert seed.sums(anchor, service=seed.INVENTORY, start=hour[0], end=hour[1]) == (0, 0)
    assert seed.sums(
        anchor, service=seed.INVENTORY, start=anchor - timedelta(hours=3), end=hour[0]
    )[0]
    day = anchor - timedelta(days=1)
    assert seed.sums(anchor, service=seed.SEARCH, start=day, end=hour[0]) == (0, 0)


def test_the_full_disk_is_97_percent_and_search_is_learning() -> None:
    assert seed.filesystem_utilization(seed.FULL_HOST) == 0.97
    signals = seed.detection_signals(seed.SEARCH, CANONICAL_ANCHOR)
    assert {each["verdict"] for each in signals.values()} == {"learning"}


def test_a_pick_reads_one_matching_row_the_top_row_or_says_none() -> None:
    rows = [
        {"service_name": "a", "request_count": 3, "p95_ms": 10.0},
        {"service_name": "b", "request_count": 5, "p95_ms": 900.0},
    ]
    assert picked(Pick("field", field="request_count", where={"service_name": "b"}), rows) == "5"
    assert picked(Pick("top", field="p95_ms", name="service_name"), rows) == "b"
    assert picked(Pick("none", where={"service_name": "c"}, word="none"), rows) == "none"
    assert picked(Pick("none", where={"service_name": "a"}, word="none"), rows) is None
    assert picked(Pick("first", field="service_name"), rows) == "a"
    assert picked(Pick("field", field="request_count", where={"service_name": "c"}), rows) is None


def test_a_figure_is_written_as_an_answer_states_it() -> None:
    assert answers.figure(1620.0) == "1620"
    assert answers.figure(0.97) == "0.97"
    assert answers.figure(0.30000000000000004) == "0.3"
