"""Stated-cause scoring, the answer keys, rounds, stored facts and replay (F3 plan T2.15; AC61,
AC62, and AC97's bed metric). Pure: no stack, no model."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from answer_keys import KEYS, REACHES, Key, key_for, staged_faults
from bed_builders import START, decision, deploy, facts, moved_edge, staged
from bed_facts import decode, facts_from_json, facts_to_json
from facts_log import append_decision, read, start
from fault_model import FAULTS
from nexovue_platform.evidence import RULE_SIGNED_DEPLOY
from replay import TUNABLE, constants, replay, sweep
from score import (
    BAR_MIN_STATED,
    DEFAULT_ROUNDS,
    Planted,
    RoundPlan,
    band_of,
    bar_reading,
    by_fault,
    by_reach,
    by_rule,
    cause_links_to_data,
    in_rounds,
    render_tables,
    rule_readings,
    score_all,
    score_decision,
    time_to_first_explanation,
)

from nexovue_scheduler.evidence import rules
from nexovue_scheduler.evidence.rules import Cause, dependency_evidence_id

# ---------------------------------------------------------------- the answer keys


def test_every_fault_the_bed_injects_has_a_written_key() -> None:
    assert {fault.name for fault in FAULTS} <= {key.fault for key in KEYS}


def test_every_key_is_on_the_service_the_fault_is_expected_to_move_or_a_caller_of_it() -> None:
    expected = {fault.name: fault.expected_service for fault in FAULTS}
    own = [key for key in KEYS if key.truth != "dependency"]

    assert all(key.service == expected[key.fault] for key in own)


def test_a_key_that_names_a_callee_must_be_a_dependency_and_a_fault_no_rule_sees_has_no_cause() -> (
    None
):
    with pytest.raises(ValueError, match="callee"):
        Key("f", "s", "deploy", "deploy", callee="x")
    with pytest.raises(ValueError, match=r"no rule can see|rules could name"):
        Key("f", "s", "deploy", "invisible")


def test_the_faults_group_by_whether_the_rules_could_see_the_cause() -> None:
    assert {key.reach for key in KEYS} == set(REACHES)
    assert all(key.truth == "deploy" for key in staged_faults())
    cpu = key_for("adHighCpu", "ad")
    assert cpu is not None and cpu.reach == "invisible"


# ---------------------------------------------------------------- scoring


def test_the_staged_deploy_named_as_the_cause_is_right() -> None:
    built, planted = staged("adFailure", "ad", 3)
    one = decision("adFailure", "ad", 3, built=built, planted=planted)

    [scored] = score_all([one])

    assert scored.cause is not None and scored.cause.rule == RULE_SIGNED_DEPLOY
    assert scored.outcome == "right"


def test_a_deploy_unrelated_to_the_fault_named_as_the_cause_is_wrong_and_marked_a_decoy() -> None:
    version = "bed-decoy-adHighCpu-r2"
    built = facts("ad", deploys=(deploy("ad", version),))
    planted = (Planted("ad", version, START - timedelta(minutes=4), "decoy"),)

    [scored] = score_all([decision("adHighCpu", "ad", 2, built=built, planted=planted)])

    assert scored.outcome == "wrong"
    assert scored.on_decoy
    assert "| signed_deploy | 1 | 0 | 1 | 0 | 0 | 0 | 1 |" in render_tables([scored])


def test_a_deploy_that_is_not_the_staged_one_named_on_a_deploy_fault_is_wrong() -> None:
    built, planted = staged("adFailure", "ad", 3)
    other = facts("ad", deploys=(deploy("ad", "some-other-version"),))

    [scored] = score_all([decision("adFailure", "ad", 3, built=other, planted=planted)])

    assert scored.cause is not None and scored.outcome == "wrong"
    assert built.deploys[0].version != "some-other-version"


def test_a_dependency_cause_with_no_edge_in_the_facts_is_wrong_for_having_no_data_behind_it() -> (
    None
):
    bare = facts("checkout")
    ghost = Cause(dependency_evidence_id("payment"), "dependency_edge", "possible", "telemetry")

    scored = score_decision(decision("paymentUnreachable", "checkout", 3, built=bare), ghost)

    assert scored.unlinked and scored.outcome == "wrong"


def test_a_finding_with_no_cause_stated_is_an_honest_miss_whatever_the_group() -> None:
    scored = score_all([decision("adFailure", "ad", 3), decision("adHighCpu", "ad", 3)])

    assert [each.outcome for each in scored] == ["honest_miss", "honest_miss"]
    reach = by_reach(scored)
    assert reach["deploy"].honest_miss == 1 and reach["invisible"].honest_miss == 1


def test_a_fault_with_no_finding_is_not_caught_and_one_with_no_key_is_unkeyed() -> None:
    scored = score_all(
        [
            decision("adFailure", "ad", 1, opened=False),
            decision("adFailure", "somewhere-else", 1),
        ]
    )

    assert [each.outcome for each in scored] == ["not_caught", "unkeyed"]


def test_a_cause_that_links_to_no_data_is_wrong_even_if_it_looks_like_the_answer() -> None:
    built, planted = staged("adFailure", "ad", 3)
    one = decision("adFailure", "ad", 3, built=built, planted=planted)
    unlinked = Cause("deploy:nothing-here", RULE_SIGNED_DEPLOY, "likely", "verified")

    scored = score_decision(one, unlinked)

    assert not cause_links_to_data(unlinked, built)
    assert scored.outcome == "wrong" and scored.unlinked


def test_a_dependency_named_by_its_edge_is_right_for_the_caller_and_another_callee_is_wrong() -> (
    None
):
    right = facts("checkout", edges=(moved_edge("payment"),))
    wrong = facts("checkout", edges=(moved_edge("cart"),))

    scored = score_all(
        [
            decision("paymentUnreachable", "checkout", 3, built=right),
            decision("paymentUnreachable", "checkout", 4, built=wrong),
        ]
    )

    assert [each.outcome for each in scored] == ["right", "wrong"]


def test_the_callees_staged_bad_release_is_the_true_cause_one_hop_away() -> None:
    version = "bed-paymentFailure-r3"
    built = facts("checkout", deploys=(deploy("payment", version),), edges=(moved_edge("payment"),))
    planted = (Planted("payment", version, START - timedelta(minutes=4), "cause"),)

    [scored] = score_all([decision("paymentFailure", "checkout", 3, built=built, planted=planted)])

    assert scored.cause is not None and scored.cause.rule == "dependency_deploy"
    assert scored.outcome == "right"


def test_a_cause_stated_where_no_rule_can_see_the_truth_is_wrong() -> None:
    built = facts("ad", deploys=(deploy("ad", "some-version"),))

    [scored] = score_all([decision("adManualGc", "ad", 3, built=built)])

    assert scored.outcome == "wrong"


def test_two_rules_naming_different_things_is_a_tie() -> None:
    built = facts("checkout", deploys=(deploy("checkout", "v9"),), edges=(moved_edge("payment"),))

    [scored] = score_all([decision("paymentUnreachable", "checkout", 3, built=built)])

    assert scored.tie


@pytest.mark.parametrize(
    ("deploys_in_week", "band"),
    [(0, "low"), (6, "low"), (7, "daily"), (14, "daily"), (15, "frequent")],
)
def test_a_service_is_banded_by_its_deploys_in_the_week_before(
    deploys_in_week: int, band: str
) -> None:
    assert band_of(facts("ad", deploys_in_week=deploys_in_week)) == band


def test_a_service_with_no_deploy_records_has_its_own_band() -> None:
    bare = facts("ad")
    no_records = type(bare)(
        finding=bare.finding, now=bare.now, zone=bare.zone, histories={}, edges=bare.edges
    )

    assert band_of(no_records) == "no_records"


def test_counts_are_per_fault_per_rule_and_never_percentages() -> None:
    built, planted = staged("adFailure", "ad", 3)
    scored = score_all(
        [
            decision("adFailure", "ad", 3, built=built, planted=planted),
            decision("adFailure", "ad", 4),
            decision("adHighCpu", "ad", 3, opened=False),
        ]
    )

    assert by_fault(scored)["adFailure"].findings == 2
    assert by_rule(scored)[RULE_SIGNED_DEPLOY].right == 1
    assert "%" not in render_tables(scored)
    assert "| adFailure | 2 | 1 | 0 | 1 | 0 | 0 | 0 |" in render_tables(scored)


# ---------------------------------------------------------------- the bar, in counts


@pytest.mark.parametrize(
    ("stated", "wrong", "reading"),
    [
        (BAR_MIN_STATED - 1, 0, "silent"),
        (4, 4, "silent"),
        (5, 0, "met"),
        (9, 0, "met"),
        (9, 1, "missed"),
        (10, 1, "met"),
        (19, 1, "met"),
        (19, 2, "missed"),
        (20, 2, "met"),
    ],
)
def test_the_approved_bar_in_counts(stated: int, wrong: int, reading: str) -> None:
    assert bar_reading(stated, wrong) == reading


def test_forged_false_causes_count_as_wrong_inside_the_rules_bar() -> None:
    built, planted = staged("adFailure", "ad", 3)
    scored = score_all(
        [decision("adFailure", "ad", r, built=built, planted=planted) for r in range(5)]
    )

    assert rule_readings(scored)[RULE_SIGNED_DEPLOY] == (5, 0, "met")
    assert rule_readings(scored, {RULE_SIGNED_DEPLOY: 1})[RULE_SIGNED_DEPLOY] == (5, 1, "missed")
    # A rule that only ever stated forged causes still appears, and is judged on them.
    assert rule_readings([], {"new_error": 5})["new_error"] == (5, 5, "missed")


# ---------------------------------------------------------------- rounds


def test_a_round_is_a_tune_round_or_a_report_round_never_both_and_is_named_first() -> None:
    with pytest.raises(ValueError, match="never both"):
        RoundPlan(frozenset({1}), frozenset({1, 2}))
    with pytest.raises(ValueError, match="at least one"):
        RoundPlan(frozenset(), frozenset({1}))
    with pytest.raises(ValueError, match="neither list"):
        DEFAULT_ROUNDS.kind_of(9)


def test_tuning_never_sees_a_report_round() -> None:
    decisions = [decision("adFailure", "ad", number) for number in range(1, 6)]

    assert {each.round for each in in_rounds(decisions, DEFAULT_ROUNDS, "tune")} == {1, 2}
    assert {each.round for each in in_rounds(decisions, DEFAULT_ROUNDS, "report")} == {3, 4, 5}


# ---------------------------------------------------------------- time to first explanation


def test_time_to_first_explanation_is_p50_and_p95_per_fault_with_the_unexplained_counted() -> None:
    base = datetime(2026, 11, 20, tzinfo=UTC)
    samples: list[tuple[str, datetime, datetime | None]] = [
        ("a", base, base + timedelta(seconds=second)) for second in range(1, 21)
    ]
    samples.append(("a", base, None))
    samples.append(("b", base, None))

    result = time_to_first_explanation(samples)

    assert result["a"].count == 20 and result["a"].missing == 1
    assert result["a"].p50 == timedelta(seconds=10) and result["a"].p95 == timedelta(seconds=19)
    assert result["b"].count == 0 and result["b"].missing == 1 and result["b"].p50 is None


# ---------------------------------------------------------------- stored facts


def test_stored_facts_come_back_equal_and_replay_to_the_same_cause() -> None:
    built, _ = staged("adFailure", "ad", 3)
    built = facts(
        "ad",
        deploys=built.deploys,
        edges=(moved_edge("payment"),),
    )

    stored = json.loads(json.dumps(facts_to_json(built)))

    assert facts_from_json(stored) == built
    assert rules.decide(facts_from_json(stored)) == rules.decide(built)


def test_a_stored_field_the_type_does_not_know_is_an_error_not_a_silent_drop() -> None:
    stored = facts_to_json(facts("ad"))
    stored["finding"]["surprise"] = 1

    with pytest.raises(ValueError, match="unknown fields"):
        facts_from_json(stored)


def test_a_time_without_a_zone_is_refused_both_ways() -> None:
    with pytest.raises(ValueError, match="zone"):
        decode(datetime, "2026-11-20T12:00:00")


def test_the_log_names_its_rounds_first_and_refuses_anything_that_gets_around_that(
    tmp_path: Path,
) -> None:
    path = tmp_path / "facts.jsonl"
    start(path, DEFAULT_ROUNDS)
    with pytest.raises(ValueError, match="rounds are named once"):
        start(path, DEFAULT_ROUNDS)
    built, planted = staged("adFailure", "ad", 3)
    append_decision(path, decision("adFailure", "ad", 3, built=built, planted=planted))
    append_decision(path, decision("adFailure", "ad", 4, opened=False))

    plan, decisions = read(path)

    assert plan == DEFAULT_ROUNDS and len(decisions) == 2
    assert decisions[0].facts == built and decisions[0].planted == planted

    append_decision(path, decision("adFailure", "ad", 9))
    with pytest.raises(ValueError, match="neither list"):
        read(path)


def test_a_decision_before_the_rounds_are_named_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "facts.jsonl"
    append_decision(path, decision("adFailure", "ad", 3))

    with pytest.raises(ValueError, match="before the rounds"):
        read(path)


# ---------------------------------------------------------------- replay


def test_a_replayed_constant_changes_what_is_stated_and_is_put_back() -> None:
    built = facts("checkout", edges=(moved_edge("payment"),))
    decisions = [decision("paymentUnreachable", "checkout", 3, built=built)]
    before = rules.EDGE_FAILURE_RISE

    assert "dependency_edge" in replay(decisions)
    assert "dependency_edge" not in replay(decisions, {"EDGE_FAILURE_RISE": 1000.0})
    assert before == rules.EDGE_FAILURE_RISE


def test_a_constant_is_put_back_even_when_the_replay_fails() -> None:
    before = rules.TRAFFIC_RISE
    with pytest.raises(RuntimeError), constants({"TRAFFIC_RISE": 9.0}):
        raise RuntimeError("boom")

    assert before == rules.TRAFFIC_RISE


def test_only_the_named_constants_can_be_replayed_and_with_the_right_type() -> None:
    with pytest.raises(ValueError, match="not a tunable"), constants({"DEPLOY_LOOKBACK": 1}):
        pass
    with pytest.raises(TypeError), constants({"ERROR_LEAD_IN": 5}):
        pass
    assert all(hasattr(rules, name) for name in TUNABLE)


def test_a_sweep_reads_the_tune_rounds_only() -> None:
    built = facts("checkout", edges=(moved_edge("payment"),))
    decisions = [
        decision("paymentUnreachable", "checkout", number, built=built) for number in range(1, 6)
    ]

    result = sweep(decisions, DEFAULT_ROUNDS, "EDGE_FAILURE_RISE", [2.0, 1000.0])

    assert result[2.0]["dependency_edge"].findings == 2  # rounds 1 and 2 only
    assert "dependency_edge" not in result[1000.0]
