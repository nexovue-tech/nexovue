"""What the fault bed injects, what it records, and the thresholds it scores against (F6, T8.1).

A recorded run is a JSON Lines file, one event per line, appended as the run goes. It holds
everything scoring needs, so `fault_score.py` reads only this file and never the demo or the API.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

Signal = Literal["slower", "failing", "traffic"]
SIGNALS: Final[tuple[Signal, ...]] = ("slower", "failing", "traffic")

# The demo release `tools/otel-demo/otel-demo.sh` pins. Its flag names are this release's.
DEMO_VERSION: Final = "3.0.0"

# The spec's normal-sensitivity minimum changes (*Product values*) and the requests each signal
# needs before it can be judged at all.
SLOWER_MIN_RATIO: Final = 1.5
FAILING_MIN_POINTS: Final = 0.02
FAILING_MIN_FAILURES: Final = 5
TRAFFIC_MAX_SHARE: Final = 0.5
SLOWER_MIN_REQUESTS: Final = 200
FAILING_MIN_REQUESTS: Final = 20
TRAFFIC_MIN_EXPECTED: Final = 47
# AC36 and D19: a fault counts only when its first full window clears the minimum change by a
# tenth of that minimum, so 1.55x slower, +2.2 points, or traffic at 45% of normal or less.
CLEAR_MARGIN: Final = 0.1
# Normal sensitivity's window: the first full one after an injection starts is what AC36 measures.
WINDOW: Final = timedelta(minutes=5)
# The flag reaches a service within seconds, so the measured window starts at the next whole
# minute after this, never the minute the flag was turned on.
PROPAGATION: Final = timedelta(minutes=1)
# D16: time to catch is 12 minutes at 200 requests a minute or more, 20 below that.
BUSY_RPM: Final = 200.0
CATCH_BUSY: Final = timedelta(minutes=12)
CATCH_QUIET: Final = timedelta(minutes=20)
# AC36: caught on at least 4 of 5.
ROUNDS: Final = 5
REQUIRED_SHARE: Final = 0.8
# The reference run is the week before the injections, so every injection has the same period of
# the reference run exactly one week earlier.
REFERENCE: Final = timedelta(weeks=1)


@dataclass(frozen=True, slots=True)
class Fault:
    """One fault the bed injects, and what it was expected to move (FR27 needs the expectation)."""

    name: str
    flag: str
    variant: str
    expected_service: str
    expected_signal: Signal
    # Set for the traffic fault: the flag's value while injecting, as a share of its value before.
    scale_of_default: float | None = None


# Expectations are read from each flag's description in 3.0.0's demo.flagd.json and the demo's
# code paths; none was observed on nexovue yet, so the run's readings, not this table, decide moves.
FAULTS: Final[tuple[Fault, ...]] = (
    Fault("adFailure", "adFailure", "on", "ad", "failing"),
    Fault("adHighCpu", "adHighCpu", "on", "ad", "slower"),
    Fault("adManualGc", "adManualGc", "on", "ad", "slower"),
    Fault("cartFailure", "cartFailure", "10%", "cart", "failing"),
    Fault("emailMemoryLeak", "emailMemoryLeak", "100x", "email", "slower"),
    # A Kubernetes readiness probe: the demo's compose files declare none, so this is expected to
    # be a gap here. It is injected anyway, so the gap is measured rather than assumed.
    Fault("failedReadinessProbe", "failedReadinessProbe", "on", "cart", "traffic"),
    # Envoy in frontend-proxy delays /images/ by a header the storefront sets from the flag.
    Fault("imageSlowLoad", "imageSlowLoad", "5sec", "frontend-proxy", "slower"),
    Fault("intlShippingSlowdown", "intlShippingSlowdown", "5sec", "shipping", "slower"),
    Fault("kafkaQueueProblems", "kafkaQueueProblems", "on", "fraud-detection", "slower"),
    Fault("paymentFailure", "paymentFailure", "10%", "payment", "failing"),
    Fault("paymentUnreachable", "paymentUnreachable", "on", "checkout", "failing"),
    Fault("productCatalogFailure", "productCatalogFailure", "on", "product-catalog", "failing"),
    Fault(
        "recommendationCacheFailure", "recommendationCacheFailure", "on", "recommendation", "slower"
    ),
    # D19: losing traffic is injected at 40% of normal, clearly past the 50% minimum. The demo has
    # no traffic fault, so the load generator's HTTP users are cut to 40% of what they were.
    Fault(
        "trafficAt40",
        "loadGeneratorVUs",
        "faultbed-40pct",
        "frontend-proxy",
        "traffic",
        scale_of_default=0.4,
    ),
)


@dataclass(frozen=True, slots=True)
class Injection:
    """One planned injection: a fault on from `start` to `end`."""

    fault: str
    round: int
    start: datetime
    end: datetime

    @property
    def window(self) -> tuple[datetime, datetime]:
        """AC36's first full window: whole minutes, starting after the flag has propagated."""
        first = _ceil_minute(self.start + PROPAGATION)
        return first, first + WINDOW


@dataclass(frozen=True, slots=True)
class Plan:
    project_id: str
    demo_version: str
    reference_start: datetime
    # Each fault's flag variant while not injecting, read from the flag file when planned.
    resting: dict[str, str]
    injections: tuple[Injection, ...]

    @property
    def reference_end(self) -> datetime:
        return self.reference_start + REFERENCE


@dataclass(frozen=True, slots=True)
class ServiceWindow:
    """One service's requests, failures and p95 over a window, as `GET /services` reports them."""

    service: str
    requests: int
    errors: int
    p95_ms: float


@dataclass(frozen=True, slots=True)
class Observation:
    """A finished injection: the fault's first full window and the same window one week earlier."""

    fault: str
    round: int
    during: tuple[ServiceWindow, ...]
    reference: tuple[ServiceWindow, ...]


@dataclass(frozen=True, slots=True)
class Skipped:
    fault: str
    round: int
    reason: str


@dataclass(frozen=True, slots=True)
class FindingSeen:
    """A finding as the API returned it; `opened_at` is when its row was written."""

    id: str
    opened_at: datetime
    scope: str
    services: tuple[str, ...]
    signals: tuple[str, ...]
    severity: str
    closed_reason: str | None


@dataclass(frozen=True, slots=True)
class RecordedRun:
    plan: Plan
    observations: tuple[Observation, ...]
    skipped: tuple[Skipped, ...]
    findings: tuple[FindingSeen, ...]
    # Every service that reported during the reference week, for the false-alarm rate's denominator.
    reference_services: tuple[str, ...] | None


def opened_at(finding_id: str) -> datetime:
    """When a finding opened. `findings.id` defaults to Postgres's `uuidv7()`, whose first 48
    bits are the insert time in milliseconds. The API's `started_at` is the onset instead."""
    parsed = uuid.UUID(finding_id)
    if parsed.version != 7:
        raise ValueError(f"finding id {finding_id} is not a UUIDv7, so its open time is unknown")
    return datetime.fromtimestamp((parsed.int >> 80) / 1000, UTC)


def fault_named(name: str) -> Fault:
    for fault in FAULTS:
        if fault.name == name:
            return fault
    raise KeyError(f"no fault named {name!r}")


def catch_limit(reference_rpm: float) -> timedelta:
    return CATCH_BUSY if reference_rpm >= BUSY_RPM else CATCH_QUIET


def _ceil_minute(at: datetime) -> datetime:
    floor = at.replace(second=0, microsecond=0)
    return floor if floor == at else floor + timedelta(minutes=1)


# ---------- the run log ----------


def _time(at: datetime) -> str:
    if at.tzinfo is None:
        raise ValueError("every time in a run log is timezone-aware")
    return at.astimezone(UTC).isoformat()


def _parse_time(text: str) -> datetime:
    at = datetime.fromisoformat(text)
    if at.tzinfo is None:
        raise ValueError(f"time {text!r} in the run log has no zone")
    return at


def plan_event(plan: Plan) -> dict[str, Any]:
    return {
        "kind": "plan",
        "project_id": plan.project_id,
        "demo_version": plan.demo_version,
        "reference_start": _time(plan.reference_start),
        "resting": plan.resting,
        "injections": [
            {"fault": i.fault, "round": i.round, "start": _time(i.start), "end": _time(i.end)}
            for i in plan.injections
        ],
    }


def _window_json(window: ServiceWindow) -> dict[str, Any]:
    return {
        "service": window.service,
        "requests": window.requests,
        "errors": window.errors,
        "p95_ms": window.p95_ms,
    }


def observation_event(observation: Observation) -> dict[str, Any]:
    return {
        "kind": "injection",
        "fault": observation.fault,
        "round": observation.round,
        "during": [_window_json(w) for w in observation.during],
        "reference": [_window_json(w) for w in observation.reference],
    }


def skipped_event(skipped: Skipped) -> dict[str, Any]:
    return {
        "kind": "skipped",
        "fault": skipped.fault,
        "round": skipped.round,
        "reason": skipped.reason,
    }


def findings_event(at: datetime, findings: Iterable[FindingSeen]) -> dict[str, Any]:
    return {
        "kind": "findings",
        "at": _time(at),
        "findings": [
            {
                "id": f.id,
                "scope": f.scope,
                "services": list(f.services),
                "signals": list(f.signals),
                "severity": f.severity,
                "closed_reason": f.closed_reason,
            }
            for f in findings
        ],
    }


def reference_services_event(services: Iterable[str]) -> dict[str, Any]:
    return {"kind": "reference_services", "services": sorted(services)}


def flag_event(at: datetime, flag: str, variant: str) -> dict[str, Any]:
    """An audit line only: scoring reads the plan's times, not these."""
    return {"kind": "flag", "at": _time(at), "flag": flag, "variant": variant}


def append(path: Path, event: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(event, separators=(",", ":")) + "\n")


def events(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as log:
        for number, line in enumerate(log, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{number} is not JSON: {error}") from error
            if not isinstance(event, dict) or "kind" not in event:
                raise ValueError(f"{path}:{number} is not a run-log event")
            yield event


def _windows(raw: list[dict[str, Any]]) -> tuple[ServiceWindow, ...]:
    return tuple(
        ServiceWindow(str(w["service"]), int(w["requests"]), int(w["errors"]), float(w["p95_ms"]))
        for w in raw
    )


def _plan(event: dict[str, Any]) -> Plan:
    return Plan(
        project_id=str(event["project_id"]),
        demo_version=str(event["demo_version"]),
        reference_start=_parse_time(event["reference_start"]),
        resting={str(k): str(v) for k, v in event["resting"].items()},
        injections=tuple(
            Injection(
                str(i["fault"]), int(i["round"]), _parse_time(i["start"]), _parse_time(i["end"])
            )
            for i in event["injections"]
        ),
    )


def load(raw: Iterable[dict[str, Any]]) -> RecordedRun:
    """A run log's events as one recorded run. A finding seen in several snapshots keeps its
    latest state; an injection recorded twice is an error, because it was injected twice."""
    plan: Plan | None = None
    observations: dict[tuple[str, int], Observation] = {}
    skipped: dict[tuple[str, int], Skipped] = {}
    findings: dict[str, FindingSeen] = {}
    reference_services: tuple[str, ...] | None = None
    for event in raw:
        kind = event["kind"]
        if kind == "plan":
            if plan is not None:
                raise ValueError("the run log holds two plans")
            plan = _plan(event)
        elif kind == "injection":
            key = (str(event["fault"]), int(event["round"]))
            if key in observations:
                raise ValueError(f"{key[0]} round {key[1]} is recorded twice")
            observations[key] = Observation(
                key[0], key[1], _windows(event["during"]), _windows(event["reference"])
            )
        elif kind == "skipped":
            key = (str(event["fault"]), int(event["round"]))
            skipped[key] = Skipped(key[0], key[1], str(event["reason"]))
        elif kind == "findings":
            for f in event["findings"]:
                findings[str(f["id"])] = FindingSeen(
                    id=str(f["id"]),
                    opened_at=opened_at(str(f["id"])),
                    scope=str(f["scope"]),
                    services=tuple(str(s) for s in f["services"]),
                    signals=tuple(str(s) for s in f["signals"]),
                    severity=str(f["severity"]),
                    closed_reason=None if f["closed_reason"] is None else str(f["closed_reason"]),
                )
        elif kind == "reference_services":
            reference_services = tuple(str(s) for s in event["services"])
        elif kind != "flag":
            raise ValueError(f"unknown run-log event {kind!r}")
    if plan is None:
        raise ValueError("the run log has no plan")
    return RecordedRun(
        plan=plan,
        observations=tuple(observations.values()),
        skipped=tuple(skipped.values()),
        findings=tuple(sorted(findings.values(), key=lambda f: f.opened_at)),
        reference_services=reference_services,
    )


def scaled_value(default_value: float, fraction: float) -> int:
    """The traffic fault's flag value: a share of the resting value, never below one user."""
    return max(1, math.floor(default_value * fraction + 0.5))
