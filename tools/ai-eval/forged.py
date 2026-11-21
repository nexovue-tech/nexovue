"""`--run forged`: forged spans through the real intake (plan T2.16; spec AC76; design, *Injection
suite in the gate*).

A forged span naming a real service, timed with an outage, can still produce a believable false
cause and can open a finding. It cannot be stopped outright, so the bed **measures** it with no
model: for each kind of forgery, in two arms,

- *during an outage* (the service is already unhealthy, with no real cause planted): how often each
  rule names a cause, which is false by construction, since the outage has no deploy and no
  dependency problem; and
- *with no outage*: how often the forged spans open a finding at all.

The forged-cause counts have no bar of their own: a false cause counts as a wrong cause for its rule
inside AC62's bar (A82; `score.rule_readings`). Spans go to the real receiver with a real ingest
key, and what is observed is what the evidence job reads. Dev stack and test installs only.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal
from urllib.parse import urlparse

from nexovue_scheduler.evidence.rules import Cause

Forgery = Literal["edge_failures", "new_version", "new_error", "traffic_burst"]
FORGERIES: Final[tuple[Forgery, ...]] = (
    "edge_failures",
    "new_version",
    "new_error",
    "traffic_burst",
)
# Which rule each forgery is aimed at (design, *The rules*): what it takes to push a cause.
AIMED_AT: Final[dict[Forgery, str]] = {
    "edge_failures": "dependency_edge",
    "new_version": "seen_version",
    "new_error": "new_error",
    "traffic_burst": "traffic_rose",
}
FORGED_CALLEE: Final = "forged-callee"
FORGED_VERSION: Final = "forged-9.9.9"
FORGED_EXCEPTION: Final = "ForgedError"
# Spans in one forged export: enough calls for an edge to be judged (`EDGE_MIN_CALLS` is 30).
SPANS_PER_EXPORT: Final = 60
LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
_ENDPOINT: Final = "GET /forged"

Post = Callable[[bytes], int]


@dataclass(frozen=True, slots=True)
class Spec:
    """One forged export: which forgery, against which real service, at what minute."""

    forgery: Forgery
    service: str
    at: datetime
    outage: bool
    count: int = SPANS_PER_EXPORT


def export_request(spec: Spec, seed: int = 1) -> bytes:
    """The OTLP/HTTP protobuf body for `spec`, as any client with an ingest key could send it."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

    rng = random.Random(seed)  # ids for a forged export, never a secret
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    name = resource_spans.resource.attributes.add()
    name.key, name.value.string_value = "service.name", spec.service
    if spec.forgery == "new_version":
        version = resource_spans.resource.attributes.add()
        version.key, version.value.string_value = "service.version", FORGED_VERSION
    scope = resource_spans.scope_spans.add()
    start_ns = int(spec.at.timestamp() * 1e9)
    for _ in range(spec.count):
        span = scope.spans.add()
        span.trace_id, span.span_id = rng.randbytes(16), rng.randbytes(8)
        span.name = _ENDPOINT
        span.start_time_unix_nano = start_ns
        span.end_time_unix_nano = start_ns + 40_000_000
        if spec.forgery == "edge_failures":
            span.kind = Span.SPAN_KIND_CLIENT
            peer = span.attributes.add()
            peer.key, peer.value.string_value = "peer.service", FORGED_CALLEE
            span.status.code = Status.STATUS_CODE_ERROR
        else:
            span.kind = Span.SPAN_KIND_SERVER
        if spec.forgery == "new_error":
            span.status.code = Status.STATUS_CODE_ERROR
            event = span.events.add()
            event.name, event.time_unix_nano = "exception", start_ns
            kind = event.attributes.add()
            kind.key, kind.value.string_value = "exception.type", FORGED_EXCEPTION
    return request.SerializeToString()


def require_local(url: str) -> None:
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing receiver host {host}: this sends forged spans, dev only")


def poster(receiver: str, key: str) -> Post:
    """Posts one export to the real receiver, as an SDK would. Dev only."""
    require_local(receiver)
    import httpx

    client = httpx.Client(timeout=10.0)

    def post(body: bytes) -> int:
        return client.post(
            f"{receiver}/v1/traces",
            content=body,
            headers={"content-type": "application/x-protobuf", "x-nexovue-key": key},
        ).status_code

    return post


# ------------------------------------------------------------------ the measurement


@dataclass(frozen=True, slots=True)
class Observed:
    """What the evidence job read after a forged export: whether a finding opened, and the cause
    its facts gave."""

    finding_opened: bool
    cause: Cause | None


@dataclass(frozen=True, slots=True)
class Trial:
    spec: Spec
    accepted: bool
    observed: Observed | None


Observe = Callable[[Spec], Awaitable[Observed]]


async def run_trials(
    specs: Iterable[Spec], *, post: Post, observe: Observe, seed: int = 1
) -> list[Trial]:
    """Send each forged export and observe. A refused export is a trial of its own, with no
    observation: the intake stopped it, and the report counts that."""
    trials: list[Trial] = []
    for number, spec in enumerate(specs):
        accepted = post(export_request(spec, seed + number)) < 300
        trials.append(Trial(spec, accepted, await observe(spec) if accepted else None))
    return trials


@dataclass(slots=True)
class ArmCounts:
    trials: int = 0
    refused: int = 0
    findings_opened: int = 0
    false_causes: int = 0


@dataclass(frozen=True, slots=True)
class ForgedReport:
    arms: dict[tuple[Forgery, bool], ArmCounts]
    # False causes by the rule that stated them, during an outage (A82 feeds these to the bar).
    false_by_rule: dict[str, int]


def tally(trials: Sequence[Trial]) -> ForgedReport:
    arms: dict[tuple[Forgery, bool], ArmCounts] = defaultdict(ArmCounts)
    by_rule: dict[str, int] = defaultdict(int)
    for trial in trials:
        arm = arms[(trial.spec.forgery, trial.spec.outage)]
        arm.trials += 1
        if not trial.accepted or trial.observed is None:
            arm.refused += 1
            continue
        arm.findings_opened += trial.observed.finding_opened
        cause = trial.observed.cause
        if trial.spec.outage and cause is not None:
            arm.false_causes += 1
            by_rule[cause.rule] += 1
    return ForgedReport(dict(arms), dict(by_rule))


def render(report: ForgedReport) -> str:
    lines = [
        "| Forgery | Aimed at | Outage | Trials | Refused | Findings opened | False causes |",
        "|---|---|---|---|---|---|---|",
    ]
    for (forgery, outage), arm in sorted(report.arms.items()):
        lines.append(
            f"| {forgery} | {AIMED_AT[forgery]} | {'yes' if outage else 'no'} | {arm.trials} | "
            f"{arm.refused} | {arm.findings_opened} | {arm.false_causes if outage else 'n/a'} |"
        )
    if report.false_by_rule:
        lines += ["", "| Rule | False causes during an outage |", "|---|---|"]
        lines += [f"| {rule} | {count} |" for rule, count in sorted(report.false_by_rule.items())]
    return "\n".join(lines)


def specs_for(
    services: Sequence[str], *, start: datetime, repeats: int, spacing: timedelta
) -> list[Spec]:
    """Every forgery against every service, in both arms, `repeats` times, spaced apart so one
    forged export's finding is not another's context."""
    specs: list[Spec] = []
    moment = start
    for _ in range(repeats):
        for service in services:
            for forgery in FORGERIES:
                for outage in (True, False):
                    specs.append(Spec(forgery, service, moment, outage))
                    moment += spacing
    return specs
