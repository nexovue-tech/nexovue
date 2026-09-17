"""What a probe measured, before it becomes OTLP.

Keeping this separate from both the probing and the publishing means the shape
the contract expects is stated once, in plain Python, and can be asserted
without a network or a broker.

The phase names are not ours to choose. `packages/contracts/fixtures/
door2-synthetic-api.bin` is the frozen example of this door, and it carries
exactly five steps whose durations sum to the total.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from nexovue_platform.checks.evidence import HEALTHY_OUTCOMES, Outcome

# The five phases, in the order a request passes through them. `transfer` is the
# body arriving after the first byte; `ttfb` is the wait before it, which is
# where a slow server shows up as distinct from a slow network.
PHASE_DNS: Final = "dns"
PHASE_TCP: Final = "tcp"
PHASE_TLS: Final = "tls"
PHASE_TTFB: Final = "ttfb"
PHASE_TRANSFER: Final = "transfer"

PHASE_ORDER: Final = (PHASE_DNS, PHASE_TCP, PHASE_TLS, PHASE_TTFB, PHASE_TRANSFER)


@dataclass(frozen=True, slots=True)
class Certificate:
    """What the server presented, when the connection was TLS."""

    days_remaining: int
    issuer: str
    server_address: str


@dataclass(frozen=True, slots=True)
class HttpExchange:
    """One request and what came back — or why nothing did.

    A target that fails is still an exchange: the timings up to the failure are
    true, and knowing DNS resolved before the connection was refused is the
    diagnosis.
    """

    # None when no response arrived at all.
    status_code: int | None
    # The final response's headers, after any redirects.
    headers: list[tuple[str, str]]
    body: bytes
    # True when the body was longer than the probe keeps and was cut off.
    body_truncated: bool
    total_seconds: float
    phases: dict[str, float]
    certificate: Certificate | None
    # Where the request ended up, after redirects.
    effective_url: str
    # Why no response arrived. Never published as a metric attribute: it is
    # unbounded text and would become a high-cardinality label.
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RunMeasurement:
    """The numbers one attempt publishes, whatever kind of check it was."""

    check_id: str
    check_type: str
    run_id: str
    outcome: Outcome
    attempt: int
    final: bool
    maintenance: bool
    location: str
    # None when the run timed nothing: a heartbeat sweep, or a run refused
    # before a request was sent. Such a run publishes no duration at all.
    total_seconds: float | None
    status_code: int | None = None
    phases: dict[str, float] = field(default_factory=dict)
    certificate: Certificate | None = None

    @property
    def up(self) -> bool:
        return self.outcome in HEALTHY_OUTCOMES
