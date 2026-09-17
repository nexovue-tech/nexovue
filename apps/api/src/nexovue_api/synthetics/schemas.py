"""What the synthetic check endpoints put on the wire.

Durations are milliseconds here and seconds in the store. The conversion
happens once, in the service, so no response mixes the two.
"""

from __future__ import annotations

from typing import Final

from nexovue_platform.checks.evidence import RunEvidence
from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp

# The order a request happens in, which is the order a stacked chart must plot.
# A phase the prober adds later is served after these rather than dropped, so a
# new step reaches the UI without a change here.
PHASE_ORDER: Final = ("dns", "tcp", "tls", "ttfb", "transfer")


class TlsCertificate(BaseModel):
    """The certificate the last probe saw, and how long it has left."""

    days_remaining: float
    issuer: str
    # From the certificate reading, so it is the host the TLS handshake reached.
    # The check's configured target lives in the checks API, not here.
    server_address: str


class CheckResultSummary(BaseModel):
    """One check's readings over the window: is it up, and how slow is it."""

    check_id: str
    check_type: str
    runs: int
    successful_runs: int
    # 0 to 100. Zero runs is 0.0 rather than null: a check with nothing in the
    # window is reported by `runs`, and a nullable percentage would have every
    # caller writing the same branch.
    uptime_percent: float
    # Null when no probe reported a status in the window.
    up: bool | None = None
    # The most recent confirmed run's outcome — including one uptime does not
    # count, so a misconfigured check says so. Empty for readings from before
    # outcomes were published.
    latest_outcome: str = ""
    latest_status_code: int
    latest_run_at: IsoTimestamp | None = None
    # Null when nothing in the window was timed: a heartbeat is never probed, and
    # a run refused before its request has no response time. A zero would read as
    # an instant answer and drag every percentile down with it.
    mean_duration_ms: float | None = None
    p95_duration_ms: float | None = None
    max_duration_ms: float | None = None
    # Null for a check whose target is plain HTTP, or one never probed here.
    tls: TlsCertificate | None = None


class CheckResultListResponse(BaseModel):
    checks: list[CheckResultSummary]


class PhaseBucket(BaseModel):
    """One time bucket of the phase breakdown, ready to stack."""

    timestamp: IsoTimestamp
    # Every phase named in the response's `phases`, so a chart never has to
    # decide what a missing key means. A phase with no reading is 0.0.
    durations_ms: dict[str, float] = Field(default_factory=dict)
    total_ms: float
    samples: int


class PhaseBreakdownResponse(BaseModel):
    """Which part of the request was slow, bucketed over time.

    This is the reading that makes a synthetic check worth more than a ping:
    a duration that doubled is an incident, and `dns` against `tls` against
    `ttfb` is the difference between whose incident it is.
    """

    check_id: str
    bucket_seconds: int
    # Request order, and the key set of every bucket's `durations_ms`.
    phases: list[str]
    buckets: list[PhaseBucket]


class CheckRun(BaseModel):
    """One attempt. The uptime strip, rather than the average over it."""

    timestamp: IsoTimestamp
    # Empty for readings published before runs carried an id; such a run has no
    # evidence to open.
    run_id: str
    up: bool
    status_code: int
    # Null when this run timed nothing — a heartbeat, or a run that never sent
    # its request. Not zero: that is a real, instant response.
    duration_ms: float | None = None
    # The phases summed. It tracks `duration_ms` closely; a gap between them is
    # time the probe spent outside the phases it measured. Null when no phase was
    # measured.
    phase_total_ms: float | None = None
    outcome: str
    attempt: int
    # False for an attempt a retry overruled; it is shown, and not counted.
    final: bool
    maintenance: bool
    location: str


class CheckRunListResponse(BaseModel):
    check_id: str
    # Newest first.
    runs: list[CheckRun]


class CheckTrendPoint(BaseModel):
    """One bucket of a check's history."""

    bucket: IsoTimestamp
    runs: int
    successful_runs: int
    # Null when no run in this bucket succeeded: a check that failed has no response
    # time, and a zero here would be drawn as the site getting instantly fast.
    mean_duration_ms: float | None = None


class CheckTrend(BaseModel):
    """One check's history across the window, oldest first."""

    check_id: str
    points: list[CheckTrendPoint]


class CheckTrendResponse(BaseModel):
    """What each row's sparkline is drawn from.

    `bucket_seconds` is returned rather than assumed: the server chooses it from the
    window length and the checks' own interval, so a client plotting against a fixed
    minute would mis-scale its axis.
    """

    window_start: IsoTimestamp
    window_end: IsoTimestamp
    bucket_seconds: int
    checks: list[CheckTrend] = Field(default_factory=list)


class CheckRunEvidenceResponse(BaseModel):
    evidence: RunEvidence


class CheckRunCount(BaseModel):
    check_id: str
    runs: int
