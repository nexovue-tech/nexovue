"""What a probe measured, before it becomes OTLP.

Keeping this separate from both the probing and the publishing means the shape
the contract expects is stated once, in plain Python, and can be asserted
without a network or a broker.

The phase names are not ours to choose. `packages/contracts/fixtures/
door2-synthetic-http.bin` is the frozen example of this door, and it carries
exactly five steps whose durations sum to the total. Both facts are asserted in
`test_http_check.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

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
class ProbeResult:
    """One completed check, whether it succeeded or not.

    A failed check is still a measurement and is still published — silence is
    indistinguishable from "nobody looked", which is the thing a monitoring
    product must never be ambiguous about.
    """

    check_id: str
    check_type: str
    target: str

    # True when the request completed and the status was not an error. A probe
    # that could not connect at all reports False with an empty `phases`.
    up: bool
    status_code: int | None
    total_seconds: float
    phases: dict[str, float]
    certificate: Certificate | None = None

    # Set when the probe failed, for the log. It is deliberately not published
    # as an attribute: libcurl's messages are unbounded text and would become a
    # high-cardinality label on a time series.
    error: str | None = None
