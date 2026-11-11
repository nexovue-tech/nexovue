"""Send releases through the real receiver, the way a customer's SDK sends them (F28 plan T1.5).

`tools/otel-demo` runs one version per service and `tools/detection-seed` writes history straight
into ClickHouse, so neither exercises the version rollup. This sends SERVER spans to `/v1/traces`
with `service.version` on the resource, shaped by a named mode, and keeps a ledger of exactly what
the receiver accepted, by service and version. `verify` then reads `service_versions_1m` and
`service_stats_1m` for the same project and window and prints both beside the ledger: the counts
must be equal, and the version rows must sum to the service rows (FR16).

Modes:

- `rolling`: `v1` hands traffic to `v2` over `--rollout-minutes`, with each version's failure rate;
- `swap`: `v1` to `v2` in one minute;
- `no-version`: no `service.version` at all, with the failure rate stepping at the same minute;
- `rollback`: a swap to `v2`, and back to `v1` after `--rollout-minutes`;
- `exceptions`: `rolling`, with `v2`'s failures carrying an exception only `v2` throws;
- `cold-start`: a swap whose new version is slow for its first `COLD_MINUTES`;
- `churn`: `--versions-per-minute` fresh build ids every minute on every service;
- `many-services`: `--services` services, each with one rollout an hour, staggered.

The webhook sender (`--deploys N`) comes with the new deploy body in plan T2.2.

Dev stack only. Run with `just release-demo <command>`; each command's options: `--help`.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_RECEIVER: Final = "http://localhost:14318"
DEFAULT_CLICKHOUSE: Final = "http://nexovue:nexovue_dev@localhost:18123/nexovue"

MODES: Final = (
    "rolling",
    "swap",
    "no-version",
    "rollback",
    "exceptions",
    "cold-start",
    "churn",
    "many-services",
)
OLD_VERSION: Final = "v1"
NEW_VERSION: Final = "v2"
# The new version is this much slower while it starts cold (FR48's warm-up is 5 minutes).
COLD_MINUTES: Final = 5
COLD_FACTOR: Final = 3.0
MEDIAN_MS: Final = 40.0
LATENCY_SIGMA: Final = 0.6
# Spans go out every few seconds, as an SDK's batch processor sends them.
SEND_EVERY_SECONDS: Final = 5.0
TICKS_PER_MINUTE: Final = int(60 / SEND_EVERY_SECONDS)
RETRIES: Final = 4
RETRYABLE: Final = frozenset({429, 502, 503, 504})
ENDPOINT: Final = "GET /items"
NEW_EXCEPTION: Final = "ReleaseDemoError"


@dataclass(frozen=True, slots=True)
class Settings:
    """What shapes a run. Minutes count from the run's start."""

    mode: str
    services: int = 1
    rpm: float = 60.0
    start_minute: int = 5
    rollout_minutes: int = 20
    old_failure: float = 0.002
    new_failure: float = 0.03
    versions_per_minute: int = 25


@dataclass(frozen=True, slots=True)
class Slice:
    """One version's part of one service's traffic in one minute. `version` None sends no
    `service.version` attribute at all."""

    version: str | None
    share: float
    failure_rate: float
    latency_factor: float = 1.0
    throws: bool = False


def service_names(settings: Settings) -> list[str]:
    if settings.mode == "many-services":
        return [f"svc-{index:03d}" for index in range(settings.services)]
    return (
        ["checkout"]
        if settings.services <= 1
        else [f"checkout-{index}" for index in range(settings.services)]
    )


def _new_share(minute: int, start: int, length: int) -> float:
    """How much of the traffic the new version holds, rising evenly from `start` over `length`."""
    if minute < start:
        return 0.0
    if length <= 0 or minute >= start + length:
        return 1.0
    return (minute - start + 1) / (length + 1)


def _two_versions(
    new_share: float, settings: Settings, *, cold: bool = False, throws: bool = False
) -> list[Slice]:
    slices = []
    if new_share < 1.0:
        slices.append(Slice(OLD_VERSION, 1.0 - new_share, settings.old_failure))
    if new_share > 0.0:
        slices.append(
            Slice(
                NEW_VERSION, new_share, settings.new_failure, COLD_FACTOR if cold else 1.0, throws
            )
        )
    return slices


def slices_for(settings: Settings, service_index: int, minute: int) -> list[Slice]:
    """Which versions one service serves in one minute, in what shares. Pure, so a test can read
    the shape of every mode without sending anything."""
    start, length = settings.start_minute, settings.rollout_minutes
    mode = settings.mode
    if mode in ("rolling", "exceptions"):
        return _two_versions(
            _new_share(minute, start, length), settings, throws=mode == "exceptions"
        )
    if mode == "swap":
        return _two_versions(_new_share(minute, start, 0), settings)
    if mode == "cold-start":
        cold = start <= minute < start + COLD_MINUTES
        return _two_versions(_new_share(minute, start, 0), settings, cold=cold)
    if mode == "rollback":
        back = minute >= start + length
        return _two_versions(0.0 if back else _new_share(minute, start, 0), settings)
    if mode == "no-version":
        rate = settings.new_failure if minute >= start else settings.old_failure
        return [Slice(None, 1.0, rate)]
    if mode == "churn":
        share = 1.0 / settings.versions_per_minute
        return [
            Slice(f"build-{minute:05d}-{index:03d}", share, settings.old_failure)
            for index in range(settings.versions_per_minute)
        ]
    if mode == "many-services":
        return _hourly_rollout(settings, service_index, minute)
    raise ValueError(f"unknown mode {mode!r}")


def _hourly_rollout(settings: Settings, service_index: int, minute: int) -> list[Slice]:
    """One rollout an hour on every service, staggered evenly across the hour, so a run of any
    length sees rollouts in progress. The version names count the hours."""
    offset = (service_index * 60) // max(settings.services, 1)
    since = minute - offset
    hour = math.floor(since / 60)
    into = since - hour * 60
    old, new = f"v{hour}", f"v{hour + 1}"
    share = _new_share(into, 0, settings.rollout_minutes)
    if share >= 1.0:
        return [Slice(new, 1.0, settings.old_failure)]
    return [
        Slice(old, 1.0 - share, settings.old_failure),
        Slice(new, share, settings.old_failure),
    ]


@dataclass(frozen=True, slots=True)
class Batch:
    """The spans of one service and version sent in one export."""

    service: str
    version: str | None
    requests: int
    failures: int
    latency_factor: float
    throws: bool


def plan_tick(settings: Settings, minute: int, rng: random.Random) -> list[Batch]:
    """The spans to send in one tick, drawn from each slice's expected count."""
    per_tick = settings.rpm / TICKS_PER_MINUTE
    batches = []
    for index, service in enumerate(service_names(settings)):
        for piece in slices_for(settings, index, minute):
            requests = poisson(rng, per_tick * piece.share)
            if requests == 0:
                continue
            failures = sum(rng.random() < piece.failure_rate for _ in range(requests))
            batches.append(
                Batch(
                    service, piece.version, requests, failures, piece.latency_factor, piece.throws
                )
            )
    return batches


def poisson(rng: random.Random, mean: float) -> int:
    """Knuth's method, fine for the small means a tick has; a normal approximation above 30."""
    if mean <= 0:
        return 0
    if mean > 30:
        return max(0, round(rng.gauss(mean, math.sqrt(mean))))
    limit, count, product = math.exp(-mean), 0, rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


def export_request(batches: Sequence[Batch], at: datetime, rng: random.Random) -> bytes:
    """One OTLP/HTTP trace export: a resource per service and version, a SERVER span per request.
    Each span starts at `at`, so the minute the worker counts it in is the minute recorded."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

    request = ExportTraceServiceRequest()
    start_ns = int(at.timestamp() * 1e9)
    for batch in batches:
        resource_spans = request.resource_spans.add()
        attributes = resource_spans.resource.attributes
        name = attributes.add()
        name.key, name.value.string_value = "service.name", batch.service
        if batch.version is not None:
            version = attributes.add()
            version.key, version.value.string_value = "service.version", batch.version
        scope = resource_spans.scope_spans.add()
        for index in range(batch.requests):
            span = scope.spans.add()
            span.trace_id, span.span_id = rng.randbytes(16), rng.randbytes(8)
            span.name, span.kind = ENDPOINT, Span.SPAN_KIND_SERVER
            duration_ms = rng.lognormvariate(
                math.log(MEDIAN_MS * batch.latency_factor), LATENCY_SIGMA
            )
            span.start_time_unix_nano = start_ns
            span.end_time_unix_nano = start_ns + int(duration_ms * 1e6)
            if index < batch.failures:
                span.status.code = Status.STATUS_CODE_ERROR
                if batch.throws:
                    event = span.events.add()
                    event.name, event.time_unix_nano = "exception", start_ns
                    kind = event.attributes.add()
                    kind.key, kind.value.string_value = "exception.type", NEW_EXCEPTION
    return request.SerializeToString()


@dataclass(slots=True)
class Ledger:
    """What the receiver accepted, by service and version (`""` for none), and the window."""

    mode: str
    started_at: str = ""
    ended_at: str = ""
    sent: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)
    refused_exports: int = 0

    def record(self, batches: Sequence[Batch]) -> None:
        for batch in batches:
            counts = self.sent.setdefault(batch.service, {}).setdefault(
                batch.version or "", {"requests": 0, "errors": 0}
            )
            counts["requests"] += batch.requests
            counts["errors"] += batch.failures

    def total_requests(self) -> int:
        return sum(c["requests"] for versions in self.sent.values() for c in versions.values())


def require_local(url: str, what: str) -> None:
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing {what} host {host}: this sends demo traffic, dev only")


def run(
    settings: Settings,
    *,
    minutes: float,
    post: Callable[[bytes], int],
    seed: int,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Ledger:
    """Send for `minutes`, one export per tick, and return what was accepted."""
    rng = random.Random(seed)
    ledger = Ledger(settings.mode, started_at=now().isoformat())
    began = clock()
    ticks = int(minutes * TICKS_PER_MINUTE)
    for tick in range(ticks):
        at = now()
        batches = plan_tick(settings, tick // TICKS_PER_MINUTE, rng)
        if batches:
            status = post(export_request(batches, at, rng))
            if status < 300:
                ledger.record(batches)
            else:
                ledger.refused_exports += 1
        sleep(max(0.0, began + (tick + 1) * SEND_EVERY_SECONDS - clock()))
    ledger.ended_at = now().isoformat()
    return ledger


def poster(receiver: str, key: str) -> Callable[[bytes], int]:
    import httpx

    client = httpx.Client(timeout=10.0)

    def post(body: bytes) -> int:
        """One export, retried after a refusal the way an OpenTelemetry SDK retries one."""
        status = 0
        for attempt in range(RETRIES):
            status = client.post(
                f"{receiver}/v1/traces",
                content=body,
                headers={"content-type": "application/x-protobuf", "x-nexovue-key": key},
            ).status_code
            if status not in RETRYABLE:
                break
            time.sleep(0.2 * 2**attempt)
        return status

    return post


# ------------------------------------------------------------------ verify


@dataclass(frozen=True, slots=True)
class Mismatch:
    service: str
    version: str
    sent: tuple[int, int]
    stored: tuple[int, int]


def compare(
    ledger: Mapping[str, Mapping[str, Mapping[str, int]]],
    stored: Mapping[tuple[str, str], tuple[int, int]],
) -> list[Mismatch]:
    """Every (service, version) whose stored requests or errors differ from what was sent,
    including one stored that was never sent."""
    sent = {
        (service, version): (counts["requests"], counts["errors"])
        for service, versions in ledger.items()
        for version, counts in versions.items()
    }
    return [
        Mismatch(
            service,
            version,
            sent.get((service, version), (0, 0)),
            stored.get((service, version), (0, 0)),
        )
        for service, version in sorted(set(sent) | set(stored))
        if sent.get((service, version), (0, 0)) != stored.get((service, version), (0, 0))
    ]


VERSION_TOTALS_SQL: Final = """
SELECT toString(service_name), toString(version), sum(request_count), sum(error_count)
FROM service_versions_1m
WHERE tenant_id = {tenant_id:String} AND project_id = {project_id:String}
  AND minute >= {start:DateTime} AND minute <= {end:DateTime}
GROUP BY service_name, version
"""
SERVICE_TOTALS_SQL: Final = """
SELECT toString(service_name), sum(request_count), sum(error_count)
FROM service_stats_1m
WHERE tenant_id = {tenant_id:String} AND project_id = {project_id:String}
  AND minute >= {start:DateTime} AND minute <= {end:DateTime}
GROUP BY service_name
"""


def clickhouse(url: str) -> Any:
    import clickhouse_connect

    require_local(url, "ClickHouse")
    parsed = urlparse(url)
    return clickhouse_connect.get_client(
        host=parsed.hostname,
        port=parsed.port or 8123,
        username=parsed.username or "nexovue",
        password=parsed.password or "",
        database=(parsed.path or "/nexovue").lstrip("/"),
    )


def stored_totals(
    client: Any, ledger: Ledger, tenant_id: str, project_id: str
) -> tuple[dict[tuple[str, str], tuple[int, int]], dict[str, tuple[int, int]]]:
    window = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "start": datetime.fromisoformat(ledger.started_at).replace(
            second=0, microsecond=0, tzinfo=None
        ),
        "end": datetime.fromisoformat(ledger.ended_at).replace(tzinfo=None) + timedelta(minutes=1),
    }
    versions = {
        (str(s), str(v)): (int(r), int(e))
        for s, v, r, e in client.query(VERSION_TOTALS_SQL, parameters=window).result_rows
    }
    services = {
        str(s): (int(r), int(e))
        for s, r, e in client.query(SERVICE_TOTALS_SQL, parameters=window).result_rows
    }
    return versions, services


def requests_of(versions: Mapping[tuple[str, str], tuple[int, int]], service: str) -> int:
    return sum(requests for (named, _), (requests, _) in versions.items() if named == service)


def unsummed_services(
    versions: Mapping[tuple[str, str], tuple[int, int]], services: Mapping[str, tuple[int, int]]
) -> list[str]:
    """Services whose version rows do not sum to their service rows (FR16)."""
    summed: dict[str, tuple[int, int]] = {}
    for (service, _), (requests, errors) in versions.items():
        before = summed.get(service, (0, 0))
        summed[service] = (before[0] + requests, before[1] + errors)
    return sorted(s for s in set(summed) | set(services) if summed.get(s) != services.get(s))


# ------------------------------------------------------------------ commands


def settings_from(args: argparse.Namespace) -> Settings:
    return Settings(
        mode=args.mode,
        services=args.services,
        rpm=args.rpm,
        start_minute=args.start_minute,
        rollout_minutes=args.rollout_minutes,
        old_failure=args.old_failure,
        new_failure=args.new_failure,
        versions_per_minute=args.versions_per_minute,
    )


def print_ledger(ledger: Ledger) -> None:
    print(
        f"{ledger.mode}: sent {ledger.started_at} to {ledger.ended_at}, "
        f"{ledger.refused_exports} exports refused"
    )
    for service in sorted(ledger.sent):
        for version, counts in sorted(ledger.sent[service].items()):
            print(
                f"  {service:12} {version or '(none)':24} {counts['requests']:8} requests "
                f"{counts['errors']:6} errors"
            )


def cmd_send(args: argparse.Namespace) -> int:
    require_local(args.receiver, "receiver")
    ledger = run(
        settings_from(args),
        minutes=args.minutes,
        post=poster(args.receiver, args.key),
        seed=args.seed,
    )
    print_ledger(ledger)
    save_ledger(ledger, Path(args.ledger))
    return 0 if ledger.refused_exports == 0 else 1


def save_ledger(ledger: Ledger, path: Path) -> None:
    path.write_text(json.dumps(asdict(ledger), indent=2))


def load_ledger(path: str) -> Ledger:
    raw = json.loads(Path(path).read_text())
    return Ledger(
        raw["mode"], raw["started_at"], raw["ended_at"], raw["sent"], raw["refused_exports"]
    )


def cmd_verify(args: argparse.Namespace) -> int:
    ledger = load_ledger(args.ledger)
    versions, services = stored_totals(
        clickhouse(args.clickhouse), ledger, args.org_id, args.project_id
    )
    print_ledger(ledger)
    print("stored in service_versions_1m:")
    for (service, version), (requests, errors) in sorted(versions.items()):
        print(f"  {service:12} {version or '(none)':24} {requests:8} requests {errors:6} errors")
    mismatches = compare(ledger.sent, versions)
    unsummed = unsummed_services(versions, services)
    for mismatch in mismatches:
        print(
            f"MISMATCH {mismatch.service} {mismatch.version or '(none)'}: "
            f"sent {mismatch.sent}, stored {mismatch.stored}"
        )
    for service in unsummed:
        print(
            f"UNSUMMED {service}: versions {requests_of(versions, service)}, "
            f"service_stats_1m {services.get(service)}"
        )
    if not mismatches and not unsummed:
        print(
            "equal: every version's stored counts match what was sent, and sum to service_stats_1m"
        )
    return 1 if mismatches or unsummed else 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = top.add_subparsers(dest="command", required=True)

    send = commands.add_parser("send", help="send one mode's traffic through the receiver")
    send.add_argument("mode", choices=MODES)
    send.add_argument("--key", required=True, help="the project's ingest key")
    send.add_argument("--receiver", default=DEFAULT_RECEIVER)
    send.add_argument("--minutes", type=float, default=30.0)
    send.add_argument("--services", type=int, default=1)
    send.add_argument("--rpm", type=float, default=60.0, help="requests a minute per service")
    send.add_argument("--start-minute", type=int, default=5, help="when the release begins")
    send.add_argument("--rollout-minutes", type=int, default=20)
    send.add_argument("--old-failure", type=float, default=0.002)
    send.add_argument("--new-failure", type=float, default=0.03)
    send.add_argument("--versions-per-minute", type=int, default=25, help="churn only")
    send.add_argument("--seed", type=int, default=28)
    send.add_argument("--ledger", default="release-demo-ledger.json")
    send.set_defaults(handler=cmd_send)

    verify = commands.add_parser("verify", help="compare a ledger with what the rollups stored")
    verify.add_argument("--ledger", default="release-demo-ledger.json")
    verify.add_argument("--org-id", required=True, help="the project's organization, its tenant id")
    verify.add_argument("--project-id", required=True)
    verify.add_argument("--clickhouse", default=DEFAULT_CLICKHOUSE)
    verify.set_defaults(handler=cmd_verify)
    return top


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
