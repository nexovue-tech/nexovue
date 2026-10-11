"""Substitution refuses what it cannot fill; maintenance windows repeat as written."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nexovue_platform.checks.maintenance import WindowSchedule, is_active
from nexovue_platform.checks.templating import (
    UnresolvedReferenceError,
    references_in,
    substitute,
)


def test_variables_and_secrets_are_substituted() -> None:
    text = "Bearer {{secret.TOKEN}} for {{ order.id }}"
    assert (
        substitute(text, variables={"order.id": "42"}, secrets={"TOKEN": "t"}) == "Bearer t for 42"
    )


def test_an_undefined_reference_is_refused_never_sent_as_literal_text() -> None:
    with pytest.raises(UnresolvedReferenceError) as refused:
        substitute("{{secret.GONE}}", variables={"GONE": "not a secret"}, secrets={})
    assert refused.value.reference == "secret.GONE"


def test_references_are_split_by_where_they_come_from() -> None:
    found = references_in("{{a}} {{secret.B}} {{secret.}} {{ c }}")
    assert found.secrets == {"B"}
    assert found.variables == {"a", "secret.", "c"}


START = datetime(2026, 9, 14, 2, 0, tzinfo=UTC)  # a Monday


def test_a_one_off_window_covers_only_its_own_span() -> None:
    window = WindowSchedule(starts_at=START, ends_at=START + timedelta(hours=1))
    assert is_active(window, START + timedelta(minutes=30))
    assert not is_active(window, START + timedelta(hours=1))
    assert not is_active(window, START + timedelta(days=1, minutes=30))


def test_a_weekly_window_repeats_and_stops_after_its_last_start() -> None:
    window = WindowSchedule(
        starts_at=START,
        ends_at=START + timedelta(hours=1),
        recurrence="weekly",
        repeat_until=START + timedelta(weeks=2),
    )
    assert is_active(window, START + timedelta(weeks=1, minutes=10))
    assert not is_active(window, START + timedelta(days=1, minutes=10))
    assert is_active(window, START + timedelta(weeks=2, minutes=10))
    assert not is_active(window, START + timedelta(weeks=3, minutes=10))
