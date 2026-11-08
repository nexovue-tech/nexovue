"""The release sync's per-minute walk, pure (F28 plan T1.10; AC24, AC25, AC55, AC56,
AC58, AC72's bound).

The walk turns a project's minutes by version into releases and periods (FR19, FR42,
FR43): a version's first counted minute opens its period; another version holding
over 90% for 10 minutes in a row, or 60 quiet minutes while another serves, ends it;
20% and 5 requests over 10 minutes brings an ended one back. Its store reads and writes
are `test_release_sync_live.py`'s.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from nexovue_platform import releases as platform

from nexovue_scheduler.detection import method
from nexovue_scheduler.releases import sync
from nexovue_scheduler.releases.store import VersionCount
from nexovue_scheduler.releases.sync import (
    KnownPeriod,
    KnownRelease,
    SyncWindow,
    sync_window,
    walk,
)

SERVICE: Final = "checkout"
MINUTE: Final = timedelta(minutes=1)
HOUR: Final = timedelta(hours=1)
T0: Final = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
PERIOD_ID: Final = uuid.UUID("018f6e2a-0000-7000-8000-000000000001")


def _traffic(
    version: str, first: datetime, minutes: int, requests: int = 10, *, every: int = 1
) -> list[VersionCount]:
    return [
        VersionCount(SERVICE, first + MINUTE * offset, version, requests)
        for offset in range(0, minutes, every)
    ]


def _first_run(to: datetime) -> SyncWindow:
    window = sync_window(None, to - MINUTE)
    assert window is not None
    return window


def _walk(
    counts: Iterable[VersionCount],
    *,
    to: datetime,
    releases: Iterable[KnownRelease] = (),
    periods: Iterable[KnownPeriod] = (),
    window: SyncWindow | None = None,
    cap: int = sync.NEW_RELEASES_PER_RUN,
) -> sync.Changes:
    return walk(
        list(counts),
        list(releases),
        list(periods),
        window or _first_run(to),
        new_release_cap=cap,
    )


def _periods_of(changes: sync.Changes, version: str) -> list[sync.NewPeriod]:
    return [period for period in changes.new_periods if period.version == version]


# ------------------------------------------------------------------ the window


def test_a_first_run_reads_no_more_than_its_bound() -> None:
    """AC72: six hours, ending at the settled minute."""
    settled = T0 + 10 * HOUR
    window = sync_window(None, settled)
    assert window is not None
    assert window.read_to == settled + MINUTE
    assert window.read_to - window.read_from == sync.RUN_BOUND
    assert window.decide_from == window.read_from


def test_a_later_run_rereads_an_hour_already_read() -> None:
    """Design 3, step 4: the hour the 60-minute rule needs also catches late first counts,
    which the design's five minutes would (`sync.REREAD`)."""
    synced = T0 + 5 * HOUR
    window = sync_window(synced, synced + 2 * MINUTE)
    assert window is not None
    assert window.decide_from == synced + MINUTE
    assert window.read_from == synced + MINUTE - sync.REREAD
    assert window.read_to == synced + 3 * MINUTE


def test_a_later_run_is_bounded_too() -> None:
    synced = T0
    window = sync_window(synced, synced + 2 * sync.RUN_BOUND)
    assert window is not None
    assert window.read_to - window.decide_from == sync.RUN_BOUND


def test_nothing_settled_since_the_last_run_reads_nothing() -> None:
    assert sync_window(T0, T0) is None
    assert sync_window(T0, T0 - MINUTE) is None


def test_the_floors_are_detection_s() -> None:
    """FR23: the same values as F6's floors."""
    assert platform.FAILURE_FLOOR == method.REQUESTS_TO_JUDGE_FAILING
    assert platform.P95_FLOOR == method.REQUESTS_TO_JUDGE_SLOWER


# -------------------------------------------------------------- first counts


def test_the_first_version_counted_on_a_service_is_already_running() -> None:
    """AC58, FR43: running when we started counting."""
    changes = _walk(_traffic("v1", T0, 30), to=T0 + 30 * MINUTE)
    (period,) = _periods_of(changes, "v1")
    assert period.already_running
    assert period.started_at == T0
    assert period.previous_version is None
    assert changes.seen[(SERVICE, "v1")] == (T0, T0 + 29 * MINUTE)


def test_a_rolling_deploy_opens_the_new_version_and_ends_the_old() -> None:
    """Both serve for a while; the new one then holds over 90% for 10 minutes."""
    old = _traffic("v1", T0, 30) + _traffic("v1", T0 + 30 * MINUTE, 10, requests=5)
    new = _traffic("v2", T0 + 30 * MINUTE, 40)
    changes = _walk(old + new, to=T0 + 70 * MINUTE)
    (v1,) = _periods_of(changes, "v1")
    (v2,) = _periods_of(changes, "v2")
    assert v2.started_at == T0 + 30 * MINUTE
    assert v2.previous_version == "v1"
    assert not v2.already_running and not v2.is_return
    # v1 stopped at minute 40; v2 then held 100% from minute 40.
    assert v1.ended_at == T0 + 40 * MINUTE
    assert v2.ended_at is None


def test_a_canary_under_10_percent_for_10_minutes_ends_its_period() -> None:
    """FR19 as written: the long-canary misreading is a stated risk (plan, *New risks*)."""
    old = _traffic("v1", T0, 60, requests=95)
    canary = _traffic("v2", T0 + 10 * MINUTE, 50, requests=5)
    changes = _walk(old + canary, to=T0 + 60 * MINUTE)
    (v2,) = _periods_of(changes, "v2")
    assert v2.ended_at == T0 + 10 * MINUTE


# ----------------------------------------------------------------- returns


def test_v1_v2_v1_inside_one_read_gives_a_return_with_its_own_period() -> None:
    """AC25 and AC55's outage case: one release row, two periods, the second a return
    that compares against what it replaced."""
    counts = (
        _traffic("v1", T0, 20)
        + _traffic("v2", T0 + 20 * MINUTE, 20)
        + _traffic("v1", T0 + 40 * MINUTE, 30)
    )
    changes = _walk(counts, to=T0 + 70 * MINUTE)
    first, back = _periods_of(changes, "v1")
    (v2,) = _periods_of(changes, "v2")
    assert first.started_at == T0 and first.ended_at == T0 + 20 * MINUTE
    assert v2.ended_at == T0 + 40 * MINUTE
    assert back.is_return and back.started_at == T0 + 40 * MINUTE
    assert back.previous_version == "v2"
    assert not back.already_running


def test_one_stray_request_from_an_ended_version_opens_no_return() -> None:
    """AC55: under 5 requests in the stretch."""
    counts = (
        _traffic("v1", T0, 20)
        + _traffic("v2", T0 + 20 * MINUTE, 60)
        + [VersionCount(SERVICE, T0 + 50 * MINUTE, "v1", 1)]
    )
    changes = _walk(counts, to=T0 + 80 * MINUTE)
    assert len(_periods_of(changes, "v1")) == 1


def test_a_sparse_two_version_service_opens_no_return() -> None:
    """AC55: a request per version every 15 minutes never ends a period."""
    counts = _traffic("v1", T0, 240, requests=1, every=15) + _traffic(
        "v2", T0 + 7 * MINUTE, 233, requests=1, every=15
    )
    changes = _walk(counts, to=T0 + 4 * HOUR)
    assert [p.ended_at for p in changes.new_periods] == [None, None]
    assert not any(p.is_return for p in changes.new_periods)


def test_a_quiet_service_s_swap_ends_the_old_period_after_60_minutes() -> None:
    """AC55: v1 stops; v2 serves a request every 3 minutes, so never 10 in a row."""
    counts = _traffic("v1", T0, 30, requests=2, every=3) + _traffic(
        "v2", T0 + 31 * MINUTE, 120, requests=2, every=3
    )
    changes = _walk(counts, to=T0 + 151 * MINUTE)
    (v1,) = _periods_of(changes, "v1")
    # v1's last request was at minute 27; the 60 quiet minutes held v2's 20 requests.
    assert v1.ended_at == T0 + 28 * MINUTE


# ------------------------------------------------------ what Postgres holds


def _known_release(version: str, first: datetime, last: datetime) -> KnownRelease:
    return KnownRelease(
        service_name=SERVICE,
        version=version,
        first_seen_at=first,
        last_seen_at=last,
        first_reported_at=None,
    )


def test_a_known_open_period_is_ended_by_id_and_never_reopened() -> None:
    """AC24: the release and its first period are Postgres's; only an end is added."""
    synced = T0 + 60 * MINUTE
    window = sync_window(synced, synced + 30 * MINUTE)
    assert window is not None
    counts = _traffic("v1", T0 + MINUTE, 50) + _traffic("v2", T0 + 51 * MINUTE, 40)
    known = KnownPeriod(
        id=PERIOD_ID,
        service_name=SERVICE,
        version="v1",
        started_at=T0 - HOUR,
        ended_at=None,
        start_basis=platform.SEEN,
    )
    changes = _walk(
        counts,
        to=window.read_to,
        window=window,
        releases=[_known_release("v1", T0 - HOUR, synced)],
        periods=[known],
    )
    assert [end.period_id for end in changes.ends] == [PERIOD_ID]
    # v2 took over at minute 51; the end is decided in this run, dated when it happened.
    assert changes.ends[0].ended_at == T0 + 51 * MINUTE
    (v2,) = _periods_of(changes, "v2")
    # The minutes read again are read for late first counts: v2 starts where it was counted.
    assert v2.started_at == T0 + 51 * MINUTE
    assert v2.previous_version == "v1"
    assert _periods_of(changes, "v1") == []


def test_a_version_first_seen_in_late_rows_read_again_keeps_its_true_minute() -> None:
    """AC55: rows that arrived after the last run, within what is read again."""
    synced = T0 + 60 * MINUTE
    window = sync_window(synced, synced + 5 * MINUTE)
    assert window is not None
    late = [VersionCount(SERVICE, synced - 2 * MINUTE, "v3", 7)]
    changes = _walk(
        _traffic("v1", T0, 65) + late,
        to=window.read_to,
        window=window,
        releases=[_known_release("v1", T0, synced)],
        periods=[
            KnownPeriod(uuid.uuid4(), SERVICE, "v1", T0, None, platform.SEEN),
        ],
    )
    (v3,) = _periods_of(changes, "v3")
    assert v3.started_at == synced - 2 * MINUTE
    assert changes.seen[(SERVICE, "v3")][0] == synced - 2 * MINUTE


def test_a_reported_period_takes_its_first_counted_minute_as_start() -> None:
    """FR42, AC57: before any traffic the start is the reported time; once counted, the
    counted minute."""
    reported = KnownPeriod(PERIOD_ID, SERVICE, "v2", T0, None, platform.REPORTED)
    release = KnownRelease(SERVICE, "v2", None, None, first_reported_at=T0)
    changes = _walk(
        _traffic("v2", T0 + 2 * MINUTE, 20),
        to=T0 + 30 * MINUTE,
        releases=[release],
        periods=[reported],
    )
    assert [(c.period_id, c.started_at) for c in changes.counted_starts] == [
        (PERIOD_ID, T0 + 2 * MINUTE)
    ]
    assert _periods_of(changes, "v2") == []


# ----------------------------------------------------------------- the cap


def test_past_the_per_run_cap_the_run_stops_at_the_last_minute_finished() -> None:
    """Design 3, step 9: nothing is lost; the next run carries on."""
    counts = [
        VersionCount(SERVICE, T0 + MINUTE * minute, f"v{minute}-{n}", 1)
        for minute in range(10)
        for n in range(3)
    ]
    changes = _walk(counts, to=T0 + 10 * MINUTE, cap=7)
    assert changes.capped
    assert changes.new_releases == 6
    assert changes.synced_through == T0 + MINUTE
    assert {version for _, version in changes.seen} == {
        f"v{m}-{n}" for m in (0, 1) for n in (0, 1, 2)
    }


def test_one_minute_over_the_cap_is_still_taken_so_a_run_always_moves() -> None:
    counts = [VersionCount(SERVICE, T0, f"v{n}", 1) for n in range(12)]
    changes = _walk(counts, to=T0 + 5 * MINUTE, cap=5)
    assert changes.new_releases == 12
    assert changes.synced_through == T0 + 4 * MINUTE


@pytest.mark.parametrize(
    ("service", "version", "kept"),
    [
        ("checkout", "v1", True),
        ("x" * 201, "v1", False),
        ("check\x00out", "v1", False),
        ("checkout", "v" * 201, False),
    ],
)
def test_rows_postgres_cannot_hold_are_skipped_and_counted(
    service: str, version: str, kept: bool
) -> None:
    """AC56: one bad name never stops a project."""
    rows = [VersionCount(service, T0, version, 3)]
    stored, skipped = sync.storable_counts(rows)
    assert (len(stored), skipped) == ((1, 0) if kept else (0, 1))
