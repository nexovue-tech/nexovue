"""Anomaly detection's vocabulary: the words the scheduler stores and the API reads (F6).

As with `alerts.model`, these tuples are what the database's CHECK constraints
allow. Migration 0041 spells its own frozen copy, and `test_alert_vocabulary.py`
holds the two equal.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, Literal, TypedDict

from nexovue_platform.alerts.channels import READ_BY_PEOPLE
from nexovue_platform.jobs import JobRequest

# A finding is about one service, or about the project as a whole when several of
# its services move together (FR15).
FINDING_SCOPES: Final = ("service", "project")

# Acknowledged is not a state: it marks an open finding as being handled (FR19).
FINDING_STATES: Final = ("open", "closed")

# How a finding closed (FR13, FR20, FR21, FR32, D20).
CLOSED_REASONS: Final = (
    "recovered",
    "false_alarm",
    "new_normal",
    "adopted",
    "service_gone",
    "could_not_be_judged",
)

# Losing traffic, failing more and slower (FR1 to FR3), as the simulation names them.
SIGNALS: Final = ("traffic", "failing", "slower")
Signal = Literal["traffic", "failing", "slower"]

# What moved a signal's normal: a person's *this is the new normal*, or adoption
# after 7 days open (FR21).
SHIFT_REASONS: Final = ("new_normal", "adopted")

# A finding nobody acted on becomes its service's new normal after this long, unless
# D22 says it never may (FR21, *Product values*).
ADOPTION_AFTER_DAYS: Final = 7

# Per project (FR18). Per-service sensitivity is deferred (D32).
SENSITIVITIES: Final = ("low", "normal", "high")
DEFAULT_SENSITIVITY: Final = "normal"

# What a project's time zone is when nobody's is known (FR24).
DEFAULT_TIMEZONE: Final = "UTC"

# Why the receiver refused a minute's telemetry for lack of a store (C5).
INTAKE_REFUSAL_REASONS: Final = ("broker_unavailable",)

# A project detection is expected to judge is *not running* once its job has not
# completed for this long (design 2.1, item 2; FR25, AC26). The API's status and
# the scheduler's install-wide count of projects behind read it alike.
NOT_RUNNING_AFTER: Final = timedelta(minutes=3)

# A project judges at most this many services, its busiest; the rest read *too many
# services* (design, *Security, tenancy, and privacy*). A starting cap, measured in
# AC29. The scheduler applies it and the API names it.
MAX_JUDGED_SERVICES: Final = 500
# Under `detection_state.signals`, written by the project job and read by the API:
# since when a service has been past the cap, and the last judged window's requests
# (*Inputs*: a service with none, and a normal that expects too few to judge, says
# *Not judged: no incoming requests*).
TOO_MANY_SERVICES_KEY: Final = "too_many_services_since"
WINDOW_REQUESTS_KEY: Final = "window_requests"

# Detection's queue and its baseline job, named here so the API can ask for a
# service's normal to be rebuilt the moment a person moves it, without importing
# scheduler code (as Run now does, ADR-011).
DETECTION_QUEUE: Final = "detection"
COMPUTE_BASELINES_TASK: Final = "nexovue_scheduler.detection.compute_baselines"

# The baseline job is pure Python over up to two hours of minutes for every service
# of a project; a queued one older than the hourly run's own reach is dropped.
BASELINE_SOFT_LIMIT_SECONDS: Final = 300
BASELINE_HARD_LIMIT_SECONDS: Final = 330
BASELINE_EXPIRES_SECONDS: Final = 3600


def compute_baselines_request(project_id: Any, org_id: Any) -> JobRequest:
    """One project's baseline job, as the hourly sweep, the project job and the API
    all queue it. A second queued while one runs finds the project locked and does
    nothing."""
    return JobRequest(
        task=COMPUTE_BASELINES_TASK,
        queue=DETECTION_QUEUE,
        kwargs={"project_id": str(project_id)},
        org_id=str(org_id),
        expires_seconds=BASELINE_EXPIRES_SECONDS,
        soft_limit_seconds=BASELINE_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=BASELINE_HARD_LIMIT_SECONDS,
    )


# FR22: a mute lasts at most 30 days (BR9), a snooze at most 4 hours (D25).
MAX_MUTE: Final = timedelta(days=30)
MAX_SNOOZE: Final = timedelta(hours=4)


@dataclass(frozen=True, slots=True)
class MinimumChange:
    """One sensitivity's minimum change for each signal (spec, *Product values*): how
    far a window must move from its normal before it can be unusual."""

    slower_ratio: float
    # Percentage points above the normal share failing.
    failing_points: float
    # The share of its normal a window's traffic must fall to.
    traffic_ratio: float


# Keyed by the window length, which is also the sustain time: a reading records its
# window, and so which sensitivity judged it. `method.SENSITIVITIES` is held equal.
MINIMUM_CHANGES: Final[Mapping[int, MinimumChange]] = {
    10: MinimumChange(slower_ratio=2.0, failing_points=5.0, traffic_ratio=0.30),
    5: MinimumChange(slower_ratio=1.5, failing_points=2.0, traffic_ratio=0.50),
    3: MinimumChange(slower_ratio=1.25, failing_points=1.0, traffic_ratio=0.70),
}

# ----------------------------------------------------------- moving a normal

# FR21: a shift's ratio is the median, over the last readings of the finding, of
# each reading's value over the normal it was judged by (design, *Level shifts*).
SHIFT_READINGS: Final = 60
# Fewer readings than this with a value for the signal, and the new level is not
# yet known well enough to become the normal: three windows at normal sensitivity.
SHIFT_READINGS_NEEDED: Final = 15
# The `detection_readings` columns a ratio is taken from.
SHIFT_COLUMNS: Final = (
    "window_minutes",
    "window_requests",
    "window_errors",
    "window_p95_ms",
    "traffic_median",
    "failing_median",
    "slower_median",
)
_PERCENT: Final = 100.0
_ANSCOMBE_SHIFT: Final = 0.375


@dataclass(frozen=True, slots=True)
class SignalReading:
    """One reading of one signal in its unit: the window's value, and its normal."""

    value: float
    normal: float


def signal_reading(signal: Signal, reading: Mapping[str, Any]) -> SignalReading | None:
    """A reading's value and normal for `signal`: the p95 in ms, the share failing in
    percent, or requests a minute. None when either is missing, so a reading with no
    p95, no requests or no normal never stands in for a real value."""
    minutes = int(reading["window_minutes"])
    requests = int(reading["window_requests"])
    if signal == "slower":
        value, normal = reading["window_p95_ms"], reading["slower_median"]
        if value is None or normal is None or normal <= 0:
            return None
        return SignalReading(float(value), float(normal))
    if signal == "failing":
        normal = reading["failing_median"]
        if not requests or normal is None:
            return None
        return SignalReading(
            _PERCENT * int(reading["window_errors"]) / requests, _PERCENT * float(normal)
        )
    median = reading["traffic_median"]
    if not minutes or median is None:
        return None
    # The median is on the square-root (Anscombe) scale; back to a count, as the
    # project job's `TrafficBaseline.expected` does.
    expected = max((float(median) / 2) ** 2 - _ANSCOMBE_SHIFT, 0.0)
    return SignalReading(requests / minutes, expected / minutes)


def shift_ratio(signal: Signal, readings: Sequence[Mapping[str, Any]]) -> float | None:
    """FR21's ratio for one signal, from readings newest first: the median of each
    value over its normal across the last `SHIFT_READINGS`. None until
    `SHIFT_READINGS_NEEDED` of them have both."""
    ratios = []
    for reading in readings[:SHIFT_READINGS]:
        found = signal_reading(signal, reading)
        if found is not None and found.normal > 0:
            ratios.append(found.value / found.normal)
    if len(ratios) < SHIFT_READINGS_NEEDED:
        return None
    ratio = statistics.median(ratios)
    # A traffic level of zero is a stopped service, never a normal (FR32).
    return ratio if ratio > 0 and math.isfinite(ratio) else None


# ------------------------------------------------------------ closing message

# The messages that tell a channel a finding exists: each channel told one of
# these is told how it closed (AC22), whatever the rules have become since.
FINDING_TOLD_KINDS: Final = ("opened", "escalated", "joined", "flapping")

# One *resolved* per channel told anything about the finding, written in the
# caller's transaction by whichever closes it: the project job, or a person in the
# API (FR20, FR21). `quiet` (muted, snoozed or flapping) leaves out the channels
# people read, so only the machines holding an alert hear it (F21 D8). `skip`
# leaves out channels told another way, such as the owners' adoption mail.
RESOLVE_FINDING_SQL: Final = """
INSERT INTO notifications
    (org_id, project_id, finding_id, channel_id, kind, sequence, severity,
     deliver_after, created_at)
SELECT DISTINCT told.org_id, told.project_id, told.finding_id, told.channel_id,
       'resolved', 0, CAST(:severity AS text), CAST(:at AS timestamptz),
       CAST(:at AS timestamptz)
FROM notifications AS told
JOIN notification_channels AS channels
    ON channels.id = told.channel_id AND channels.org_id = told.org_id
WHERE told.org_id = :org_id AND told.finding_id = :finding_id
  AND told.kind = ANY(CAST(:told_kinds AS text[]))
  AND NOT (CAST(:quiet AS boolean) AND channels.kind = ANY(CAST(:people AS text[])))
  AND told.channel_id <> ALL(CAST(:skip AS uuid[]))
ON CONFLICT (finding_id, channel_id, kind, sequence) DO NOTHING
RETURNING channel_id
"""


class FindingSignal(TypedDict):
    """One entry of `findings.signals`, a list with the leading signal first: what the
    scheduler writes and the API reads (AC40).

    The unit follows from the signal and is not stored: *slower* is the p95 in ms,
    *failing* the share of requests failing in percent, *traffic* requests a minute.
    A missing value is None, never 0: a missing number and a real zero differ.
    """

    signal: Signal
    normal: float | None
    current: float | None
    worst: float | None
    # What it was compared with: "Tuesdays around 15:00, last 3 weeks".
    comparison: str
    # The evidence endpoint (FR3), when one is named.
    endpoint: str | None


def finding_signal(
    signal: Signal,
    *,
    normal: float | None,
    current: float | None,
    worst: float | None,
    comparison: str,
    endpoint: str | None = None,
) -> FindingSignal:
    """A `findings.signals` entry, every key present."""
    if signal not in SIGNALS:
        raise ValueError(f"{signal!r} is not a detection signal")
    return FindingSignal(
        signal=signal,
        normal=normal,
        current=current,
        worst=worst,
        comparison=comparison,
        endpoint=endpoint,
    )


def resolve_parameters(
    *,
    org_id: Any,
    finding_id: Any,
    severity: str,
    at: Any,
    quiet: bool,
    skip: Sequence[Any] = (),
) -> dict[str, Any]:
    """`RESOLVE_FINDING_SQL`'s bound values, spelled once for both callers."""
    return {
        "org_id": org_id,
        "finding_id": finding_id,
        "severity": severity,
        "at": at,
        "told_kinds": list(FINDING_TOLD_KINDS),
        "quiet": quiet,
        "people": sorted(READ_BY_PEOPLE),
        "skip": list(skip),
    }


# The audit log's words for a finding, shared because the scheduler records an
# adoption as the system while the API records what people did (FR21).
FINDING_AUDIT_TARGET: Final = "finding"
ADOPTED_AUDIT_ACTION: Final = "finding.adopted"
