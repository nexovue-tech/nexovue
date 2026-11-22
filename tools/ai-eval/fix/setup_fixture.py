"""AC37c's setup fixture: 30 services in two environments, for someone new to time F5's setup
(F5 plan T9.4; spec AC37c: "Someone new must time the whole setup once, on a 30-service,
two-environment fixture, and the time and step count are reported with no pass mark … Rungs 3
and 4 are timed separately from rung 5").

**The timing is a person's, and the founder's to arrange.** This only gives them something to
set up: `send` makes the 30 services appear in both environments of a project on the dev stack,
and `locations` prints where each one's code lives, across three repositories, as they would be
laid out in a scratch GitHub organization. How to run the timing: `docs/features/ai-solution/
status.md`, *T9.4, the setup timing*.

    cd tools/ai-eval && uv run python -m fix.setup_fixture send --key <ingest key> \\
        [--receiver http://localhost:14318]
    cd tools/ai-eval && uv run python -m fix.setup_fixture locations

Dev stack only: it refuses any receiver but this machine.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections.abc import Sequence
from typing import Final
from urllib.parse import urlparse

LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_RECEIVER: Final = "http://localhost:14318"
ENVIRONMENTS: Final = ("production", "staging")
# A made-up company of 30 services: 18 in one monorepo, the rest in two repositories of their
# own kind. Names only; nothing here runs.
MONOREPO: Final = "acme-scratch/platform"
SERVICES: Final = (
    "checkout",
    "cart",
    "pricing",
    "tax",
    "discounts",
    "inventory",
    "catalog",
    "search",
    "recommendations",
    "reviews",
    "accounts",
    "auth",
    "sessions",
    "notifications",
    "email",
    "sms",
    "billing",
    "invoices",
    *(f"web-{name}" for name in ("storefront", "admin", "help", "status", "docs", "blog")),
    *(f"data-{name}" for name in ("ingest", "export", "reports", "ledger", "audit", "backfill")),
)
SPANS_PER_RESOURCE: Final = 3
ENDPOINT: Final = "GET /health"
DURATION_NS: Final = 20_000_000
RETRIES: Final = 3


def code_locations() -> dict[str, tuple[str, str]]:
    """Each service's repository and folder, as an admin would enter them on Code locations."""
    locations: dict[str, tuple[str, str]] = {}
    for service in SERVICES:
        if service.startswith("web-"):
            locations[service] = ("acme-scratch/web", f"apps/{service.removeprefix('web-')}")
        elif service.startswith("data-"):
            locations[service] = ("acme-scratch/data", f"jobs/{service.removeprefix('data-')}")
        else:
            locations[service] = (MONOREPO, f"services/{service}")
    return locations


def export_request(*, at_ns: int, seed: int = 0) -> bytes:
    """One OTLP/HTTP export: a resource per service and environment, a few healthy SERVER spans
    each, so every service shows in both environments."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.proto.trace.v1.trace_pb2 import Span

    rng = random.Random(seed)
    request = ExportTraceServiceRequest()
    for environment in ENVIRONMENTS:
        for service in SERVICES:
            resource_spans = request.resource_spans.add()
            attributes = resource_spans.resource.attributes
            for key, value in (
                ("service.name", service),
                ("deployment.environment.name", environment),
            ):
                attribute = attributes.add()
                attribute.key, attribute.value.string_value = key, value
            scope = resource_spans.scope_spans.add()
            for _ in range(SPANS_PER_RESOURCE):
                span = scope.spans.add()
                span.trace_id, span.span_id = rng.randbytes(16), rng.randbytes(8)
                span.name, span.kind = ENDPOINT, Span.SPAN_KIND_SERVER
                span.start_time_unix_nano = at_ns
                span.end_time_unix_nano = at_ns + DURATION_NS
    return request.SerializeToString()


def require_local(url: str) -> None:
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing receiver host {host}: this sends fixture traffic, dev only")


def send(receiver: str, key: str) -> int:
    import httpx

    require_local(receiver)
    body = export_request(at_ns=time.time_ns())
    status = 0
    with httpx.Client(timeout=10.0) as client:
        for attempt in range(RETRIES):
            status = client.post(
                f"{receiver}/v1/traces",
                content=body,
                headers={"content-type": "application/x-protobuf", "x-nexovue-key": key},
            ).status_code
            if status < 500 and status != 429:
                break
            time.sleep(0.5 * 2**attempt)
    return status


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fix.setup_fixture", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sending = commands.add_parser("send", help="make the 30 services appear in both environments")
    sending.add_argument("--key", required=True, help="the project's ingest key")
    sending.add_argument("--receiver", default=DEFAULT_RECEIVER)
    commands.add_parser("locations", help="print each service's repository and folder")
    arguments = parser.parse_args(argv)
    if arguments.command == "send":
        status = send(arguments.receiver, arguments.key)
        print(f"receiver answered {status} for {len(SERVICES)} services x {len(ENVIRONMENTS)}")
        return 0 if status < 300 else 1
    for service, (repository, folder) in code_locations().items():
        print(f"{service}\t{repository}\t{folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
