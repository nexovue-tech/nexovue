"""Measure what dashboards cost the install (F20, plan T5.7 and T11.6; NFR4, NFR11, NFR12, NFR14).

Reads the committed pass marks (`docs/research/f20-pass-marks-2026-11.md`) and the numbers below
them, signs in as the dev stack's owner, builds worst-case dashboards, and reads them at 1, 5 and
20 at once while a steady trickle of spans goes through the receiver. For each level it records
the API's batch times, panel states, the API account's queries in ClickHouse (`system.query_log`),
ClickHouse's resident memory, the API process's peak memory, and the worker's lag and dead-letter
counters before and after. It prints JSON; the report in `docs/research/` is written from it.

It measures what it is pointed at and says so: the machine is the one it ran on, the data is the
seeded demo (`tools/dashboard-demo`), and nothing here is a customer's volume. The scheduler's
three measures (NFR11) are not read here: this tool does not start a scheduler.

    uv run --package nexovue-api python tools/dashboard-load/load.py --help

Dev stack only: it refuses any API or ClickHouse host but localhost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlparse

import httpx

LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
SEEDED_EMAIL: Final = "nexovue@nexovue.com"
SEEDED_PASSWORD: Final = "nexovue-dev"
PANEL_STATES: Final = ("ok", "refused", "timed_out", "failed", "over_limit", "paused_by_operator")
# One request must outlast the batch's own deadline, or the tool would report its own timeout.
READ_TIMEOUT_SECONDS: Final = 120.0
INGEST_SPANS_PER_BATCH: Final = 200
INGEST_BATCHES_PER_SECOND: Final = 2
NS_PER_MS: Final = 1_000_000


def require_local(url: str, what: str) -> None:
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"{what} is {host!r}: this tool measures the dev stack and nothing else.")


@dataclass
class Level:
    """What one level of load produced."""

    dashboards: int
    processes: int
    reads: int = 0
    batch_ms: list[float] = field(default_factory=list)
    states: Counter[str] = field(default_factory=Counter)
    http_refused: Counter[int] = field(default_factory=Counter)

    def summary(self) -> dict[str, Any]:
        ordered = sorted(self.batch_ms)
        return {
            "dashboards": self.dashboards,
            "api_processes": self.processes,
            "reads": self.reads,
            "batch_ms_p50": _quantile(ordered, 0.5),
            "batch_ms_p95": _quantile(ordered, 0.95),
            "batch_ms_max": ordered[-1] if ordered else None,
            "panel_states": dict(self.states),
            "http_errors": {str(code): count for code, count in self.http_refused.items()},
        }


def _quantile(ordered: Sequence[float], q: float) -> float | None:
    if not ordered:
        return None
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)


def worst_case_definition(services: Sequence[str]) -> dict[str, Any]:
    """30 panels over as many distinct reads as the services allow, under the scan cap."""

    def panel(index: int, kind: str, settings: dict[str, Any], width: int = 4) -> dict[str, Any]:
        return {
            "id": f"p_{index}",
            "kind": kind,
            "title": None,
            "width": width,
            "height": "medium",
            "settings": settings,
        }

    panels: list[dict[str, Any]] = []
    signals = ("rate", "errors", "latency")
    for index in range(22):
        service = services[index % len(services)]
        panels.append(
            panel(
                len(panels) + 1, "service_trend", {"service": service, "signal": signals[index % 3]}
            )
        )
    for signal in signals:
        panels.append(
            panel(
                len(panels) + 1,
                "top_services_trend",
                {"signal": signal, "order": "worst", "count": 5},
            )
        )
    panels.append(panel(len(panels) + 1, "services_table", {"order": "worst", "rows": 20}))
    panels.append(
        panel(
            len(panels) + 1,
            "endpoints_table",
            {"service": services[0], "order": "worst", "rows": 20},
        )
    )
    panels.append(panel(len(panels) + 1, "note", {"text": "Load measurement."}))
    panels.append(panel(len(panels) + 1, "open_findings", {"service": services[0]}))
    panels.append(
        panel(
            len(panels) + 1,
            "service_trend",
            {"service": services[1 % len(services)], "signal": "rate"},
        )
    )
    return {
        "schema_version": 1,
        "refresh_seconds": None,
        "default_range": "1h",
        "service_picker": False,
        "links": [],
        "panels": panels[:30],
    }


class Stack:
    """The dev stack this run measures: API origins, the stores and the worker's metrics."""

    def __init__(self, arguments: argparse.Namespace) -> None:
        self.apis = arguments.api
        self.clickhouse = arguments.clickhouse
        self.receiver = arguments.receiver
        self.worker_metrics = arguments.worker_metrics
        self.api_pids = arguments.api_pid
        self.container = arguments.clickhouse_container

    def clickhouse_query(self, sql: str) -> str:
        done = subprocess.run(
            ["docker", "exec", self.container, "clickhouse-client", "-q", sql],
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    def worker_counters(self) -> dict[str, float]:
        text = httpx.get(self.worker_metrics, timeout=10).text
        lag = dead = 0.0
        for line in text.splitlines():
            if line.startswith("nexovue_worker_consumer_lag_records{"):
                lag += float(line.rsplit(" ", 1)[1])
            elif line.startswith("nexovue_worker_dead_letter_records"):
                dead += float(line.rsplit(" ", 1)[1])
        return {"consumer_lag_records": lag, "dead_letter_records": dead}

    def api_peak_rss_mib(self) -> list[float]:
        peaks = []
        for pid in self.api_pids:
            with open(f"/proc/{pid}/status", encoding="utf-8") as status:
                for line in status:
                    if line.startswith("VmHWM:"):
                        peaks.append(round(int(line.split()[1]) / 1024, 1))
        return peaks

    def clickhouse_resident_mib(self) -> float:
        value = self.clickhouse_query(
            "select round(value / 1048576, 1) from system.asynchronous_metrics "
            "where metric = 'MemoryResident'"
        )
        return float(value)

    def api_account_since(self, since: datetime) -> dict[str, Any]:
        stamp = since.strftime("%Y-%m-%d %H:%M:%S")
        self.clickhouse_query("system flush logs")
        row = self.clickhouse_query(
            "select count(), countIf(exception_code != 0), "
            "round(max(memory_usage) / 1048576, 1), round(quantile(0.95)(query_duration_ms), 1), "
            "groupUniqArrayIf(exception_code, exception_code != 0) "
            "from system.query_log where user = 'nexovue_api_reader' and type != 'QueryStart' "
            f"and event_time >= toDateTime('{stamp}', 'UTC') format TSV"
        )
        count, failed, peak, p95, codes = row.split("\t")
        return {
            "queries": int(count),
            "failed": int(failed),
            "peak_query_memory_mib": float(peak),
            "p95_query_ms": None if p95 == "nan" else float(p95),
            "error_codes": codes,
        }


async def sign_in(
    origin: str, email: str = SEEDED_EMAIL, password: str = SEEDED_PASSWORD, address: int = 0
) -> httpx.AsyncClient:
    """One person's session, from an address of their own: the sign-in door counts per address."""
    client = httpx.AsyncClient(
        base_url=origin,
        timeout=READ_TIMEOUT_SECONDS,
        headers={"x-forwarded-for": f"10.20.{address // 250}.{address % 250 + 1}"},
    )
    reply = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    reply.raise_for_status()
    return client


async def invite_people(
    owner: httpx.AsyncClient, origins: Sequence[str], count: int, mail_log: str
) -> list[httpx.AsyncClient]:
    """`count` viewers in the owner's organization, so reads are charged to people.

    The dev API renders mail into its log instead of sending it, and the invitation's link is read
    from there. Each person signs in at one of the API processes in turn.
    """
    run = uuid.uuid4().hex[:6]
    people: list[httpx.AsyncClient] = []
    for index in range(count):
        email = f"load-{run}-{index}@nexovue.test"
        reply = await owner.post(
            "/api/v1/members/invitations", json={"email": email, "role": "viewer"}
        )
        reply.raise_for_status()
        await asyncio.sleep(0.2)
        with open(mail_log, encoding="utf-8", errors="replace") as log:
            lines = [line for line in log if email in line and "invite?token=" in line]
        token = lines[-1].split("invite?token=")[1].split("\\n")[0].split("'")[0]
        anyone = httpx.AsyncClient(
            base_url=origins[0],
            timeout=READ_TIMEOUT_SECONDS,
            headers={"x-forwarded-for": f"10.20.{index // 250}.{index % 250 + 1}"},
        )
        accepted = await anyone.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": f"Load {index}", "password": f"load-{run}-password"},
        )
        accepted.raise_for_status()
        await anyone.aclose()
        people.append(
            await sign_in(origins[index % len(origins)], email, f"load-{run}-password", index + 1)
        )
    return people


async def sample_lag(stack: Stack, stop: asyncio.Event, seen: list[float]) -> None:
    """The worker's lag every 2 s: it drops at each commit, so its peak is what is read."""
    while not stop.is_set():
        counters = await asyncio.to_thread(stack.worker_counters)
        seen.append(counters["consumer_lag_records"])
        await asyncio.sleep(2)


async def ingest_trickle(
    receiver: str, api_key: str, stop: asyncio.Event, counter: Counter[str]
) -> None:
    """Spans through the receiver all the while, so the worker has work beside the reads."""
    async with httpx.AsyncClient(timeout=30) as client:
        while not stop.is_set():
            now_ns = int(time.time() * 1000) * NS_PER_MS
            spans = [
                {
                    "traceId": uuid.uuid4().bytes.hex(),
                    "spanId": uuid.uuid4().bytes[:8].hex(),
                    "name": "GET /load",
                    "kind": 2,
                    "startTimeUnixNano": str(now_ns - 90 * NS_PER_MS),
                    "endTimeUnixNano": str(now_ns),
                    "status": {"code": 1},
                }
                for _ in range(INGEST_SPANS_PER_BATCH)
            ]
            body = {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {"key": "service.name", "value": {"stringValue": "dashboard-load"}}
                            ]
                        },
                        "scopeSpans": [{"scope": {"name": "load"}, "spans": spans}],
                    }
                ]
            }
            reply = await client.post(
                f"{receiver}/v1/traces", json=body, headers={"x-nexovue-key": api_key}
            )
            counter[str(reply.status_code)] += 1
            await asyncio.sleep(1 / INGEST_BATCHES_PER_SECOND)


def own_window(index: int, minutes: int) -> dict[str, str]:
    """A window of its own per dashboard, ending whole minutes apart, so no two share a scan."""
    end = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=index + 1)
    return {
        "from": (end - timedelta(minutes=minutes)).isoformat(),
        "to": end.isoformat(),
    }


async def read_once(
    client: httpx.AsyncClient,
    dashboard_id: str,
    project_id: str,
    level: Level,
    window: dict[str, str],
) -> None:
    started = time.monotonic()
    reply = await client.get(
        "/api/v1/dashboard-panels/read",
        params={"dashboard_id": dashboard_id, "project_id": project_id, **window},
    )
    elapsed = (time.monotonic() - started) * 1000
    level.reads += 1
    if reply.status_code != 200:
        level.http_refused[reply.status_code] += 1
        return
    level.batch_ms.append(elapsed)
    for result in reply.json()["panels"].values():
        level.states[result["state"]] += 1


async def run_level(
    clients: Sequence[httpx.AsyncClient],
    dashboards: Sequence[str],
    project_id: str,
    processes: int,
    rounds: int,
    window_minutes: int,
) -> Level:
    level = Level(dashboards=len(dashboards), processes=processes)
    for round_index in range(rounds):
        await asyncio.gather(
            *(
                read_once(
                    clients[index % len(clients)],
                    dashboard,
                    project_id,
                    level,
                    own_window(index + round_index * len(dashboards), window_minutes),
                )
                for index, dashboard in enumerate(dashboards)
            )
        )
    return level


async def open_templates(
    client: httpx.AsyncClient, project_id: str, service: str
) -> dict[str, Any]:
    """AC129: each starting point, saved and opened once by one person, and every panel's state."""
    found = (await client.get("/api/v1/dashboards/templates")).json()["templates"]
    opened: dict[str, Any] = {}
    for template in found:
        document = template["document"]
        document["title"] = f"{template['name']} {uuid.uuid4().hex[:6]}"
        created = await client.post("/api/v1/dashboards", json=document)
        if created.status_code >= 400:
            opened[template["id"]] = {
                "save_refused": created.status_code,
                "detail": created.text[:300],
            }
            continue
        dashboard_id = created.json()["id"]
        started = time.monotonic()
        read = await client.get(
            "/api/v1/dashboard-panels/read",
            params={
                "dashboard_id": dashboard_id,
                "project_id": project_id,
                "range": "1h",
                "service": service,
            },
        )
        panels = read.json()["panels"] if read.status_code == 200 else {}
        opened[template["id"]] = {
            "http": read.status_code,
            "ms": round((time.monotonic() - started) * 1000),
            "panel_states": dict(Counter(result["state"] for result in panels.values())),
        }
        await client.delete(f"/api/v1/dashboards/{dashboard_id}")
    return opened


async def measure(arguments: argparse.Namespace) -> dict[str, Any]:
    stack = Stack(arguments)
    clients = [await sign_in(origin) for origin in stack.apis]
    first = clients[0]
    people = (
        await invite_people(first, stack.apis, arguments.people, arguments.mail_log)
        if arguments.people
        else clients
    )
    project_id = (await first.get("/api/v1/projects")).json()["projects"][0]["id"]
    listed = (
        await first.get(
            "/api/v1/services",
            params={
                "project_id": project_id,
                "from": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "to": datetime.now(UTC).isoformat(),
            },
        )
    ).json()["services"]
    services = [row["service_name"] for row in listed] or sys.exit("the project has no services")
    if arguments.templates:
        return {"templates_opened_once": await open_templates(first, project_id, services[0])}
    definition = worst_case_definition(services)
    made: list[str] = []
    for index in range(max(max(arguments.levels), 1)):
        created = await first.post(
            "/api/v1/dashboards",
            json={
                "title": f"Load {uuid.uuid4().hex[:6]} {index}",
                "description": "",
                "definition": definition,
            },
        )
        if created.status_code >= 400:
            sys.exit(f"the dashboard was refused: {created.status_code} {created.text[:600]}")
        made.append(created.json()["id"])

    report: dict[str, Any] = {
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "api_processes": len(stack.apis),
        "services": len(services),
        "panels_per_dashboard": len(definition["panels"]),
        "window_minutes": arguments.window_minutes,
        "baseline": {
            **stack.worker_counters(),
            "clickhouse_resident_mib": stack.clickhouse_resident_mib(),
        },
        "levels": [],
    }
    stop = asyncio.Event()
    ingest: Counter[str] = Counter()
    trickle = asyncio.create_task(
        ingest_trickle(stack.receiver, arguments.ingest_key, stop, ingest)
    )
    try:
        for count in arguments.levels:
            started = datetime.now(UTC)
            before = stack.worker_counters()
            lag_seen: list[float] = []
            sampling_stop = asyncio.Event()
            sampler = asyncio.create_task(sample_lag(stack, sampling_stop, lag_seen))
            if count == 0:
                # The baseline: the same ingest trickle for the same time, and no reads.
                level = Level(dashboards=0, processes=len(stack.apis))
                await asyncio.sleep(arguments.idle_seconds)
            else:
                level = await run_level(
                    people,
                    made[:count],
                    project_id,
                    len(stack.apis),
                    arguments.rounds,
                    arguments.window_minutes,
                )
            await asyncio.sleep(5)
            sampling_stop.set()
            await sampler
            after = stack.worker_counters()
            report["levels"].append(
                {
                    **level.summary(),
                    "worker_lag_peak": max(lag_seen, default=None),
                    "worker_lag_samples": len(lag_seen),
                    "dead_letters_delta": after["dead_letter_records"]
                    - before["dead_letter_records"],
                    "clickhouse_resident_mib": stack.clickhouse_resident_mib(),
                    "api_account": stack.api_account_since(started),
                    "api_peak_rss_mib": stack.api_peak_rss_mib(),
                }
            )
    finally:
        stop.set()
        await trickle
        for dashboard in made:
            await first.delete(f"/api/v1/dashboards/{dashboard}")
    report["ingest_batches_by_status"] = dict(ingest)
    report["wall_time_median_s"] = statistics.median(
        [level["batch_ms_p50"] or 0 for level in report["levels"]]
    )
    for client in {*clients, *people}:
        await client.aclose()
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--api", action="append", required=True, help="an API origin, once per process"
    )
    parser.add_argument("--api-pid", action="append", type=int, default=[], help="its process id")
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--worker-metrics", required=True, help="the worker's /metrics URL")
    parser.add_argument("--clickhouse-container", required=True)
    parser.add_argument("--clickhouse", default="localhost")
    parser.add_argument("--ingest-key", default="nvk_live_devlocaldevlocaldevloc")
    parser.add_argument("--levels", type=int, nargs="+", default=[1, 5, 20])
    parser.add_argument("--rounds", type=int, default=3, help="reads of each dashboard per level")
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument(
        "--templates", action="store_true", help="only open each template once (AC129)"
    )
    parser.add_argument(
        "--people", type=int, default=0, help="viewers to invite, one per dashboard read"
    )
    parser.add_argument(
        "--mail-log", default="", help="the API log that holds the rendered invitations"
    )
    parser.add_argument("--idle-seconds", type=int, default=30, help="how long level 0 waits")
    arguments = parser.parse_args(argv)
    for origin in [*arguments.api, arguments.receiver, arguments.worker_metrics]:
        require_local(origin, "an endpoint")
    print(json.dumps(asyncio.run(measure(arguments)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
