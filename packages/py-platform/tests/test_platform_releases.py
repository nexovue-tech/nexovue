"""F28's comparison, pure (plan T1.9; AC31, AC32, AC33, AC62, AC63, AC65's last clause).

Every row of the design's ordered list is reached here with exact minutes, both
sides of z = 3 and the 1-point rule, the p95 ratio and its floor, the warm-up, and
the one name check against the fixture the worker's Go reads.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from nexovue_platform import releases
from nexovue_platform.releases import (
    Comparison,
    PeriodToCompare,
    VersionMinute,
    bucket_p95,
    choose_method,
    compare_failure_share,
    compare_p95,
    name_allowed,
    storable,
)
from nexovue_platform.slo import BUCKET_COUNT, LOG_EDGES

FIXTURE: Final = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "fixtures"
    / "release-names.json"
)
START: Final = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
MINUTE: Final = timedelta(minutes=1)
HOUR: Final = timedelta(hours=1)
# Bucket 30 is about 160 ms, bucket 34 about 290 ms: well apart, either side of 1.2.
FAST: Final = 30
SLOW: Final = 34


def _buckets(requests: int, bucket: int = FAST) -> tuple[int, ...]:
    counts = [0] * BUCKET_COUNT
    counts[bucket] = requests
    return tuple(counts)


def _minutes(
    version: str,
    first: datetime,
    count: int,
    *,
    requests: int,
    errors: int = 0,
    bucket: int = FAST,
) -> list[VersionMinute]:
    return [
        VersionMinute(
            minute=first + MINUTE * offset,
            version=version,
            requests=requests,
            errors=errors,
            buckets=_buckets(requests, bucket),
        )
        for offset in range(count)
    ]


def _period(**changes: object) -> PeriodToCompare:
    values: dict[str, object] = {
        "started_at": START,
        "version": "v2",
        "previous_version": "v1",
        "already_running": False,
        "previous_started_at": START - timedelta(days=2),
        "reported_at": None,
    }
    values.update(changes)
    return PeriodToCompare(**values)  # type: ignore[arg-type]


def _compare(
    minutes: Iterable[VersionMinute],
    *,
    period: PeriodToCompare | None = None,
    to: datetime | None = None,
    window: timedelta = HOUR,
) -> Comparison:
    return choose_method(
        period or _period(),
        list(minutes),
        to=to or START + HOUR,
        window=window,
        expired_before=START - timedelta(days=30),
    )


# --------------------------------------------------------------- the readings


def test_failure_share_reads_higher_at_z_3_and_not_just_below_it() -> None:
    """AC33: both sides of z = 3."""
    # 30 of 1,000 against 10 of 1,000: z is about 3.18.
    above = compare_failure_share(30, 1000, 10, 1000)
    # 28 of 1,000 against 12 of 1,000: z is about 2.55.
    below = compare_failure_share(28, 1000, 12, 1000)
    assert above.reading == releases.HIGHER
    assert below.reading == releases.NOT_CLEARLY_DIFFERENT
    assert compare_failure_share(10, 1000, 30, 1000).reading == releases.LOWER


def test_0_of_20_against_5_of_20_is_not_clearly_different() -> None:
    """AC33: z is about 2.39, so 'about the same' would be false."""
    assert compare_failure_share(0, 20, 5, 20).reading == releases.NOT_CLEARLY_DIFFERENT


def test_0_of_20_against_0_of_20_is_not_about_the_same() -> None:
    """AC33: twenty requests a side cannot rule out a change of one point."""
    assert compare_failure_share(0, 20, 0, 20).reading == releases.NOT_CLEARLY_DIFFERENT


def test_about_the_same_needs_traffic_that_rules_out_one_point() -> None:
    """FR24: near 0.2%, about 360 requests a side rule it out (design, *The words*)."""
    assert compare_failure_share(2, 1000, 2, 1000).reading == releases.ABOUT_SAME
    assert compare_failure_share(1, 300, 1, 300).reading == releases.NOT_CLEARLY_DIFFERENT


def test_every_request_failing_on_both_sides_has_a_spread() -> None:
    """A pooled share of 1 would otherwise give no spread and divide by zero."""
    assert compare_failure_share(20, 20, 20, 20).reading == releases.NOT_CLEARLY_DIFFERENT


def test_failure_share_under_its_floor_reads_too_little_traffic() -> None:
    """FR23, AC31: 20 requests a side, F6's floor."""
    found = compare_failure_share(0, 5, 0, 600)
    assert found.reading is None
    assert found.note == releases.TOO_LITTLE_TRAFFIC


def test_p95_within_a_ratio_of_1_2_is_about_the_same() -> None:
    """AC33, FR24: one bucket is about 19% wide."""
    assert compare_p95(330.0, 300, 300.0, 300).reading == releases.ABOUT_SAME
    assert compare_p95(370.0, 300, 300.0, 300).reading == releases.HIGHER
    assert compare_p95(240.0, 300, 300.0, 300).reading == releases.LOWER


def test_p95_under_its_floor_reads_too_little_traffic() -> None:
    """FR23: 200 requests a side, F6's floor; p95 falls back per cell."""
    found = compare_p95(330.0, 199, 300.0, 300)
    assert (found.reading, found.note) == (None, releases.TOO_LITTLE_TRAFFIC)


def test_bucket_p95_is_log_linear_inside_its_bucket() -> None:
    """Moved here unchanged from detection's method (design 4): one definition."""
    counts = [0] * BUCKET_COUNT
    counts[FAST] = 100
    p95 = bucket_p95(counts)
    assert p95 is not None
    low, high = LOG_EDGES[FAST], LOG_EDGES[FAST + 1]
    assert p95 == pytest.approx(math.exp(low + 0.95 * (high - low)))
    assert bucket_p95([0] * BUCKET_COUNT) is None


# ------------------------------------------------------ the ordered list, AC32


def test_a_period_older_than_the_project_limit_reads_numbers_expired_first() -> None:
    """AC32: before anything is read, or an old release would compare over nothing."""
    old = _period(started_at=START - timedelta(days=31))
    found = choose_method(
        old, [], to=START + HOUR, window=HOUR, expired_before=START - timedelta(days=30)
    )
    assert found.reason == releases.NUMBERS_EXPIRED


def test_under_five_settled_minutes_reads_too_recent() -> None:
    minutes = _minutes("v2", START, 4, requests=100) + _minutes("v1", START, 4, requests=100)
    assert _compare(minutes, to=START + 4 * MINUTE).reason == releases.TOO_RECENT


def test_a_service_with_no_rows_reads_no_request_traffic() -> None:
    """FR27: a browser-only service sends no server spans to count."""
    assert _compare([]).reason == releases.NO_REQUEST_TRAFFIC


def test_traffic_with_no_version_and_no_deploy_reads_no_version_no_deploy() -> None:
    minutes = _minutes("", START - HOUR, 120, requests=100)
    assert _compare(minutes).reason == releases.NO_VERSION_NO_DEPLOY


def test_traffic_with_no_version_and_a_reported_deploy_compares_by_time() -> None:
    """AC30's first half: every row has no version, and a deploy was reported."""
    before = _minutes("", START - HOUR, 60, requests=100, errors=0)
    after = _minutes("", START, 60, requests=100, errors=5)
    found = _compare(before + after, period=_period(reported_at=START))
    assert found.reason is None
    (block,) = found.blocks
    assert block.method == releases.TIME_BEFORE_AFTER
    assert (block.this_version.requests, block.this_version.errors) == (6000, 300)
    assert (block.previous_version.requests, block.previous_version.errors) == (6000, 0)
    assert block.failure_share.reading == releases.HIGHER


def test_a_reported_version_never_seen_while_others_serve_reads_not_seen() -> None:
    """AC30's second half: no time comparison describes traffic it did not serve."""
    minutes = _minutes("v1", START - HOUR, 120, requests=100)
    found = _compare(minutes, period=_period(reported_at=START))
    assert found.reason == releases.NEW_VERSION_NOT_SEEN
    assert found.other_versions == ("v1",)
    assert found.blocks == ()


def test_the_first_version_counted_reads_already_running() -> None:
    minutes = _minutes("v2", START, 60, requests=100)
    found = _compare(minutes, period=_period(previous_version=None, already_running=True))
    assert found.reason == releases.ALREADY_RUNNING


def test_no_previous_version_reads_so() -> None:
    minutes = _minutes("v2", START, 60, requests=100)
    assert _compare(minutes, period=_period(previous_version=None)).reason == (
        releases.NO_PREVIOUS_VERSION
    )


@pytest.mark.parametrize("stored", ["", releases.OTHER_VERSION])
def test_no_version_and_combined_versions_are_never_a_previous_version(stored: str) -> None:
    """AC65's last clause, FR20."""
    minutes = _minutes("v2", START, 60, requests=100) + _minutes(stored, START, 60, requests=100)
    found = _compare(minutes, period=_period(previous_version=stored))
    assert found.reason == releases.NO_PREVIOUS_VERSION


def test_five_requests_on_one_side_read_too_little_traffic_with_counts() -> None:
    """AC31: no percentage, and the counts still show."""
    minutes = _minutes("v2", START, 5, requests=1) + _minutes("v1", START, 60, requests=10)
    found = _compare(minutes)
    assert found.reason == releases.TOO_LITTLE_TRAFFIC
    assert found.blocks == ()
    assert found.traffic is not None
    this, previous = found.traffic
    assert (this.version, this.requests) == ("v2", 5)
    assert previous.version == "v1"


# ---------------------------------------------------------------- the blocks


def _rolling(*, new_errors: int, old_errors: int, minutes: int = 40) -> list[VersionMinute]:
    """Both versions in the same minutes, the old one draining to nothing after."""
    shared = _minutes("v2", START, minutes, requests=100, errors=new_errors)
    shared += _minutes("v1", START, minutes, requests=100, errors=old_errors)
    before = _minutes("v1", START - HOUR, 60, requests=200, errors=old_errors)
    return shared + before


def test_a_rollout_in_the_same_minutes_compares_side_by_side() -> None:
    """AC28's arithmetic: 3% on the new version against 0.2% on the old."""
    minutes = _rolling(new_errors=3, old_errors=0)
    found = _compare(minutes, to=START + 40 * MINUTE)
    assert found.reason is None
    assert found.lead == releases.ROLLOUT
    (block,) = found.blocks
    assert block.method == releases.SIDE_BY_SIDE
    assert block.minutes_compared == 40
    assert (block.this_version.requests, block.this_version.errors) == (4000, 120)
    assert (block.previous_version.requests, block.previous_version.errors) == (4000, 0)
    assert block.failure_share.reading == releases.HIGHER


def test_healthy_during_the_rollout_and_failing_after_leads_with_the_after_block() -> None:
    """AC62: the opening sentence reads from the most recent settled block."""
    overlap = _minutes("v2", START, 10, requests=100) + _minutes("v1", START, 10, requests=100)
    after = _minutes("v2", START + 10 * MINUTE, 50, requests=200, errors=6)
    before = _minutes("v1", START - HOUR, 60, requests=200, errors=0)
    found = _compare(overlap + after + before)
    assert found.lead == releases.AFTER_ROLLOUT
    rollout, finished = found.blocks
    assert rollout.method == releases.SIDE_BY_SIDE
    assert rollout.failure_share.reading == releases.ABOUT_SAME or (
        rollout.failure_share.reading == releases.NOT_CLEARLY_DIFFERENT
    )
    assert finished.method == releases.VERSION_BEFORE_AFTER
    assert finished.start == START + 10 * MINUTE
    assert finished.failure_share.reading == releases.HIGHER
    # The previous version's equal stretch just before the rollout: 50 minutes.
    assert finished.previous_version.requests == 50 * 200


def test_a_replacement_with_no_overlap_compares_before_and_after() -> None:
    """AC29's method: nothing to put side by side."""
    before = _minutes("v1", START - HOUR, 60, requests=100)
    after = _minutes("v2", START, 60, requests=100)
    found = _compare(before + after)
    (block,) = found.blocks
    assert block.method == releases.VERSION_BEFORE_AFTER
    assert block.start == START
    assert block.this_version.requests == block.previous_version.requests == 6000


def test_the_window_bounds_what_is_compared() -> None:
    """FR22: 1, 6 or 24 hours from the period's start."""
    before = _minutes("v1", START - 6 * HOUR, 360, requests=10)
    after = _minutes("v2", START, 360, requests=10)
    one = _compare(before + after, to=START + 6 * HOUR, window=HOUR)
    six = _compare(before + after, to=START + 6 * HOUR, window=6 * HOUR)
    assert one.blocks[0].this_version.requests == 600
    assert six.blocks[0].this_version.requests == 3600


def test_the_comparison_ends_at_the_settled_minute() -> None:
    """AC35: a minute past `to` is not counted."""
    before = _minutes("v1", START - HOUR, 60, requests=100)
    after = _minutes("v2", START, 60, requests=100)
    found = _compare(before + after, to=START + 30 * MINUTE)
    assert found.blocks[0].this_version.requests == 3000
    assert found.blocks[0].end == START + 30 * MINUTE


def test_a_slow_first_three_minutes_are_left_out_of_p95() -> None:
    """AC63, FR48: new instances start cold."""
    cold = _minutes("v2", START, 3, requests=100, bucket=SLOW + 6)
    warm = _minutes("v2", START + 3 * MINUTE, 57, requests=100, bucket=FAST)
    before = _minutes("v1", START - HOUR, 60, requests=100, bucket=FAST)
    found = _compare(cold + warm + before)
    (block,) = found.blocks
    assert block.p95.reading == releases.ABOUT_SAME
    # Failure share keeps every minute.
    assert block.this_version.requests == 6000


def test_a_three_minute_overlap_has_no_p95_and_says_why() -> None:
    """The design's mixed case: the rollout block's p95 is all warm-up."""
    overlap = _minutes("v2", START, 3, requests=100) + _minutes("v1", START, 3, requests=100)
    after = _minutes("v2", START + 3 * MINUTE, 57, requests=100)
    before = _minutes("v1", START - HOUR, 60, requests=100)
    rollout, _ = _compare(overlap + after + before).blocks
    assert rollout.p95.reading is None
    assert rollout.p95.note == releases.WARM_UP_NOTE
    assert rollout.this_version.p95_ms is None


def test_mixed_floors_read_failure_share_and_leave_p95_to_its_floor() -> None:
    """FR23: the failure floor selects the method; p95 falls back per cell."""
    minutes = _minutes("v2", START, 10, requests=5) + _minutes("v1", START, 10, requests=5)
    (block,) = _compare(minutes, to=START + 10 * MINUTE).blocks
    assert block.method == releases.SIDE_BY_SIDE
    assert block.failure_share.reading == releases.NOT_CLEARLY_DIFFERENT
    assert (block.p95.reading, block.p95.note) == (None, releases.TOO_LITTLE_TRAFFIC)


# ----------------------------------------------------------- the name check


def _fixture_cases() -> list[tuple[str, str, bool]]:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    return [(case["name"], case["value"], case["allowed"]) for case in cases]


@pytest.mark.parametrize(("label", "value", "allowed"), _fixture_cases())
def test_the_name_check_agrees_with_the_worker_s_cases(
    label: str, value: str, allowed: bool
) -> None:
    """FR9: one rule at both doors; the Go side reads the same file."""
    assert name_allowed(value) is allowed, label


def test_a_row_postgres_cannot_hold_is_not_storable() -> None:
    """FR18, AC56: over-long, NUL, no version or combined versions are skipped."""
    assert storable("checkout", "v2")
    assert not storable("x" * (releases.NAME_MAX_LENGTH + 1), "v2")
    assert not storable("check\x00out", "v2")
    assert not storable("checkout", "v" * (releases.NAME_MAX_LENGTH + 1))
    assert not storable("checkout", "")
    assert not storable("checkout", releases.OTHER_VERSION)


# --------------------------------------------------------------- coverage, AC73

FINDING: Final = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
DAY: Final = timedelta(days=1)


@pytest.mark.parametrize(
    ("reported", "seen", "value"),
    [
        (FINDING - 3 * DAY, FINDING - 2 * DAY, releases.DEPLOYS_AND_VERSIONS),
        (FINDING - 3 * DAY, None, releases.DEPLOYS_ONLY),
        (None, FINDING - 2 * DAY, releases.VERSIONS_ONLY),
        (None, None, releases.COVERAGE_NONE),
    ],
)
def test_coverage_takes_each_of_its_four_values(
    reported: datetime | None, seen: datetime | None, value: str
) -> None:
    """AC73, spec *A service's coverage*."""
    found = releases.coverage_of(reported, seen, at=FINDING, last_reported_at=reported)
    assert found.value == value
    assert found.first_heard_at is None


@pytest.mark.parametrize("days", [29, 31])
def test_a_report_29_or_31_days_old_gives_the_same_value_and_its_age(days: int) -> None:
    """AC73: no silent 30-day flip; the last report's date always shows with it."""
    last = FINDING - days * DAY
    found = releases.coverage_of(last, None, at=FINDING, last_reported_at=last)
    assert (found.value, found.last_reported_at) == (releases.DEPLOYS_ONLY, last)


def test_reports_and_versions_first_heard_after_the_moment_say_so() -> None:
    """AC73: a finding older than the first report reads "first heard", not none."""
    later = FINDING + 2 * DAY
    found = releases.coverage_of(later, FINDING + DAY, at=FINDING, last_reported_at=None)
    assert (found.value, found.first_heard_at) == (releases.COVERAGE_NONE, FINDING + DAY)


def test_a_version_counted_after_the_moment_does_not_count_yet() -> None:
    """Coverage is as of a moment: a report before it, a version only after it."""
    found = releases.coverage_of(
        FINDING - DAY, FINDING + DAY, at=FINDING, last_reported_at=FINDING - DAY
    )
    assert (found.value, found.first_heard_at) == (releases.DEPLOYS_ONLY, None)


def test_the_time_method_s_moment_is_a_reported_period_s_start() -> None:
    """Design 4, step 6, with the founder's answer of 2026-11-09: never a release's first
    report, which may be a failed deploy or an earlier period's."""
    assert releases.reported_moment(FINDING, releases.REPORTED) == FINDING
    assert releases.reported_moment(FINDING, releases.SEEN) is None
