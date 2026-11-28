"""The fault tool's shape, without a store (F22, plan T1.0).

The live half — the real project job opening, closing and reopening a finding
over these rows — is run by hand against the dev stack and recorded in
`status.md`, because it needs the whole detection pipeline judging.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fault import FAULT_TIMES, NORMAL, fault_rows, window
from seed import LATENCY_BOUNDS, require_local

NOW = datetime(2026, 11, 2, 14, 2, 30, tzinfo=UTC)


def _p95_ms(buckets: tuple[int, ...]) -> float:
    """The bucket bound 95% of requests fall at or under, as detection reads it."""
    total = sum(buckets)
    seen = 0
    for index, count in enumerate(buckets):
        seen += count
        if seen >= 0.95 * total:
            return LATENCY_BOUNDS[index] if index < len(LATENCY_BOUNDS) else LATENCY_BOUNDS[-1]
    return LATENCY_BOUNDS[-1]


def test_a_fault_raises_the_p95_about_four_times_above_normal() -> None:
    normal = next(fault_rows("checkout", start=NOW, minutes=1, median_ms=NORMAL.median_ms, rpm=120))
    broken = next(
        fault_rows(
            "checkout", start=NOW, minutes=1, median_ms=NORMAL.median_ms * FAULT_TIMES, rpm=120
        )
    )
    assert 3.0 <= _p95_ms(broken.latency_buckets) / _p95_ms(normal.latency_buckets) <= 6.0


def test_every_minute_is_written_and_every_request_is_bucketed() -> None:
    rows = list(fault_rows("checkout", start=NOW, minutes=20, median_ms=480, rpm=120))
    assert len(rows) == 20
    assert [row.minute.minute for row in rows[:3]] == [2, 3, 4]
    for row in rows:
        assert sum(row.latency_buckets) == row.request_count
        assert row.error_count <= row.request_count


def test_the_window_ends_now_not_behind_the_live_lag() -> None:
    """The fault must land on the minutes the job judges next, unlike the seeded
    history, which deliberately stops short of them."""
    start = window(NOW, 20)
    assert start == datetime(2026, 11, 2, 13, 43, tzinfo=UTC)


def test_any_host_but_localhost_is_refused() -> None:
    with pytest.raises(SystemExit, match="refusing"):
        require_local("http://nexovue@prod.example:8123/nexovue", "ClickHouse")
