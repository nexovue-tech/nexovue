"""F3's rules and sentences, pure and table-driven (plan T1.5; spec FR8 to FR12, FR44,
FR49; AC6 to AC11, AC13, AC15, AC16's table, AC74's rule cases, AC98).

No store: every case builds the `Facts` a gather would have read. Times are on a fixed
Wednesday at 14:00 in Kolkata, so the base rate's comparable windows are known: the
week before holds five weekdays, so 5 x 6 = 30 windows with a full week covered (FR9).
"""

from __future__ import annotations

import dataclasses
import itertools
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from nexovue_platform.evidence import LIKELY, POSSIBLE, TELEMETRY_TEXT, VERIFIED

from nexovue_scheduler.evidence import rules, sentences
from nexovue_scheduler.evidence.rules import (
    BaseRate,
    CheckChange,
    DeployFact,
    DeployHistory,
    EdgeFact,
    ErrorGroup,
    Facts,
    FindingInput,
    HostReading,
    LinkedIncident,
    OtherFinding,
    Release,
    Traffic,
)

ZONE = ZoneInfo("Asia/Kolkata")
# Wednesday 2026-11-04, 14:00 in Kolkata.
START = datetime(2026, 11, 4, 14, 0, tzinfo=ZONE).astimezone(UTC)
NOW = START + timedelta(minutes=6)
SERVICE = "checkout"
CALLEE = "payments"


def finding(**changes: Any) -> FindingInput:
    values: dict[str, Any] = {
        "id": "f-1",
        "scope": "service",
        "service": SERVICE,
        "started_at": START,
        "signals": ("slower",),
    }
    return FindingInput(**{**values, **changes})


def facts(**changes: Any) -> Facts:
    values: dict[str, Any] = {
        "finding": finding(),
        "now": NOW,
        "zone": ZONE,
        "coverage": "deploys_and_versions",
        "histories": {SERVICE: quiet_history(), CALLEE: quiet_history(CALLEE)},
    }
    return Facts(**{**values, **changes})


def quiet_history(service: str = SERVICE, days: int = 30) -> DeployHistory:
    """A deploy at 03:00 each day: nothing in the 13:00 to 15:59 windows."""
    times = tuple(
        datetime(2026, 11, 4, 3, 0, tzinfo=ZONE).astimezone(UTC) - timedelta(days=back)
        for back in range(1, 8)
    )
    return DeployHistory(service, times, START - timedelta(days=days))


def history_with_windows(held: int, service: str = SERVICE) -> DeployHistory:
    """A full week of records with a deploy in `held` of the 30 comparable windows."""
    windows = rules._comparable_windows(START, ZONE, covered_from=START - timedelta(days=30))
    times = tuple(window + timedelta(minutes=5) for window in windows[:held])
    return DeployHistory(service, times, START - timedelta(days=30))


def signed(minutes_before: float, service: str = SERVICE, deploy_id: str = "d-1") -> DeployFact:
    return DeployFact(deploy_id, service, "v2.1", START - timedelta(minutes=minutes_before), True)


def seen(minutes_before: float) -> DeployFact:
    return DeployFact("p-9", SERVICE, "v2.2", START - timedelta(minutes=minutes_before), False)


def moved_edge(callee: str = CALLEE, **changes: Any) -> EdgeFact:
    values: dict[str, Any] = {
        "callee": callee,
        "calls_before": 600,
        "calls_during": 600,
        "failures_before": 2,
        "failures_during": 36,
        "p95_before": 40.0,
        "p95_during": 42.0,
    }
    return EdgeFact(**{**values, **changes})


def steady_edge(callee: str = CALLEE) -> EdgeFact:
    return moved_edge(callee, failures_during=3, p95_during=41.0)


def dependency_finding(minutes_before: float, callee: str = CALLEE) -> OtherFinding:
    return OtherFinding("f-2", callee, START - timedelta(minutes=minutes_before))


def new_group(minutes_before: float = 1) -> ErrorGroup:
    return ErrorGroup(
        "g-1", "TimeoutError", (SERVICE,), 12, START - timedelta(minutes=minutes_before)
    )


# ------------------------------------------------------------------ deploys


def test_a_recent_signed_deploy_at_an_unusual_hour_is_the_likely_place_to_look() -> None:
    """AC7."""
    found = facts(deploys=(signed(4),))

    cause = rules.decide(found)

    assert cause is not None
    assert (cause.rule, cause.strength, cause.basis) == ("signed_deploy", LIKELY, VERIFIED)
    record = sentences.record(found, cause)
    header = sentences.all_words(record.summary["header"])
    assert header.startswith("Likely: the deploy of ")
    assert "4 minutes before this" in header and "7 days" in header
    assert not any(word in header.lower() for word in sentences.BANNED_WORDS)


@pytest.mark.parametrize(
    ("held", "chance", "strength"),
    [(2, 2 / 30, LIKELY), (3, 3 / 30, POSSIBLE), (14, 14 / 30, POSSIBLE), (15, 15 / 30, None)],
)
def test_the_base_rate_bands_on_real_windows(
    held: int, chance: float, strength: str | None
) -> None:
    """FR9 on the window count: 30 comparable windows in this week."""
    rate = rules.base_rate(history_with_windows(held), START, ZONE)

    assert rate.chance == pytest.approx(chance)
    assert rules.deploy_strength(rate) == strength


@pytest.mark.parametrize(
    ("chance", "strength"),
    [(0.099, LIKELY), (0.10, POSSIBLE), (0.499, POSSIBLE), (0.50, None)],
)
def test_the_strength_boundaries_are_fixed(chance: float, strength: str | None) -> None:
    """AC8's boundaries: 9.9%, 10%, 49.9% and 50%."""
    assert rules.deploy_strength(BaseRate(3, 30, chance)) == strength


def test_a_frequent_deployer_s_deploy_stays_a_fact_with_its_base_rate() -> None:
    """AC8: 75 deploys a week in working hours, a finding at 14:00 on a weekday."""
    working_hours = tuple(
        datetime(2026, 11, 4, 9, 0, tzinfo=ZONE).astimezone(UTC)
        - timedelta(days=day)
        + timedelta(minutes=30 * n)
        for day in (1, 2, 5, 6)
        for n in range(18)
    ) + tuple(datetime(2026, 11, 4, hour, 0, tzinfo=ZONE).astimezone(UTC) for hour in (9, 10, 11))
    history = DeployHistory(SERVICE, working_hours, START - timedelta(days=60))
    found = facts(deploys=(signed(4),), histories={SERVICE: history})

    assert rules.decide(found) is None
    deploy = next(i for i in sentences.record(found, None).items if i["kind"] == "deploy")
    assert deploy["values"]["deploys_last_7_days"] == 75
    assert "75 deploys in 7 days" in sentences.all_words(deploy["sentence"])


def test_two_days_of_records_say_so_and_reach_possible_at_most() -> None:
    """AC98, FR7."""
    history = DeployHistory(
        SERVICE, (START - timedelta(days=1, hours=3),), START - timedelta(days=2)
    )
    found = facts(deploys=(signed(4),), histories={SERVICE: history})

    cause = rules.decide(found)

    assert cause is not None and cause.strength == POSSIBLE
    words = sentences.all_words(sentences.record(found, cause).summary["header"])
    assert words.startswith("Possibly: ")
    assert "1 deploy in the 2 days nexovue has records for, too few to judge" in words


def test_an_old_deploy_is_never_a_cause_by_age_alone() -> None:
    """FR49, AC91: 2 h 10 min earlier, a fact only."""
    last = Release("r-1", "p-1", SERVICE, "v2.1", START - timedelta(hours=2, minutes=10), True)
    found = facts(deploys=(signed(130),), last_release=last)

    assert rules.decide(found) is None
    line = next(i for i in sentences.record(found, None).items if i.get("role") == "last_before")
    assert sentences.all_words(line["sentence"]) == "Last deploy: , 2 h 10 min before this."
    assert line["sentence"][1] == {"name": "v2.1"}


def test_no_deploy_information_is_not_the_same_as_no_recent_deploy() -> None:
    """AC12: a service that never sent one has an absence; one that sends, and has none
    recently, has none."""
    never = sentences.record(facts(coverage="versions_only", histories={}), None)
    sends = sentences.record(facts(coverage="deploys_and_versions"), None)

    reasons = [i["values"]["reason"] for i in never.items if i["kind"] == "absence"]
    assert "no_deploy_information" in reasons
    assert "No deploy information for this service." in [
        sentences.all_words(i["sentence"]) for i in never.items
    ]
    assert "no_deploy_information" not in [
        i["values"].get("reason") for i in sends.items if i["kind"] == "absence"
    ]


# ------------------------------------------------------------------ seen versions


def test_a_version_only_seen_in_telemetry_never_names_a_cause_alone() -> None:
    """AC8, A75: alone, and with only a new error group, nothing is named."""
    alone = facts(deploys=(seen(4),))
    with_error = facts(deploys=(seen(4),), error_groups=(new_group(),))

    assert rules.decide(alone) is None
    assert rules.decide(with_error) is None
    item = next(i for i in sentences.record(alone, None).items if i["kind"] == "deploy")
    assert item["basis"] == TELEMETRY_TEXT


def test_a_seen_version_supports_a_cause_another_rule_names() -> None:
    found = facts(
        deploys=(seen(4),), edges=(moved_edge(),), dependency_findings=(dependency_finding(3),)
    )

    cause = rules.decide(found)

    assert cause is not None and cause.rule == "dependency_finding"
    assert rules.deploy_evidence_id(seen(4)) in cause.supported_by


# ------------------------------------------------------------------ dependencies


def test_a_dependency_with_its_own_earlier_finding_and_a_moved_edge_is_likely() -> None:
    """AC6, first half."""
    found = facts(edges=(moved_edge(),), dependency_findings=(dependency_finding(3),))

    cause = rules.decide(found)

    assert cause is not None
    assert (cause.rule, cause.strength, cause.basis) == ("dependency_finding", LIKELY, "telemetry")
    header = sentences.all_words(sentences.record(found, cause).summary["header"])
    assert header.startswith("Likely, from telemetry only: calls to ")
    assert "opened its own finding 3 minutes earlier" in header


def test_a_signed_deploy_outranks_the_dependency_which_supports_it() -> None:
    """AC6: with a qualifying deploy too, the deploy is named first."""
    found = facts(
        deploys=(signed(4),), edges=(moved_edge(),), dependency_findings=(dependency_finding(3),)
    )

    cause = rules.decide(found)

    assert cause is not None and cause.rule == "signed_deploy"
    assert rules.dependency_evidence_id(CALLEE) in cause.supported_by


def test_a_same_minute_dependency_is_possible_and_needs_its_edge() -> None:
    """AC6, A73: a tie passes only through rule 6's same_minute variant."""
    tie = dependency_finding(0)
    moved = rules.decide(facts(edges=(moved_edge(),), dependency_findings=(tie,)))
    still = rules.decide(facts(edges=(steady_edge(),), dependency_findings=(tie,)))

    assert moved is not None
    assert (moved.rule, moved.strength, moved.same_minute) == ("dependency_edge", POSSIBLE, True)
    assert still is None


def test_a_moved_edge_with_no_finding_of_its_own_is_possible() -> None:
    """Rule 6, AC74."""
    cause = rules.decide(facts(edges=(moved_edge(),)))

    assert cause is not None
    assert (cause.rule, cause.strength, cause.same_minute) == ("dependency_edge", POSSIBLE, False)


def test_a_browser_door_edge_caps_the_cause_at_possible() -> None:
    """FR12, AC14's rule case: no server door in the project."""
    found = facts(
        edges=(moved_edge(from_browser=True),), dependency_findings=(dependency_finding(3),)
    )

    cause = rules.decide(found)

    assert cause is not None and cause.strength == POSSIBLE


def test_a_dependency_s_signed_deploy_is_possible() -> None:
    """Rule 4, AC74."""
    found = facts(deploys=(signed(10, service=CALLEE, deploy_id="d-9"),), edges=(steady_edge(),))

    cause = rules.decide(found)

    assert cause is not None
    assert (cause.rule, cause.strength, cause.basis) == ("dependency_deploy", POSSIBLE, VERIFIED)


def test_a_weak_signed_deploy_is_possible() -> None:
    """Rule 3, AC74: a chance of 10% to 50%."""
    found = facts(deploys=(signed(4),), histories={SERVICE: history_with_windows(6)})  # 20%

    cause = rules.decide(found)

    assert cause is not None and (cause.rule, cause.strength) == ("signed_deploy_weak", POSSIBLE)


def test_traffic_rising_before_a_slowdown_is_possible() -> None:
    """Rule 7, AC74."""
    rose = rules.decide(facts(traffic=Traffic(100.0, 180.0)))
    failing = rules.decide(
        facts(traffic=Traffic(100.0, 180.0), finding=finding(signals=("failing",)))
    )

    assert rose is not None and (rose.rule, rose.strength) == ("traffic_rose", POSSIBLE)
    assert failing is None


# ------------------------------------------------------------------ errors and the rest


@pytest.mark.parametrize(
    ("with_deploy_seen", "with_check", "with_incident", "with_host"),
    list(itertools.product((False, True), repeat=4)),
)
def test_a_new_error_group_alone_never_names_a_cause(
    with_deploy_seen: bool, with_check: bool, with_incident: bool, with_host: bool
) -> None:
    """AC9, every combination of the rules that may never name a cause."""
    found = facts(
        error_groups=(new_group(1),),
        deploys=(seen(4),) if with_deploy_seen else (),
        checks=(CheckChange("c-1", "Homepage", "down", START),) if with_check else (),
        incidents=(LinkedIncident("i-1", "Errors over 5%", START),) if with_incident else (),
        hosts=(HostReading("db-1", "disk", 0.95, START),) if with_host else (),
    )

    assert rules.decide(found) is None


def test_a_group_that_began_a_minute_before_counts_as_new() -> None:
    """AC9."""
    found = facts(error_groups=(new_group(1),), edges=(moved_edge(),))

    cause = rules.decide(found)

    assert cause is not None and rules.error_evidence_id(new_group(1)) in cause.supported_by


def test_a_check_s_state_change_is_a_fact_and_never_a_cause() -> None:
    """AC10, A17."""
    found = facts(checks=(CheckChange("c-1", "Homepage", "down", START),))

    assert rules.decide(found) is None
    check = next(i for i in sentences.record(found, None).items if i["kind"] == "check")
    assert check["basis"] == VERIFIED and "project-wide" in sentences.all_words(check["sentence"])


def test_a_high_host_reading_is_project_wide_and_never_a_cause() -> None:
    """AC100."""
    found = facts(hosts=(HostReading("db-1", "disk", 0.95, START),))
    nameless = facts(host_names_present=False)

    assert rules.decide(found) is None
    host = next(i for i in sentences.record(found, None).items if i["kind"] == "infrastructure")
    assert "whole project" in sentences.all_words(host["sentence"])
    reasons = [
        i["values"]["reason"]
        for i in sentences.record(nameless, None).items
        if i["kind"] == "absence"
    ]
    assert "no_host_names" in reasons


def test_nothing_passing_with_every_read_complete_names_nothing() -> None:
    """AC11, AC16(a): normal base rates, normal edges, routine deploys give no cause."""
    routine = facts(
        deploys=(signed(4),),
        histories={SERVICE: history_with_windows(20)},
        edges=(steady_edge(),),
        traffic=Traffic(100.0, 104.0),
    )

    assert rules.decide(routine) is None
    assert sentences.record(routine, None).summary == {"header": None, "card": None}


def test_a_failed_read_is_an_absence_and_never_a_zero() -> None:
    """AC64."""
    found = facts(failed_reads=frozenset({"errors"}))

    record = sentences.record(found, None)

    failed = [i for i in record.items if i["values"].get("reason") == "could_not_read"]
    assert [sentences.all_words(i["sentence"]) for i in failed] == [
        "Could not read recent errors this time."
    ]
    assert record.reads_failed == 1


def test_a_missing_value_stays_missing() -> None:
    """AC15: no p95 reads as no p95, never 0."""
    edge = moved_edge(p95_before=None, p95_during=None)
    item = next(
        i for i in sentences.record(facts(edges=(edge,)), None).items if i["kind"] == "dependency"
    )

    assert item["values"]["p95_before"] is None and "p95" not in sentences.all_words(
        item["sentence"]
    )


# ------------------------------------------------------------------ project findings


def project(**changes: Any) -> Facts:
    return facts(
        finding=finding(scope="project", service=None, services=("a", "b", "c", "d")), **changes
    )


def test_a_deploy_to_half_a_project_s_services_is_its_rule() -> None:
    """AC13, FR11's first rule."""
    found = project(deploys=(signed(5, "a", "d-a"), signed(6, "b", "d-b")))

    cause = rules.decide(found)

    assert cause is not None and cause.rule == "project_deploy"


def test_a_shared_dependency_with_its_own_finding_is_a_project_s_rule() -> None:
    """AC13, FR11's second rule."""
    found = project(
        project_edges=(("a", steady_edge("db")), ("b", steady_edge("db"))),
        dependency_findings=(OtherFinding("f-db", "db", START - timedelta(minutes=4)),),
    )

    cause = rules.decide(found)

    assert cause is not None and cause.rule == "project_shared_dependency"


def test_a_project_finding_with_no_rule_still_has_an_explanation() -> None:
    """AC13: an intake refusal is an install-wide fact, never a cause."""
    refused = START - timedelta(minutes=10)
    found = project(intake_refused_at=(refused,))

    assert rules.decide(found) is None
    words = [sentences.all_words(i["sentence"]) for i in sentences.record(found, None).items]
    assert "This install's intake refused data at , for every project on it." in words


# ------------------------------------------------------------------ order and bounds


def test_facts_are_ordered_as_fr44_says() -> None:
    """FR44: the cause, its support, then verified, telemetry, telemetry text, absences."""
    found = facts(
        deploys=(signed(4),),
        edges=(moved_edge(),),
        error_groups=(new_group(),),
        checks=(CheckChange("c-1", "Homepage", "down", START),),
        failed_reads=frozenset({"hosts"}),
    )
    cause = rules.decide(found)
    kinds = [i["kind"] for i in sentences.record(found, cause).items]

    assert kinds[0] == "deploy"
    assert kinds[-1] == "absence"
    assert kinds.index("check") < kinds.index("error_group") or "error_group" in kinds[:3]


def test_past_the_bound_the_rest_becomes_one_truncated_absence() -> None:
    """AC94's row part: 10,000 forged callees leave a bounded row."""
    edges = tuple(steady_edge(f"(forged-{n})") for n in range(10_000))

    items = sentences.record(facts(edges=edges), None).items

    assert len(items) == 100
    assert items[-1]["values"]["reason"] == "truncated"


def test_evidence_ids_are_stable_across_reads() -> None:
    """AC74: the same facts give the same ids, so a version can be compared."""
    first = sentences.record(facts(deploys=(signed(4),), edges=(moved_edge(),)), None)
    again = sentences.record(
        facts(deploys=(signed(4),), edges=(moved_edge(failures_during=40),)), None
    )

    assert [i["id"] for i in first.items] == [i["id"] for i in again.items]


def test_no_code_written_sentence_holds_a_banned_word() -> None:
    """BR6, across every kind of fact and every cause line."""
    found = facts(
        deploys=(signed(4), seen(3), signed(10, CALLEE, "d-9")),
        edges=(moved_edge(), moved_edge("(redis)", callee_attr="peer.service")),
        dependency_findings=(dependency_finding(3),),
        traffic=Traffic(10.0, 30.0),
        error_groups=(
            new_group(),
            dataclasses.replace(new_group(), group_id="g-2", exception_type=None),
        ),
        checks=(CheckChange("c-1", "Homepage", "down", START),),
        incidents=(LinkedIncident("i-1", "Errors", START),),
        hosts=(HostReading("db-1", "memory", 0.97, START),),
        failed_reads=frozenset({"intake"}),
        coverage="none",
        host_names_present=False,
        last_release=Release("r", "p", SERVICE, "v1", START - timedelta(hours=3), True),
        truncated_reads=frozenset({"dependencies"}),
    )
    for cause in (rules.decide(found), None):
        record = sentences.record(found, cause)
        lines = [i["sentence"] for i in record.items]
        lines += [line for line in record.summary.values() if line is not None]
        for line in lines:
            words = sentences.all_words(line).lower()
            assert not [banned for banned in sentences.BANNED_WORDS if banned in words], words
