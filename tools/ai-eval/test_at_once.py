"""What the 100-findings-at-once measurement reports from the times it is given (F3 plan T2.16;
AC97). The live part is `apps/scheduler/tests/test_bed_live.py`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from at_once import render, summarize

BEGAN = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def seconds(count: float) -> datetime:
    return BEGAN + timedelta(seconds=count)


def test_the_queue_clears_when_the_last_explanation_lands_and_percentiles_are_per_finding() -> None:
    result = summarize(
        started_at=[BEGAN] * 4,
        explained_at=[seconds(1), seconds(2), seconds(3), seconds(8)],
        probe_due=seconds(1),
        probe_ran=seconds(2),
    )

    assert result.explained == 4 and result.cleared_after == timedelta(seconds=8)
    assert result.probe_waited == timedelta(seconds=1) and result.probe_ran_during_the_burst
    assert result.latency is not None
    assert (result.latency.p50, result.latency.p95) == (timedelta(seconds=2), timedelta(seconds=8))


def test_a_probe_that_ran_after_the_burst_is_said_to_prove_nothing() -> None:
    result = summarize(
        started_at=[BEGAN], explained_at=[seconds(2)], probe_due=seconds(1), probe_ran=seconds(30)
    )

    assert not result.probe_ran_during_the_burst
    assert "no proof" in render(result, machine="a test machine")


def test_a_finding_never_explained_and_a_probe_that_never_ran_are_stated_not_dropped() -> None:
    result = summarize(
        started_at=[BEGAN, BEGAN],
        explained_at=[seconds(2), None],
        probe_due=seconds(1),
        probe_ran=None,
    )

    text = render(result, machine="a test machine")
    assert result.explained == 1 and result.latency is not None and result.latency.missing == 1
    assert "waited **never**" in text and "1 never explained" in text


def test_it_refuses_times_that_do_not_pair_up() -> None:
    with pytest.raises(ValueError, match="one explanation time"):
        summarize(started_at=[BEGAN], explained_at=[], probe_due=BEGAN, probe_ran=None)


def test_the_clear_time_runs_from_the_first_finding_opening_not_the_last() -> None:
    result = summarize(
        started_at=[seconds(0), seconds(5)],
        explained_at=[seconds(6), seconds(10)],
        probe_due=seconds(1),
        probe_ran=seconds(2),
    )

    assert result.cleared_after == timedelta(seconds=10)
