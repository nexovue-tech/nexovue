"""What one run of a check saw, in the shape the console shows it.

The scheduler builds this, redacts it and publishes it as a log record, or hands
it back to a person who pressed Run now. The API reads the same shape back. It is
the answer to "what was sent, what came back, and which assertion failed".
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

Outcome = Literal["up", "degraded", "down", "misconfigured", "prober_error"]

HEALTHY_OUTCOMES: Final[frozenset[str]] = frozenset({"up", "degraded"})

# How much of a body is kept as evidence. Assertions read up to the probe's own
# cap; a failing one is explained by its first bytes far more often than its last.
MAX_CAPTURED_BODY_BYTES: Final = 16 * 1024


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapturedHeader(_Evidence):
    name: str
    value: str


class CapturedBody(_Evidence):
    text: str
    size_bytes: int
    truncated: bool
    # Not UTF-8, so `text` is a placeholder rather than the bytes.
    binary: bool = False


class SentRequest(_Evidence):
    method: str
    url: str
    headers: list[CapturedHeader] = []
    body: CapturedBody | None = None


class ReceivedResponse(_Evidence):
    status_code: int
    headers: list[CapturedHeader] = []
    body: CapturedBody | None = None


class AssertionResult(_Evidence):
    source: str
    property: str = ""
    comparison: str
    target: str = ""
    # What was found. None when there was nothing to find — no such header, no
    # match at the path — which is different from an empty string.
    actual: str | None = None
    passed: bool


class StepEvidence(_Evidence):
    """One request of a run, or the single probe of a TCP, DNS or heartbeat check."""

    position: int
    name: str = ""
    # The URL, host:port or domain name, redacted.
    target: str
    duration_ms: float | None = None
    phases_ms: dict[str, float] = {}
    request: SentRequest | None = None
    response: ReceivedResponse | None = None
    answers: list[str] = []
    assertions: list[AssertionResult] = []
    extracted: list[str] = []
    passed: bool
    error: str | None = None


class RunEvidence(_Evidence):
    run_id: str
    check_id: str | None
    check_type: str
    location: str
    attempt: Annotated[int, Field(ge=1)]
    final: bool
    maintenance: bool = False
    outcome: Outcome
    started_at: datetime
    # Absent when nothing was timed, for the same reason `status_code` is: a
    # heartbeat is never probed, and a misconfigured run never sent a request.
    # Zero would chart and sort as a real, instant response.
    duration_ms: float | None = None
    status_code: int | None = None
    tls_days_remaining: int | None = None
    steps: list[StepEvidence] = []
    # Why the run was misconfigured or could not be performed.
    error: str | None = None
