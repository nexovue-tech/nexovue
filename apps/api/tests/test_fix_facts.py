"""F5's rung 1 additions to F28's *Release before this* (plan T1.2; spec FR1, AC34).

One test per fixed template, by exact string. F28 words the release itself, so these are
only what F5 adds: the version before it, CI's last word on that version, no earlier
version, and past F3's look-back. Where F28's own sentence is all there is (nothing
before, already running, a day or more old, a project finding), F5 adds nothing. No
template holds a causal word. The clock is fixed.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from nexovue_platform.evidence import DEPLOY_LOOKBACK

from nexovue_api.findings.fix_facts import (
    FAILED_SINCE,
    LONGER_THAN_LOOKBACK,
    NO_EARLIER_VERSION,
    RAN_BEFORE_IT,
    ROLLED_BACK_SINCE,
    STATED_PLAINLY_WITHIN,
    PeriodSeen,
    deploy_line_addition,
)
from nexovue_api.findings.fix_schemas import DeployLineAddition

STARTED: Final = datetime(2026, 11, 11, 14, 2, tzinfo=UTC)
BEFORE_ID: Final = uuid.UUID("11111111-1111-4111-8111-111111111111")
EARLIER_ID: Final = uuid.UUID("22222222-2222-4222-8222-222222222222")
# BR11 and "before, not because" (`errorgroups/schemas.py`). "Likely cause" names F3's
# record, which FR1's own sentence uses; it claims nothing.
CAUSAL: Final = re.compile(
    r"\b(caused|causing|because|due to|root cause|fault|broke)\b", re.IGNORECASE
)


def _period(release_id: uuid.UUID, version: str, minutes_before: int, **fields: Any) -> PeriodSeen:
    return PeriodSeen(
        **{
            "release_id": release_id,
            "period_id": uuid.uuid4(),
            "version": version,
            "started_at": STARTED - timedelta(minutes=minutes_before),
            "already_running": False,
            "last_state": "finished",
            **fields,
        }
    )


def _said(addition: DeployLineAddition | None) -> list[str]:
    assert addition is not None
    return [
        "".join(segment.name or segment.text or "" for segment in sentence)
        for sentence in addition.sentences
    ]


def test_ac34_the_version_before_it_is_named_and_linked() -> None:
    addition = deploy_line_addition(
        started_at=STARTED,
        before=_period(BEFORE_ID, "v2.1", 4),
        earlier=_period(EARLIER_ID, "v2.0", 300),
    )

    assert _said(addition) == ["v2.0 ran before it."]
    assert addition is not None and addition.release_id == BEFORE_ID
    assert addition.earlier is not None and addition.earlier.release_id == EARLIER_ID
    assert addition.sentences[0][0].name == "v2.0", "a version is a name, never Nexovue's words"


def test_ac34_a_version_ci_last_reported_failed_says_so() -> None:
    addition = deploy_line_addition(
        started_at=STARTED,
        before=_period(BEFORE_ID, "v2.1", 4),
        earlier=_period(EARLIER_ID, "v2.0", 300, last_state="failed"),
    )

    assert _said(addition) == ["v2.0 ran before it. CI's last report on it said it failed."]
    assert addition is not None and addition.earlier is not None
    assert addition.earlier.last_state == "failed"


def test_ac34_a_version_ci_last_reported_rolled_back_says_so() -> None:
    addition = deploy_line_addition(
        started_at=STARTED,
        before=_period(BEFORE_ID, "v2.1", 4),
        earlier=_period(EARLIER_ID, "v2.0", 300, last_state="rolled_back"),
    )

    assert _said(addition) == ["v2.0 ran before it. CI's last report on it said it rolled back."]


def test_ac34_no_earlier_version() -> None:
    addition = deploy_line_addition(
        started_at=STARTED, before=_period(BEFORE_ID, "v2.1", 4), earlier=None
    )

    assert _said(addition) == ["No earlier version of this service is known."]
    assert addition is not None and addition.earlier is None


def test_ac34_a_release_that_came_back_still_names_what_ran_before_it() -> None:
    """F28 words "came back"; F5 adds the version it replaced, as for any release."""
    addition = deploy_line_addition(
        started_at=STARTED,
        before=_period(BEFORE_ID, "v2.0", 10),
        earlier=_period(EARLIER_ID, "v2.1", 40),
    )

    assert _said(addition) == ["v2.1 ran before it."]


@pytest.mark.parametrize("minutes", [31, 90, 24 * 60 - 1])
def test_ac34_over_the_look_back_says_so(minutes: int) -> None:
    addition = deploy_line_addition(
        started_at=STARTED,
        before=_period(BEFORE_ID, "v2.1", minutes),
        earlier=_period(EARLIER_ID, "v2.0", minutes + 60),
    )

    assert _said(addition) == [
        "v2.0 ran before it.",
        "Longer before this than Nexovue looks for a likely cause.",
    ]


@pytest.mark.parametrize("minutes", [0, 4, 30])
def test_within_the_look_back_there_is_no_look_back_sentence(minutes: int) -> None:
    addition = deploy_line_addition(
        started_at=STARTED,
        before=_period(BEFORE_ID, "v2.1", minutes),
        earlier=_period(EARLIER_ID, "v2.0", 300),
    )

    assert LONGER_THAN_LOOKBACK not in _said(addition)


@pytest.mark.parametrize("minutes", [24 * 60, 3 * 24 * 60])
def test_ac34_over_24_hours_f5_adds_nothing_to_f28s_older_line(minutes: int) -> None:
    assert (
        deploy_line_addition(
            started_at=STARTED,
            before=_period(BEFORE_ID, "v2.1", minutes),
            earlier=_period(EARLIER_ID, "v2.0", minutes + 60),
        )
        is None
    )


def test_ac34_no_release_before_and_one_already_running_add_nothing() -> None:
    running = _period(BEFORE_ID, "v2.1", 4, already_running=True)

    assert deploy_line_addition(started_at=STARTED, before=None, earlier=None) is None
    assert deploy_line_addition(started_at=STARTED, before=running, earlier=None) is None


def test_the_bounds_are_f3s_look_back_and_f28s_day() -> None:
    assert timedelta(minutes=30) == DEPLOY_LOOKBACK
    assert timedelta(hours=24) == STATED_PLAINLY_WITHIN


@pytest.mark.parametrize(
    "template",
    [RAN_BEFORE_IT, FAILED_SINCE, ROLLED_BACK_SINCE, NO_EARLIER_VERSION, LONGER_THAN_LOOKBACK],
)
def test_ac34_no_template_holds_a_causal_word(template: str) -> None:
    assert CAUSAL.search(template) is None
