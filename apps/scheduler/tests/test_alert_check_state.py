"""What one rule makes of a check's new state — pure, so every case is cheap."""

from __future__ import annotations

from nexovue_platform.alerts.model import CheckStateCondition

from nexovue_scheduler.alerts.check_state import Clear, Fire, judge

DOWN_ONLY = CheckStateCondition(states={"down": "critical"})
DEGRADED_AND_DOWN = CheckStateCondition(states={"down": "critical", "degraded": "warning"})


def test_down_fires_at_the_rules_severity() -> None:
    assert judge(DOWN_ONLY, "down") == Fire("critical")


def test_up_clears() -> None:
    assert judge(DOWN_ONLY, "up") == Clear()


def test_misconfigured_does_not_fire_a_rule_that_asked_only_for_down() -> None:
    """FR7, AC7: an author's typo is not an outage worth a page."""
    assert judge(DOWN_ONLY, "misconfigured") == Clear()


def test_each_selected_state_fires_at_its_own_severity() -> None:
    assert judge(DEGRADED_AND_DOWN, "degraded") == Fire("warning")
    assert judge(DEGRADED_AND_DOWN, "down") == Fire("critical")
