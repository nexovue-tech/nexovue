"""Show one of F3's rules on the dev stack within minutes (F3 plan T1.12; Step 1, *Judgeable*).

`just explain-demo --rule N` writes what rule N reads, opens a finding directly, and queues
its facts' read, so the finding's page shows its explanation within a minute:

- a signed deploy goes **through the real webhook**, signed with a deploy secret you give;
- calls between services go into `service_edges_1m`, traffic into `service_stats_1m`, and a
  new error group into `spans`, a few minutes before now;
- the finding is inserted into `findings` and its `opened` gather into `jobs`, as detection
  would in its own transaction.

**Detection did not judge this finding.** The tool opens it, so the page shows how F3
explains a finding, never whether detection would have opened one. It says so when it runs.

Rules, by number (spec FR8): 1 a signed deploy of this service, 2 a dependency with its own
earlier finding and moved calls, 3 a signed deploy of a service with a short deploy history,
4 a signed deploy of a dependency, 5 a version only seen in telemetry (names no cause alone),
6 a dependency's calls moved with no finding of its own, 7 traffic rose before a slowdown, 8 a
new error group (names no cause alone; shown beside rule 6's moved calls). Whether a cause is
named, and how strongly, is the rules' own judgement on what is written; rule 1 needs a week
of deploy records, which `just detection-seed` writes for the first service.

Dev stack only: it refuses any host but localhost, as `seed.py` does. Likely causes are held
until the founder's bar is met; set `SHOW_HELD_CAUSES=true` on the dev API to see them.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import sys
import urllib.request
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from seed import DEFAULT_CLICKHOUSE, DEFAULT_POSTGRES, Project, _clickhouse, _resolve, require_local

DEFAULT_API: Final = "http://localhost:8000"
RULES: Final = (1, 2, 3, 4, 5, 6, 7, 8)
DISCLAIMER: Final = (
    "Detection did not judge this finding: explain-demo opened it directly, so its page shows "
    "how the facts are explained, not whether detection would have opened it."
)
SIGNATURE_HEADER: Final = "X-Hub-Signature-256"
DEPENDENCY: Final = "payments"
# Calls a minute between the two services; failures before and during the demo's fault.
CALLS: Final = 120
FAILURES_BEFORE: Final = 1
FAILURES_DURING: Final = 30
# The demo's minutes: the finding starts this long ago, and the window before is as long.
STARTED_AGO: Final = timedelta(minutes=2)
WINDOW: Final = timedelta(minutes=10)
GATHER_TASK: Final = "nexovue_scheduler.evidence.gather"


@dataclass(frozen=True, slots=True)
class Edge:
    minute: datetime
    caller: str
    callee: str
    calls: int
    errors: int


@dataclass(frozen=True, slots=True)
class Deploy:
    service: str
    version: str
    at: datetime


@dataclass(slots=True)
class Demo:
    """What one rule's demo writes. Pure, so a test can read it without a stack."""

    rule: int
    service: str
    deploys: list[Deploy] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    traffic: list[tuple[datetime, int]] = field(default_factory=list)
    errors: list[tuple[datetime, str]] = field(default_factory=list)
    seen_versions: list[Deploy] = field(default_factory=list)
    # A dependency's own open finding, started this long before the demo's.
    dependency_finding_ago: timedelta | None = None


def plan(rule: int, service: str, *, now: datetime) -> Demo:
    """What rule `rule`'s demo writes for `service`, ending at `now`."""
    if rule not in RULES:
        raise ValueError(f"rule {rule} is not one of {RULES}")
    start = (now - STARTED_AGO).replace(second=0, microsecond=0)
    demo = Demo(rule=rule, service=service)
    if rule == 3:
        # A service with no deploy history: a deploy there is "possible" at most (FR7).
        demo.service = f"{service}-new"
    if rule in (1, 3):
        demo.deploys.append(
            Deploy(demo.service, f"demo-{uuid.uuid4().hex[:6]}", start - timedelta(minutes=4))
        )
    if rule in (2, 4, 6, 8):
        moved = rule != 4
        demo.edges.extend(_edges(demo.service, start, moved=moved))
    if rule == 2:
        demo.dependency_finding_ago = timedelta(minutes=3)
    if rule == 4:
        demo.deploys.append(
            Deploy(DEPENDENCY, f"demo-{uuid.uuid4().hex[:6]}", start - timedelta(minutes=8))
        )
    if rule == 5:
        demo.seen_versions.append(
            Deploy(service, f"demo-seen-{uuid.uuid4().hex[:6]}", start - timedelta(minutes=5))
        )
    if rule == 7:
        for back in range(75, 0, -1):
            minute = start - timedelta(minutes=back)
            demo.traffic.append((minute, 200 if back <= 15 else 100))
    if rule == 8:
        demo.errors.append((start - timedelta(minutes=1), "DemoTimeoutError"))
    return demo


def _edges(caller: str, start: datetime, *, moved: bool) -> list[Edge]:
    edges = []
    for back in range(int(WINDOW / timedelta(minutes=1)), -2, -1):
        minute = start - timedelta(minutes=back)
        during = minute >= start
        errors = FAILURES_DURING if (moved and during) else FAILURES_BEFORE
        edges.append(Edge(minute, caller, DEPENDENCY, CALLS, errors))
    return edges


def webhook_body(deploys: Sequence[Deploy]) -> bytes:
    """F28's signed envelope: each item signs its own id and time (D6)."""
    return json.dumps(
        {
            "deploys": [
                {
                    "delivery_id": uuid.uuid4().hex,
                    "service_name": deploy.service,
                    "version": deploy.version,
                    "deployed_at": deploy.at.isoformat(),
                }
                for deploy in deploys
            ]
        }
    ).encode()


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _send_deploys(api: str, project: Project, secret: str, deploys: Sequence[Deploy]) -> None:
    body = webhook_body(deploys)
    request = urllib.request.Request(
        f"{api.rstrip('/')}/api/v1/deploys/webhook/{project.project_id}",
        data=body,
        method="POST",
        headers={"content-type": "application/json", SIGNATURE_HEADER: sign(body, secret)},
    )
    with urllib.request.urlopen(request, timeout=10) as answer:
        if answer.status >= 300:
            raise SystemExit(f"the webhook refused the deploy: {answer.status}")


def _write_clickhouse(client: Any, project: Project, demo: Demo) -> None:
    for edge in demo.edges:
        client.command(
            "INSERT INTO service_edges_1m (tenant_id, project_id, client_service, server_service, "
            "server_peer_kind, server_peer_attr, door, edge_kind, minute, call_count, error_count, "
            "latency_quantiles) SELECT %(tenant)s, %(project)s, %(caller)s, %(callee)s, '', '', "
            "'server', 'call', %(minute)s, %(calls)s, %(errors)s, "
            "arrayReduce('quantilesState(0.5, 0.95, 0.99)', [toFloat64(40)])",
            parameters={
                "tenant": project.tenant_id,
                "project": project.project_id,
                "caller": edge.caller,
                "callee": edge.callee,
                "minute": edge.minute.replace(tzinfo=None),
                "calls": edge.calls,
                "errors": edge.errors,
            },
        )
    for minute, requests in demo.traffic:
        client.command(
            "INSERT INTO service_stats_1m (tenant_id, project_id, service_name, minute, "
            "request_count) VALUES (%(tenant)s, %(project)s, %(service)s, %(minute)s, "
            "%(requests)s)",
            parameters={
                "tenant": project.tenant_id,
                "project": project.project_id,
                "service": demo.service,
                "minute": minute.replace(tzinfo=None),
                "requests": requests,
            },
        )
    for at, exception_type in demo.errors:
        client.command(
            "INSERT INTO spans (tenant_id, project_id, service_name, timestamp, received_at, "
            "exception_type, exception_message, trace_id, span_id) VALUES (%(tenant)s, "
            "%(project)s, %(service)s, %(at)s, %(at)s, %(type)s, 'demo', %(trace)s, %(span)s)",
            parameters={
                "tenant": project.tenant_id,
                "project": project.project_id,
                "service": demo.service,
                "at": at.replace(tzinfo=None),
                "type": exception_type,
                "trace": uuid.uuid4().hex,
                "span": uuid.uuid4().hex[:16],
            },
        )


_OPEN_FINDING: Final = """
INSERT INTO findings (org_id, project_id, scope, service_name, severity, state, title, signals,
                      started_at)
VALUES ($1::uuid, $2::uuid, 'service', $3, 'warning', 'open', $4, $5::jsonb, $6)
RETURNING id
"""
_QUEUE_GATHER: Final = """
INSERT INTO jobs (queue, task, kwargs, org_id, run_after, expires_at, max_attempts,
                  soft_limit_seconds, hard_limit_seconds)
VALUES ('evidence', $1, $2::jsonb, $3::uuid, now(), now() + interval '1 hour', 8, 60, 90)
"""
_SEEN_VERSION: Final = """
WITH release AS (
    INSERT INTO releases (org_id, project_id, service_name, version, first_seen_at, last_seen_at,
                          report_count, last_activity_at)
    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $5, 0, now())
    RETURNING id
)
INSERT INTO release_periods (org_id, release_id, project_id, service_name, started_at,
                             start_basis, already_running, is_return)
SELECT $1::uuid, release.id, $2::uuid, $3, $5, 'seen', false, false FROM release
"""
_SLOWER: Final = [{"signal": "slower", "key": "slower", "normal": 120.0, "worst": 480.0}]


async def _open(postgres: str, project: Project, demo: Demo, *, now: datetime) -> str:
    import asyncpg  # Here, so the plan is testable without a driver.

    start = (now - STARTED_AGO).replace(second=0, microsecond=0)
    connection = await asyncpg.connect(postgres)
    try:
        async with connection.transaction():
            for seen in demo.seen_versions:
                await connection.execute(
                    _SEEN_VERSION,
                    project.tenant_id,
                    project.project_id,
                    seen.service,
                    seen.version,
                    seen.at,
                )
            if demo.dependency_finding_ago is not None:
                await connection.execute(
                    _OPEN_FINDING,
                    project.tenant_id,
                    project.project_id,
                    DEPENDENCY,
                    f"{DEPENDENCY} is slower than normal (explain-demo)",
                    json.dumps(_SLOWER),
                    start - demo.dependency_finding_ago,
                )
            finding_id = await connection.fetchval(
                _OPEN_FINDING,
                project.tenant_id,
                project.project_id,
                demo.service,
                f"{demo.service} is slower than normal (explain-demo, rule {demo.rule})",
                json.dumps(_SLOWER),
                start,
            )
            await connection.execute(
                _QUEUE_GATHER,
                GATHER_TASK,
                json.dumps(
                    {"finding_id": str(finding_id), "org_id": project.tenant_id, "reason": "opened"}
                ),
                project.tenant_id,
            )
    finally:
        await connection.close()
    return str(finding_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--rule", type=int, required=True, choices=RULES, help="the rule to show")
    parser.add_argument("--service", default="frontend", help="the finding's service")
    parser.add_argument(
        "--secret", default="", help="a deploy secret of the project, for rules 1, 3, 4"
    )
    parser.add_argument("--api", default=DEFAULT_API, help="the dev API, for the deploy webhook")
    parser.add_argument("--clickhouse", default=DEFAULT_CLICKHOUSE, help="the admin account")
    parser.add_argument("--postgres", default=DEFAULT_POSTGRES)
    parser.add_argument("--project", default="production", help="slug or id (default production)")
    parser.add_argument("--org", default="acme", help="the organization a slug is in")
    args = parser.parse_args(argv)

    for url, what in (
        (args.clickhouse, "ClickHouse"),
        (args.postgres, "Postgres"),
        (args.api, "API"),
    ):
        require_local(url, what)
    now = datetime.now(UTC)
    demo = plan(args.rule, args.service, now=now)
    if demo.deploys and not args.secret:
        raise SystemExit(
            f"rule {args.rule} sends a signed deploy: pass --secret (Settings, Projects, Deploys)"
        )
    project: Project = asyncio.run(_resolve(args.postgres, args.project, args.org))
    if demo.deploys:
        _send_deploys(args.api, project, args.secret, demo.deploys)
    _write_clickhouse(_clickhouse(args.clickhouse), project, demo)
    finding_id = asyncio.run(_open(args.postgres, project, demo, now=now))
    print(DISCLAIMER)
    print(
        f"Opened finding {finding_id} on {demo.service} for rule {args.rule}; "
        "its facts are being read."
    )
    print(f"Open it at /findings/{finding_id} within a minute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
