"""The evidence job's wording decision, pure (F3 plan T5.2; AC5's decisions, AC57's no-run
cases, AC74's material and non-material, AC92's held run; design, *Wording states*).

`wording.decide` imports no AI code (`test_detection_has_no_ai.py` walks it), and reaches
every state the evidence job may write: `off`, `held`, `abstained`, `pending`, or carried.
"""

from __future__ import annotations

from typing import Any

import pytest
from nexovue_platform.evidence import CAUSE_BAR_RECORDED

from nexovue_scheduler.evidence.wording import Decision, Previous, Settings, decide

DEPLOY: dict[str, Any] = {"id": "deploy:d-1", "kind": "deploy"}
EDGE: dict[str, Any] = {"id": "dependency:orders-db", "kind": "dependency"}
ERROR: dict[str, Any] = {"id": "error_group:g-1", "kind": "error_group"}
ABSENCE: dict[str, Any] = {"id": "absence:no_host_names", "kind": "absence"}
CAUSE: dict[str, Any] = {"evidence_id": "deploy:d-1", "rule": "signed_deploy", "strength": "likely"}
WORDS: dict[str, Any] = {"claims": [], "next_checks": [], "abstain": False, "model": "m"}
ON = Settings(ai_on=True, opted_out=False, show_held_causes=True, call_seconds=60)


def previous(state: str, **changes: Any) -> Previous:
    return Previous(
        **{
            "items": [DEPLOY, ABSENCE],
            "likely_cause": CAUSE,
            "state": state,
            "reason": "limited" if state == "failed" else None,
            "words": WORDS if state in ("done", "failed", "pending") else None,
            "from_version": 1,
        }
        | changes
    )


def run(
    items: list[dict[str, Any]] | None = None,
    cause: dict[str, Any] | None = CAUSE,
    before: Previous | None = None,
    *,
    muted: bool = False,
    settings: Settings = ON,
) -> Decision:
    return decide(
        items=[DEPLOY, ABSENCE] if items is None else items,
        likely_cause=cause,
        previous=before,
        muted_or_flapping=muted,
        settings=settings,
    )


@pytest.mark.parametrize(
    ("settings", "why"),
    [
        (Settings(ai_on=False, opted_out=False, show_held_causes=True, call_seconds=60), "off"),
        (Settings(ai_on=True, opted_out=True, show_held_causes=True, call_seconds=60), "out"),
        (Settings(ai_on=True, opted_out=False, show_held_causes=False, call_seconds=60), "held"),
    ],
)
def test_ai_off_an_opted_out_project_or_held_causes_write_off_and_start_nothing(
    settings: Settings, why: str
) -> None:
    """FR33, FR28; AC92: while FR50 holds, no wording job is added."""
    assert run(settings=settings) == Decision("off")
    assert run(before=previous("done"), settings=settings) == Decision("off")


def test_a_cause_from_a_rule_not_released_starts_no_run_without_the_override() -> None:
    """A85: with the bar recorded but the rule not on the released list, still `off`."""
    assert CAUSE_BAR_RECORDED is False
    held = Settings(ai_on=True, opted_out=False, show_held_causes=False, call_seconds=60)
    assert run(cause=None, settings=held) == Decision("off")


def test_a_muted_or_flapping_finding_is_held_and_starts_no_run() -> None:
    """FR35, AC57: a click starts one later; nothing by itself."""
    assert run(muted=True) == Decision("held")


def test_only_absences_abstain_with_no_run() -> None:
    """FR33: no model is called when there is nothing but what is missing."""
    assert run(items=[ABSENCE], cause=None) == Decision("abstained")


def test_the_first_version_is_material_and_starts_exactly_one_run() -> None:
    assert run() == Decision("pending", run=True)


@pytest.mark.parametrize(
    ("items", "cause"),
    [
        pytest.param([DEPLOY, EDGE, ABSENCE], CAUSE, id="a new fact that may be a cause"),
        pytest.param([DEPLOY, ABSENCE], dict(CAUSE, strength="possible"), id="the cause moved"),
        pytest.param([DEPLOY, ABSENCE], None, id="the cause went"),
    ],
)
def test_a_material_change_starts_one_run_and_carries_the_earlier_words(
    items: list[dict[str, Any]], cause: dict[str, Any] | None
) -> None:
    """FR34, AC74, AC89: the earlier words stay, marked as from their own version."""
    assert run(items, cause, previous("done")) == Decision(
        "pending", words=WORDS, from_version=1, run=True
    )


@pytest.mark.parametrize("state", ["done", "abstained", "failed", "over_limit", "off", "held"])
def test_a_non_material_version_starts_no_run_and_keeps_the_earlier_state(state: str) -> None:
    """AC74: a new error group supports and is not material; nor are moved counts, which
    write no version at all (FR43). Words are carried with their version (AC73)."""
    decided = run([DEPLOY, ERROR, ABSENCE], CAUSE, previous(state))
    assert decided.state == state and decided.run is False
    assert decided.reason == ("limited" if state == "failed" else None)
    carried = state in ("done", "failed")
    assert decided.words == (WORDS if carried else None)
    assert decided.from_version == (1 if carried else None)


def test_a_non_material_version_over_pending_gets_its_own_run() -> None:
    """AC56: the older job sees a newer version and stops; this one must not stay pending."""
    assert run(before=previous("pending")) == Decision(
        "pending", words=WORDS, from_version=1, run=True
    )


def test_ai_off_wins_over_muted_and_muted_over_abstaining() -> None:
    off = Settings(ai_on=False, opted_out=False, show_held_causes=True, call_seconds=60)
    assert run(items=[ABSENCE], muted=True, settings=off) == Decision("off")
    assert run(items=[ABSENCE], cause=None, muted=True) == Decision("held")
