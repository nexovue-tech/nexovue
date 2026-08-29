"""What the synthetic check endpoints put on the wire.

Durations are milliseconds here and seconds in the store. The conversion
happens once, in the service, so no response mixes the two.
"""

from __future__ import annotations

from typing import Final

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
    latest_status_code: int
    latest_run_at: IsoTimestamp | None = None
    mean_duration_ms: float
    p95_duration_ms: float
    max_duration_ms: float
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
    """One probe. The uptime strip, rather than the average over it."""

    timestamp: IsoTimestamp
    up: bool
    status_code: int
    duration_ms: float
    # The phases summed. It tracks `duration_ms` closely; a gap between them is
    # time the probe spent outside the phases it measured.
    phase_total_ms: float


class CheckRunListResponse(BaseModel):
    check_id: str
    # Newest first.
    runs: list[CheckRun]
