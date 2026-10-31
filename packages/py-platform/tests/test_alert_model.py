"""The rule vocabulary the API validates with and the scheduler parses with."""

from __future__ import annotations

import uuid

import pytest
from nexovue_platform.alerts.model import (
    DEFAULT_RULE_CONDITION,
    SEVERITY_RANK,
    AnomalyFindingCondition,
    CheckStateCondition,
    check_subject,
    parse_check_condition,
    parse_condition,
)
from pydantic import ValidationError


def test_the_default_rule_is_any_check_down_as_critical() -> None:
    assert DEFAULT_RULE_CONDITION.model_dump(mode="json") == {
        "kind": "check_state",
        "check_ids": None,
        "states": {"down": "critical"},
    }


def test_a_rule_with_no_checks_named_covers_every_check() -> None:
    condition = CheckStateCondition(states={"down": "critical"})
    assert condition.covers(uuid.uuid4())


def test_a_rule_naming_checks_covers_only_those() -> None:
    chosen, other = uuid.uuid4(), uuid.uuid4()
    condition = CheckStateCondition(check_ids=[chosen], states={"down": "critical"})
    assert condition.covers(chosen)
    assert not condition.covers(other)


def test_each_selected_state_carries_its_own_severity() -> None:
    condition = CheckStateCondition(states={"down": "critical", "degraded": "warning"})
    assert condition.severity_of("down") == "critical"
    assert condition.severity_of("degraded") == "warning"


def test_misconfigured_is_not_implied_by_down() -> None:
    """FR7: a typo in a check's own configuration does not page anyone who asked for down."""
    condition = CheckStateCondition(states={"down": "critical"})
    assert condition.severity_of("misconfigured") is None


def test_up_is_never_something_a_rule_fires_on() -> None:
    with pytest.raises(ValidationError):
        parse_check_condition({"kind": "check_state", "states": {"up": "critical"}})


def test_a_rule_must_select_at_least_one_state() -> None:
    with pytest.raises(ValidationError):
        parse_check_condition({"kind": "check_state", "states": {}})


def test_an_unknown_field_is_refused_rather_than_ignored() -> None:
    with pytest.raises(ValidationError):
        parse_check_condition(
            {"kind": "check_state", "states": {"down": "critical"}, "thresold": 5}
        )


def test_critical_outranks_warning() -> None:
    assert SEVERITY_RANK["critical"] > SEVERITY_RANK["warning"]


def test_a_check_subject_names_the_check() -> None:
    check_id = uuid.uuid4()
    assert check_subject(check_id) == f"check:{check_id}"


def test_an_anomaly_finding_rule_parses_by_its_kind() -> None:
    condition = parse_condition({"kind": "anomaly_finding", "minimum_severity": "critical"})
    assert condition == AnomalyFindingCondition(minimum_severity="critical")


def test_an_anomaly_finding_rule_with_no_services_named_covers_every_service() -> None:
    condition = AnomalyFindingCondition(minimum_severity="warning")
    assert condition.covers("checkout")


def test_an_anomaly_finding_rule_naming_services_covers_only_those() -> None:
    condition = AnomalyFindingCondition(services=["checkout"], minimum_severity="warning")
    assert condition.covers("checkout")
    assert not condition.covers("payments")


def test_an_anomaly_finding_rule_admits_its_minimum_severity_and_above() -> None:
    """FR28: the severity is the finding's; the rule only chooses which it sends."""
    critical_only = AnomalyFindingCondition(minimum_severity="critical")
    assert critical_only.admits("critical")
    assert not critical_only.admits("warning")
    assert AnomalyFindingCondition(minimum_severity="warning").admits("critical")


def test_an_anomaly_finding_rule_must_name_its_minimum_severity() -> None:
    with pytest.raises(ValidationError):
        parse_condition({"kind": "anomaly_finding"})
