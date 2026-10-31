"""Anomaly detection's vocabulary: the words the scheduler stores and the API reads (F6).

As with `alerts.model`, these tuples are what the database's CHECK constraints
allow. Migration 0041 spells its own frozen copy, and `test_alert_vocabulary.py`
holds the two equal.
"""

from __future__ import annotations

from typing import Final, Literal, TypedDict

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

# Per project (FR18). Per-service sensitivity is deferred (D32).
SENSITIVITIES: Final = ("low", "normal", "high")
DEFAULT_SENSITIVITY: Final = "normal"

# What a project's time zone is when nobody's is known (FR24).
DEFAULT_TIMEZONE: Final = "UTC"

# Why the receiver refused a minute's telemetry for lack of a store (C5).
INTAKE_REFUSAL_REASONS: Final = ("broker_unavailable",)


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
