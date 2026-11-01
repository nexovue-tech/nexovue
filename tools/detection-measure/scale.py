"""Measure anomaly detection at its cost target: 200 projects, 2,000 services (F6, AC29, plan T8.2).

Four steps, each a command:

- `tenants` signs up the projects through signup's own code, each its own organization with a
  known ingest key, their time zones spread round the world;
- `load` sends each project's services live spans through the receiver, at the rate
  `tools/detection-seed` models for that service at this local time, so live traffic looks like its
  history. Start it before `seed`: the history then ends where the live minutes begin;
- `seed` writes four weeks of history for every project with `tools/detection-seed`;
- `watch` records, while the scheduler runs, when each service's minute was judged, what
  detection read from ClickHouse, the memory and CPU of the processes named, and what detection
  stores, then writes a JSON report and prints the AC29 table.

A minute M settles once every span partition is counted past M + 3 minutes
(`nexovue_scheduler.detection.settle`), so M + 3 minutes is the earliest it can settle. A minute
that settled later, because the trace worker held it, was not handed out by the sweep before it.
So each lag is measured from the later of M + 3 minutes and the last sweep that did not hand M out:
the moment the minute is known to have been unsettled. The lag from M + 3 minutes alone, which
counts the pipeline's own delay against detection, is reported beside it.

Dev stack only: every store must be on localhost. Run with `just detection-scale <command>`.
To remove the tenants afterwards: `just detection-scale remove --yes`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "detection-seed"))

import seed as history  # noqa: E402  The seeder's own model, so live traffic matches history.

LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_POSTGRES: Final = "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
DEFAULT_CLICKHOUSE: Final = "http://nexovue:nexovue_dev@localhost:18123/nexovue"
DEFAULT_RECEIVER: Final = "http://localhost:14318"

# AC29's counts.
PROJECTS: Final = 200
SERVICES: Final = history.DEMO_SERVICES[:10]
TARGET_SECONDS: Final = 90.0

# Every organization this tool makes starts with this slug, and only these.
SLUG_PREFIX: Final = "ac29-"
# `nvk_live_` + 22 base62 characters. The first 7 carry the project's number, because a key is
# known by its first 16 characters, which must be unique (`uq_api_keys_key_prefix`).
KEY_STEM: Final = "nvk_live_a29"
KEY_PAD: Final = "scale" * 3
# Spread round the world, so some projects are at their busiest whenever this runs.
ZONES: Final = (
    "UTC",
    "America/New_York",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "Europe/Berlin",
    "Asia/Kolkata",
    "Asia/Tokyo",
    "Australia/Sydney",
)
ENDPOINTS: Final = ("GET /items", "POST /orders", "GET /account")
# The seeder's default, so `seed` and `load` agree unless told otherwise.
DEFAULT_RPM: Final = 120.0
SEND_EVERY_SECONDS: Final = 5.0
# The receiver holds at most 8 export bodies at once (RECEIVER_MAX_INFLIGHT_EXPORTS) and answers
# 429 beyond that; an SDK retries those, and so does this.
SEND_PARALLEL: Final = 6
RETRIES: Final = 4
RETRYABLE: Final = frozenset({429, 502, 503, 504})

# M + 1 minute + the export allowance: the earliest minute M can settle (settle.py).
SETTLE_DELAY: Final = timedelta(minutes=3)
# Detection's tables in Postgres.
POSTGRES_TABLES: Final = (
    "detection_state",
    "detection_baselines",
    "detection_project_runs",
    "detection_sweep",
    "detection_events",
    "findings",
    "finding_incidents",
    "baseline_shifts",
    "service_detection_settings",
    "intake_refusals",
)
CLICKHOUSE_TABLES: Final = ("service_stats_1m", "detection_readings", "endpoint_stats_1m")
# The accounts detection reads and writes ClickHouse with (ADR-023).
DETECTION_USERS: Final = ("nexovue_reader", "nexovue_detection_writer")


@dataclass(frozen=True, slots=True)
class Tenant:
    number: int
    org_id: str
    project_id: str
    timezone: str

    @property
    def key(self) -> str:
        return ingest_key(self.number)


def ingest_key(number: int) -> str:
    return f"{KEY_STEM}{number:04d}{KEY_PAD}"


def slug(number: int) -> str:
    return f"{SLUG_PREFIX}{number:03d}"


def require_local(url: str, what: str) -> None:
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing {what} host {host}: this writes test tenants, dev only")


# ------------------------------------------------------------------ the lags


@dataclass(frozen=True, slots=True)
class Sweep:
    swept_at: datetime
    settled_minute: datetime


@dataclass(frozen=True, slots=True)
class Lag:
    project_id: str
    service: str
    minute: datetime
    # None: the minute was never judged.
    judged_at: datetime | None
    # Seconds from the minute settling, and from M + 3 minutes.
    seconds: float | None
    seconds_from_earliest: float | None


def settled_from(minute: datetime, sweeps: Sequence[Sweep]) -> datetime:
    """When `minute` is known to have still been unsettled: the later of M + 3 minutes and the
    last sweep before the first one to hand it out."""
    earliest = minute + SETTLE_DELAY
    before = None
    for sweep in sweeps:
        if sweep.settled_minute >= minute:
            break
        before = sweep.swept_at
    return earliest if before is None else max(earliest, before)


def judged_minutes(
    advances: Sequence[tuple[datetime, datetime | None, datetime]],
) -> dict[datetime, datetime]:
    """Each minute's judging time, from a service's (seen at, judged before, judged through)
    observations: a run that moved it on judged every minute between."""
    found: dict[datetime, datetime] = {}
    for seen_at, before, through in advances:
        minute = through
        while before is None or minute > before:
            found.setdefault(minute, seen_at)
            if before is None:
                break
            minute -= timedelta(minutes=1)
    return found


def lags(
    judged: Mapping[tuple[str, str], Mapping[datetime, datetime]],
    sweeps: Sequence[Sweep],
    minutes: Sequence[datetime],
) -> list[Lag]:
    """Every service's lag for every minute in `minutes`."""
    found: list[Lag] = []
    for (project_id, service), at in sorted(judged.items()):
        for minute in minutes:
            judged_at = at.get(minute)
            if judged_at is None:
                found.append(Lag(project_id, service, minute, None, None, None))
                continue
            found.append(
                Lag(
                    project_id,
                    service,
                    minute,
                    judged_at,
                    (judged_at - settled_from(minute, sweeps)).total_seconds(),
                    (judged_at - (minute + SETTLE_DELAY)).total_seconds(),
                )
            )
    return found


@dataclass(frozen=True, slots=True)
class LagSummary:
    count: int
    never_judged: int
    max_seconds: float | None
    p95_seconds: float | None
    p50_seconds: float | None
    over_target: int


def percentile(values: Sequence[float], share: float) -> float:
    """The nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def summarise(seconds: Sequence[float | None]) -> LagSummary:
    known = [value for value in seconds if value is not None]
    return LagSummary(
        count=len(seconds),
        never_judged=len(seconds) - len(known),
        max_seconds=max(known) if known else None,
        p95_seconds=percentile(known, 0.95) if known else None,
        p50_seconds=statistics.median(known) if known else None,
        over_target=sum(1 for value in seconds if value is None or value > TARGET_SECONDS),
    )


def by_project(found: Sequence[Lag], *, earliest: bool = False) -> list[float | None]:
    """A project's lag for a minute is its slowest service's; never judged wins."""
    worst: dict[tuple[str, datetime], float | None] = {}
    for lag in found:
        value = lag.seconds_from_earliest if earliest else lag.seconds
        key = (lag.project_id, lag.minute)
        known = worst.get(key, 0.0)
        if key not in worst or (known is not None and (value is None or value > known)):
            worst[key] = value
    return list(worst.values())


def by_minute(found: Sequence[Lag]) -> dict[datetime, list[float | None]]:
    """Each minute's lags, every service's, so a slow minute shows which it was."""
    minutes: dict[datetime, list[float | None]] = {}
    for lag in found:
        minutes.setdefault(lag.minute, []).append(lag.seconds)
    return dict(sorted(minutes.items()))


# ------------------------------------------------------------ what was read


@dataclass(frozen=True, slots=True)
class QueryKind:
    name: str
    queries: int
    read_rows: int
    read_bytes: int
    written_rows: int
    written_bytes: int
    total_ms: int
    max_ms: int


_PLACEHOLDER: Final = re.compile(r"\{\w+:[^}]+\}")
# Bound values as the server logs them: literals first, then arrays of them, then the casts
# around either, whose type names have brackets of their own (`'Array(String)'`).
_LITERAL: Final = re.compile(r"'(?:[^'\\]|\\.)*'")
_ARRAY: Final = re.compile(r"\[[^\]]*\]")
_CAST: Final = re.compile(r"_CAST\([^()]*\)")
_FORMAT: Final = re.compile(r"\s+FORMAT\s+\w+\s*$")
_READINGS: Final = re.compile(r"^(INSERT INTO|DESCRIBE TABLE) `?(?:\w+`?\.`?)?detection_readings\b")


def shape(sql: str) -> str:
    """A statement with its values taken out, as written in the source or as the server logs
    it: the server binds each parameter into the text and brackets every condition."""
    sql = _FORMAT.sub("", sql)
    for bound in (_PLACEHOLDER, _LITERAL, _ARRAY, _CAST):
        sql = bound.sub("?", sql)
    return " ".join(sql.replace("(", " ").replace(")", " ").replace(",", " , ").split())


def detection_statements() -> dict[str, str]:
    """Detection's ClickHouse statements by name and shape, from the scheduler's own modules."""
    from nexovue_scheduler.detection import settle, store

    return {
        name: shape(value)
        for module in (store, settle)
        for name, value in vars(module).items()
        if name.endswith("_SQL") and isinstance(value, str)
    }


def classify(query: str, statements: Mapping[str, str]) -> str | None:
    """Which detection statement a logged query is; None if none. The driver describes the
    readings table before each insert into it."""
    readings = _READINGS.match(" ".join(query.split()))
    if readings is not None:
        return "READINGS_INSERT" if readings.group(1) == "INSERT INTO" else "READINGS_DESCRIBE"
    logged = shape(query)
    for name, sql in statements.items():
        if logged == sql:
            return name
    return None


# --------------------------------------------------------------- processes


@dataclass(slots=True)
class ProcessGroup:
    """A process and its descendants, found by a pattern on its command line."""

    label: str
    pattern: re.Pattern[str]
    peak_rss_bytes: int = 0
    peak_pss_bytes: int = 0
    peak_processes: int = 0
    cpu_seconds: float = 0.0
    peak_cpu_cores: float = 0.0
    _ticks: dict[int, int] = field(default_factory=dict)
    _sampled_at: float | None = None


def _proc_children() -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields_after = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except OSError:
            continue
        children.setdefault(int(fields_after[1]), []).append(int(entry.name))
    return children


def _cmdline(pid: int) -> str:
    try:
        return (
            Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        )
    except OSError:
        return ""


def _tree(roots: Iterable[int], children: Mapping[int, list[int]]) -> set[int]:
    found: set[int] = set()
    pending = list(roots)
    while pending:
        pid = pending.pop()
        if pid not in found:
            found.add(pid)
            pending.extend(children.get(pid, ()))
    return found


def _memory(pid: int) -> tuple[int, int]:
    """RSS and PSS in bytes; PSS shares each page among the processes using it."""
    rss = pss = 0
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
            name, _, rest = line.partition(":")
            if name == "Rss":
                rss = int(rest.split()[0]) * 1024
            elif name == "Pss":
                pss = int(rest.split()[0]) * 1024
    except OSError:
        pass
    return rss, pss


def _ticks(pid: int) -> int:
    try:
        after = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except OSError:
        return 0
    return int(after[11]) + int(after[12])  # utime + stime


def sample(groups: Sequence[ProcessGroup]) -> None:
    """One reading of each group's memory and CPU, from /proc (Linux)."""
    children = _proc_children()
    pids = [int(entry.name) for entry in Path("/proc").iterdir() if entry.name.isdigit()]
    now = time.monotonic()
    tick = os.sysconf("SC_CLK_TCK")
    me = os.getpid()
    for group in groups:
        roots = [pid for pid in pids if pid != me and group.pattern.search(_cmdline(pid))]
        members = _tree(roots, children)
        rss = pss = 0
        ticks: dict[int, int] = {}
        for pid in members:
            r, p = _memory(pid)
            rss, pss = rss + r, pss + p
            ticks[pid] = _ticks(pid)
        used = sum(
            max(0, now_ticks - group._ticks.get(pid, now_ticks)) for pid, now_ticks in ticks.items()
        )
        if group._sampled_at is not None and now > group._sampled_at:
            seconds = used / tick
            group.cpu_seconds += seconds
            group.peak_cpu_cores = max(group.peak_cpu_cores, seconds / (now - group._sampled_at))
        group._ticks, group._sampled_at = ticks, now
        group.peak_rss_bytes = max(group.peak_rss_bytes, rss)
        group.peak_pss_bytes = max(group.peak_pss_bytes, pss)
        group.peak_processes = max(group.peak_processes, len(members))


@dataclass(slots=True)
class ContainerPeak:
    name: str
    peak_bytes: int = 0
    limit_bytes: int = 0


def _flat_keyed(text: str) -> dict[str, int]:
    """A cgroup file of `name value` lines."""
    found: dict[str, int] = {}
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if value.strip().isdigit():
            found[name] = int(value)
    return found


def cgroup_memory(path: Path) -> dict[str, int]:
    """A memory cgroup's peak, limit and OOM kills, v2 or v1: the container's own ceiling, for
    a process run from source inside one (`echo $$ > <path>/cgroup.procs` before `exec`)."""

    def read(name: str) -> str:
        return (path / name).read_text()

    if (path / "memory.peak").exists():
        limit = read("memory.max").strip()
        return {
            "peak_bytes": int(read("memory.peak")),
            "limit_bytes": 0 if limit == "max" else int(limit),
            "oom_kills": _flat_keyed(read("memory.events")).get("oom_kill", 0),
        }
    return {
        "peak_bytes": int(read("memory.max_usage_in_bytes")),
        "limit_bytes": int(read("memory.limit_in_bytes")),
        "failcnt": int(read("memory.failcnt")),
        "oom_kills": _flat_keyed(read("memory.oom_control")).get("oom_kill", 0),
    }


_UNITS: Final = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "kB": 1000, "MB": 1000**2}


def parse_size(text: str) -> int:
    match = re.fullmatch(r"\s*([\d.]+)\s*([A-Za-z]+)\s*", text)
    if match is None:
        return 0
    return int(float(match.group(1)) * _UNITS.get(match.group(2), 1))


def sample_containers(containers: Sequence[ContainerPeak]) -> None:
    """`docker stats`: the container's cgroup memory and its limit, where one runs in Docker."""
    if not containers:
        return
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"]
        + [c.name for c in containers],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    usage = dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)
    for container in containers:
        used, _, limit = usage.get(container.name, "").partition("/")
        container.peak_bytes = max(container.peak_bytes, parse_size(used))
        container.limit_bytes = parse_size(limit) or container.limit_bytes


# ------------------------------------------------------------------ the stores


def _clickhouse(url: str) -> Any:
    import clickhouse_connect

    require_local(url, "ClickHouse")
    parsed = urlparse(url)
    return clickhouse_connect.get_client(
        host=parsed.hostname,
        port=parsed.port or 18123,
        username=parsed.username or "nexovue",
        password=parsed.password or "",
        database=(parsed.path or "/nexovue").lstrip("/"),
    )


async def _tenants(postgres: str) -> list[Tenant]:
    import asyncpg

    connection = await asyncpg.connect(postgres)
    try:
        rows = await connection.fetch(
            """
            SELECT organizations.slug, organizations.id::text, projects.id::text, projects.timezone
            FROM organizations JOIN projects ON projects.org_id = organizations.id
            WHERE organizations.slug LIKE $1 AND projects.deleted_at IS NULL
            ORDER BY organizations.slug
            """,
            f"{SLUG_PREFIX}%",
        )
    finally:
        await connection.close()
    return [Tenant(int(s.removeprefix(SLUG_PREFIX)), o, p, z) for s, o, p, z in rows]


def tenants_or_exit(postgres: str) -> list[Tenant]:
    found = asyncio.run(_tenants(postgres))
    if not found:
        raise SystemExit("No AC29 tenants. Run `just detection-scale tenants` first.")
    return found


# ------------------------------------------------------------------ commands


def cmd_tenants(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from nexovue_api.data.postgres.models import Organization
    from nexovue_api.signup.service import provision_tenant

    require_local(args.postgres, "Postgres")

    async def make() -> int:
        engine = create_async_engine(
            args.postgres.replace("postgresql://", "postgresql+asyncpg://")
        )
        made = 0
        try:
            for number in range(1, args.count + 1):
                async with AsyncSession(engine) as session, session.begin():
                    exists = await session.scalar(
                        select(Organization.id).where(Organization.slug == slug(number))
                    )
                    if exists is not None:
                        continue
                    await provision_tenant(
                        session,
                        organization_name=f"AC29 scale {number:03d}",
                        email=f"{slug(number)}@example.com",
                        password=None,
                        organization_slug=slug(number),
                        api_key=ingest_key(number),
                        api_key_name="AC29 scale",
                        timezone=ZONES[number % len(ZONES)],
                    )
                    made += 1
        finally:
            await engine.dispose()
        return made

    made = asyncio.run(make())
    print(f"{made} tenants made; {len(tenants_or_exit(args.postgres))} AC29 tenants in all.")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    tenants = tenants_or_exit(args.postgres)
    script = ROOT / "tools" / "detection-seed" / "seed.py"

    def one(tenant: Tenant) -> str:
        done = subprocess.run(
            [
                sys.executable,
                str(script),
                "--postgres",
                args.postgres,
                "--clickhouse",
                args.clickhouse,
                "--project",
                tenant.project_id,
                "--services",
                ",".join(SERVICES),
                "--seed",
                str(tenant.number),
                "--rpm",
                str(args.rpm),
                "--weeks",
                str(args.weeks),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        output = (done.stdout + done.stderr).strip()
        if done.returncode == 0:
            return f"{slug(tenant.number)}: {output.splitlines()[-1]}"
        if "seeded already" in output:
            return f"{slug(tenant.number)}: seeded already"
        raise SystemExit(f"{slug(tenant.number)} failed: {output[-2000:]}")

    started = time.monotonic()
    with ThreadPoolExecutor(args.parallel) as pool:
        for line in pool.map(one, tenants):
            print(line, flush=True)
    print(f"Seeded {len(tenants)} projects in {time.monotonic() - started:.0f} s.")
    return 0


@dataclass(frozen=True, slots=True)
class LiveService:
    name: str
    character: history.Profile


def live_services(tenant: Tenant) -> list[LiveService]:
    """Each service's character, drawn exactly as `seed.py --seed <number>` drew it."""
    return [
        LiveService(name, history.profile(random.Random(f"{tenant.number}:{name}")))
        for name in SERVICES
    ]


def per_minute(service: LiveService, at: datetime, zone: ZoneInfo, rpm: float) -> float:
    return rpm * service.character.scale * history.load_factor(at.astimezone(zone))


def export_request(
    services: Sequence[LiveService],
    counts: Sequence[tuple[int, int]],
    at: datetime,
    zone: ZoneInfo,
    rng: random.Random,
) -> bytes:
    """One OTLP/HTTP trace export: each service's inbound requests, failures marked."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

    request = ExportTraceServiceRequest()
    end_ns = int(at.timestamp() * 1e9)
    load = history.load_factor(at.astimezone(zone))
    for service, (requests, failures) in zip(services, counts, strict=True):
        if not requests:
            continue
        spans = request.resource_spans.add()
        attribute = spans.resource.attributes.add()
        attribute.key, attribute.value.string_value = "service.name", service.name
        scope = spans.scope_spans.add()
        mu = math.log(service.character.median_ms) + 0.15 * load
        for index in range(requests):
            span = scope.spans.add()
            span.trace_id, span.span_id = rng.randbytes(16), rng.randbytes(8)
            span.name = ENDPOINTS[index % len(ENDPOINTS)]
            span.kind = Span.SPAN_KIND_SERVER
            duration_ns = int(rng.lognormvariate(mu, service.character.sigma) * 1e6)
            span.start_time_unix_nano = end_ns - duration_ns
            span.end_time_unix_nano = end_ns
            if index < failures:
                span.status.code = Status.STATUS_CODE_ERROR
    return request.SerializeToString()


def cmd_load(args: argparse.Namespace) -> int:
    import httpx

    require_local(args.receiver, "receiver")
    tenants = tenants_or_exit(args.postgres)
    services = {tenant.number: live_services(tenant) for tenant in tenants}
    rng = random.Random(args.seed)
    stop_at = time.monotonic() + args.minutes * 60 if args.minutes else math.inf
    client = httpx.Client(timeout=10.0)
    sent = refused = spans = 0
    reported = time.monotonic()

    def send(tenant: Tenant, body: bytes) -> int:
        """One export, retried after a refusal the way an OpenTelemetry SDK retries one."""
        for attempt in range(RETRIES):
            response = client.post(
                f"{args.receiver}/v1/traces",
                content=body,
                headers={"content-type": "application/x-protobuf", "x-nexovue-key": tenant.key},
            )
            if response.status_code not in RETRYABLE:
                break
            time.sleep(0.2 * 2**attempt)
        return response.status_code

    with ThreadPoolExecutor(args.parallel) as pool:
        while time.monotonic() < stop_at:
            tick = time.monotonic()
            at = datetime.now(UTC)
            bodies: list[tuple[Tenant, bytes]] = []
            for tenant in tenants:
                zone = ZoneInfo(tenant.timezone)
                counts = []
                for service in services[tenant.number]:
                    mean = per_minute(service, at, zone, args.rpm) * SEND_EVERY_SECONDS / 60
                    requests = history.poisson(rng, mean)
                    failures = history.poisson(rng, requests * service.character.failure_rate)
                    counts.append((requests, min(requests, failures)))
                    spans += requests
                bodies.append(
                    (tenant, export_request(services[tenant.number], counts, at, zone, rng))
                )
            for status in pool.map(lambda pair: send(*pair), bodies):
                sent += 1
                refused += status >= 300
            if time.monotonic() - reported >= 60:
                print(
                    f"{at:%H:%M:%S} {sent} exports, {refused} refused, {spans} spans "
                    f"({spans / (time.monotonic() - reported):.0f}/s)",
                    flush=True,
                )
                sent = refused = spans = 0
                reported = time.monotonic()
            time.sleep(max(0.0, SEND_EVERY_SECONDS - (time.monotonic() - tick)))
    return 0


@dataclass(slots=True)
class Observed:
    """What `watch` saw as it polled."""

    sweeps: list[Sweep] = field(default_factory=list)
    # (project, service) -> [(seen at, judged before, judged through)]
    advances: dict[tuple[str, str], list[tuple[datetime, datetime | None, datetime]]] = field(
        default_factory=dict
    )
    runs: int = 0
    peak_waiting_jobs: int = 0
    peak_running_jobs: int = 0


async def _watch_postgres(
    postgres: str,
    tenants: Sequence[Tenant],
    seconds: float,
    poll: float,
    groups: Sequence[ProcessGroup],
    containers: Sequence[ContainerPeak],
) -> Observed:
    import asyncpg

    observed = Observed()
    orgs = [tenant.org_id for tenant in tenants]
    last: dict[tuple[str, str], datetime | None] = {}
    completed: dict[str, datetime] = {}
    connection = await asyncpg.connect(postgres)
    stop_at = time.monotonic() + seconds
    next_print = time.monotonic()
    try:
        while time.monotonic() < stop_at:
            now = await connection.fetchval("SELECT clock_timestamp()")
            for swept_at, settled in await connection.fetch(
                "SELECT swept_at, settled_minute FROM detection_sweep"
            ):
                if settled is not None and (
                    not observed.sweeps or observed.sweeps[-1].swept_at != swept_at
                ):
                    observed.sweeps.append(Sweep(swept_at, settled))
            for project_id, service, through in await connection.fetch(
                "SELECT project_id::text, service_name, last_judged_minute FROM detection_state "
                "WHERE org_id = ANY($1::uuid[])",
                orgs,
            ):
                key = (project_id, service)
                if key in last and through is not None and through != last[key]:
                    observed.advances.setdefault(key, []).append((now, last[key], through))
                last[key] = through
            for project_id, at in await connection.fetch(
                "SELECT project_id::text, last_completed_at FROM detection_project_runs "
                "WHERE org_id = ANY($1::uuid[])",
                orgs,
            ):
                if at is not None and completed.get(project_id) not in (None, at):
                    observed.runs += 1
                if at is not None:
                    completed[project_id] = at
            waiting, running = await connection.fetchrow(
                "SELECT count(*) FILTER (WHERE lease_until IS NULL), "
                "count(*) FILTER (WHERE lease_until IS NOT NULL) "
                "FROM jobs WHERE queue = 'detection'"
            )
            observed.peak_waiting_jobs = max(observed.peak_waiting_jobs, waiting)
            observed.peak_running_jobs = max(observed.peak_running_jobs, running)
            sample(groups)
            sample_containers(containers)
            if time.monotonic() >= next_print:
                settled = observed.sweeps[-1].settled_minute if observed.sweeps else None
                print(
                    f"{now:%H:%M:%S} settled {settled:%H:%M} " if settled else f"{now:%H:%M:%S} ",
                    f"runs {observed.runs}, detection jobs waiting {waiting}, running {running}; "
                    + ", ".join(f"{g.label} {g.peak_pss_bytes / 2**20:.0f} MiB" for g in groups),
                    flush=True,
                )
                next_print += 60
            await asyncio.sleep(poll)
    finally:
        await connection.close()
    return observed


def _query_log(client: Any, start: datetime, end: datetime) -> list[QueryKind]:
    client.command("SYSTEM FLUSH LOGS")
    rows = client.query(
        """
        SELECT query, read_rows, read_bytes, written_rows, written_bytes, query_duration_ms
        FROM system.query_log
        WHERE type = 'QueryFinish' AND user IN {users:Array(String)}
          AND event_time >= {start:DateTime} AND event_time < {end:DateTime}
        """,
        parameters={"start": start, "end": end, "users": list(DETECTION_USERS)},
    ).result_rows
    statements = detection_statements()
    kinds: dict[str, list[tuple[int, int, int, int, int]]] = {}
    for query, read_rows, read_bytes, written_rows, written_bytes, ms in rows:
        name = classify(str(query), statements)
        if name is not None:
            kinds.setdefault(name, []).append(
                (int(read_rows), int(read_bytes), int(written_rows), int(written_bytes), int(ms))
            )
    return [
        QueryKind(
            name,
            len(found),
            sum(r[0] for r in found),
            sum(r[1] for r in found),
            sum(r[2] for r in found),
            sum(r[3] for r in found),
            sum(r[4] for r in found),
            max(r[4] for r in found),
        )
        for name, found in sorted(kinds.items())
    ]


def _storage(client: Any, postgres: str, tenants: Sequence[Tenant]) -> dict[str, Any]:
    import asyncpg

    parts = client.query(
        """
        SELECT table, sum(rows), sum(bytes_on_disk), sum(data_uncompressed_bytes), count()
        FROM system.parts
        WHERE database = currentDatabase() AND active AND table IN {tables:Array(String)}
        GROUP BY table
        """,
        parameters={"tables": list(CLICKHOUSE_TABLES)},
    ).result_rows
    orgs = [tenant.org_id for tenant in tenants]
    live_rows = client.query(
        """
        SELECT count(), min(minute), max(minute) FROM service_stats_1m
        WHERE tenant_id IN {orgs:Array(String)} AND minute >= now() - toIntervalHour(1)
          AND minute < toStartOfMinute(now()) - toIntervalMinute(2)
        """,
        parameters={"orgs": orgs},
    ).result_rows[0]

    async def sizes() -> list[tuple[str, int, int]]:
        connection = await asyncpg.connect(postgres)
        try:
            return [
                (
                    table,
                    int(await connection.fetchval("SELECT pg_total_relation_size($1)", table)),
                    int(await connection.fetchval(f"SELECT count(*) FROM {table}")),
                )
                for table in POSTGRES_TABLES
            ]
        finally:
            await connection.close()

    count, first, last = live_rows
    minutes = max(1, int((last - first).total_seconds() // 60) + 1) if count else 1
    return {
        "clickhouse": [
            {
                "table": table,
                "rows": int(rows),
                "bytes_on_disk": int(disk),
                "bytes_uncompressed": int(raw),
                "active_parts": int(active),
                "bytes_per_row_on_disk": disk / rows if rows else None,
            }
            for table, rows, disk, raw, active in parts
        ],
        "postgres": [
            {"table": table, "bytes": size, "rows": rows}
            for table, size, rows in asyncio.run(sizes())
        ],
        "service_stats_rows_per_day_ac29": count / minutes * 1440,
    }


def cmd_watch(args: argparse.Namespace) -> int:
    tenants = tenants_or_exit(args.postgres)
    groups = [
        ProcessGroup(label, re.compile(pattern))
        for label, _, pattern in (spec.partition("=") for spec in args.process)
    ]
    containers = [ContainerPeak(name) for name in args.container]
    client = _clickhouse(args.clickhouse)
    started = datetime.now(UTC)
    observed = asyncio.run(
        _watch_postgres(args.postgres, tenants, args.minutes * 60, args.poll, groups, containers)
    )
    ended = datetime.now(UTC)
    # Minutes that settled after the watch began and had time to be judged before it ended.
    first = (started - SETTLE_DELAY).replace(second=0, microsecond=0) + timedelta(minutes=1)
    last = (ended - SETTLE_DELAY - timedelta(minutes=3)).replace(second=0, microsecond=0)
    minutes = [
        first + timedelta(minutes=i) for i in range(int((last - first) / timedelta(minutes=1)) + 1)
    ]
    judged = {key: judged_minutes(seen) for key, seen in observed.advances.items()}
    # A service that never moved is never judged: it counts against the target.
    for tenant in tenants:
        for service in SERVICES:
            judged.setdefault((tenant.project_id, service), {})
    found = lags(judged, observed.sweeps, minutes)
    reads = _query_log(client, started, ended)
    passes = max(1, len(minutes))
    report = {
        "started": started.isoformat(),
        "ended": ended.isoformat(),
        "machine": {"cpus": os.cpu_count(), "memory_bytes": _memory_total()},
        "projects": len(tenants),
        "services_judged": sum(1 for at in judged.values() if at),
        "minutes": [minute.isoformat() for minute in minutes],
        "sweeps": len(observed.sweeps),
        "project_runs": observed.runs,
        "peak_detection_jobs_waiting": observed.peak_waiting_jobs,
        "peak_detection_jobs_running": observed.peak_running_jobs,
        "lag_by_service": asdict(summarise([lag.seconds for lag in found])),
        "lag_by_project": asdict(summarise(by_project(found))),
        "lag_by_project_from_earliest": asdict(summarise(by_project(found, earliest=True))),
        "lag_by_minute": [
            {"minute": minute.isoformat(), **asdict(summarise(each))}
            for minute, each in by_minute(found).items()
        ],
        "reads": [asdict(kind) for kind in reads],
        "read_rows_per_pass": sum(kind.read_rows for kind in reads) / passes,
        "processes": [
            {
                "label": g.label,
                "peak_rss_bytes": g.peak_rss_bytes,
                "peak_pss_bytes": g.peak_pss_bytes,
                "peak_processes": g.peak_processes,
                "cpu_seconds": g.cpu_seconds,
                "mean_cpu_cores": g.cpu_seconds / max(1.0, (ended - started).total_seconds()),
                "peak_cpu_cores": g.peak_cpu_cores,
            }
            for g in groups
        ],
        "containers": [asdict(c) for c in containers],
        "cgroups": {str(path): cgroup_memory(path) for path in args.cgroup},
        "storage": _storage(client, args.postgres, tenants),
        "worst": [
            {**asdict(lag), "minute": lag.minute.isoformat()}
            for lag in sorted(
                found, key=lambda lag: -math.inf if lag.seconds is None else -lag.seconds
            )[:20]
        ],
    }
    args.out.write_text(json.dumps(report, indent=1, default=str))
    _print(report)
    print(f"\nWritten to {args.out}.")
    return 0


def _memory_total() -> int:
    try:
        line = Path("/proc/meminfo").read_text().splitlines()[0]
    except OSError:
        return 0
    return int(line.split()[1]) * 1024


def _print(report: Mapping[str, Any]) -> None:
    def seconds(value: float | None) -> str:
        return "—" if value is None else f"{value:.1f} s"

    print(
        f"\n{report['projects']} projects, {report['services_judged']} services judged, "
        f"{len(report['minutes'])} minutes, {report['sweeps']} sweeps, "
        f"{report['project_runs']} project runs."
    )
    for name in ("lag_by_service", "lag_by_project", "lag_by_project_from_earliest"):
        lag = report[name]
        print(
            f"{name:30} max {seconds(lag['max_seconds'])}, p95 {seconds(lag['p95_seconds'])}, "
            f"p50 {seconds(lag['p50_seconds'])}, over {TARGET_SECONDS:.0f} s or never: "
            f"{lag['over_target']} of {lag['count']} ({lag['never_judged']} never)"
        )
    for minute in report["lag_by_minute"]:
        print(
            f"  {minute['minute'][11:16]} max {seconds(minute['max_seconds'])}, "
            f"p95 {seconds(minute['p95_seconds'])}, over {minute['over_target']}, "
            f"never {minute['never_judged']}"
        )
    print(f"rows read per pass: {report['read_rows_per_pass']:,.0f}")
    for kind in report["reads"]:
        print(
            f"  {kind['name']:24} {kind['queries']:6} queries, {kind['read_rows']:>12,} rows read, "
            f"{kind['written_rows']:>9,} written, max {kind['max_ms']} ms"
        )
    for process in report["processes"]:
        print(
            f"{process['label']:20} peak PSS {process['peak_pss_bytes'] / 2**20:.0f} MiB, RSS "
            f"{process['peak_rss_bytes'] / 2**20:.0f} MiB, {process['peak_processes']} processes, "
            f"CPU mean {process['mean_cpu_cores']:.2f} cores, peak {process['peak_cpu_cores']:.2f}"
        )
    for container in report["containers"]:
        print(
            f"{container['name']:20} peak {container['peak_bytes'] / 2**20:.0f} MiB of "
            f"{container['limit_bytes'] / 2**20:.0f} MiB"
        )
    for path, memory in report["cgroups"].items():
        print(
            f"cgroup {path}: peak {memory['peak_bytes'] / 2**20:.0f} MiB of "
            f"{memory['limit_bytes'] / 2**20:.0f} MiB, {memory['oom_kills']} OOM kills"
        )
    storage = report["storage"]
    for table in storage["clickhouse"]:
        print(
            f"ClickHouse {table['table']:20} {table['rows']:>12,} rows, "
            f"{table['bytes_on_disk'] / 2**20:8.1f} MiB on disk, {table['active_parts']} parts"
        )
    for table in storage["postgres"]:
        print(
            f"Postgres   {table['table']:28} {table['rows']:>9,} rows, "
            f"{table['bytes'] / 2**20:7.2f} MiB"
        )
    print(
        "service_stats_1m rows a day, AC29 tenants: "
        f"{storage['service_stats_rows_per_day_ac29']:,.0f}"
    )


@dataclass(slots=True)
class JobTime:
    """Where one project job's time went."""

    wall: float = 0.0
    clickhouse_reads: float = 0.0
    readings_insert: float = 0.0
    postgres: float = 0.0
    postgres_statements: int = 0

    @property
    def python(self) -> float:
        return self.wall - self.clickhouse_reads - self.readings_insert - self.postgres


def cmd_profile(args: argparse.Namespace) -> int:
    """Judge projects in this process, as their job does, timing each part. Each run is real:
    it judges the settled minute and records the run, as the scheduled job would have."""
    from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

    from nexovue_scheduler.detection import project_job
    from nexovue_scheduler.detection.store import ProjectDetection, open_store

    tenants = tenants_or_exit(args.postgres)[: args.projects]
    store = open_store(args.reader, args.writer)
    if store is None:
        return 2
    current = JobTime()

    def timed(target: Any, name: str, part: str, statements: bool = False) -> None:
        original = getattr(target, name)

        if asyncio.iscoroutinefunction(original):

            async def wrapped_async(*a: Any, **k: Any) -> Any:
                started = time.perf_counter()
                try:
                    return await original(*a, **k)
                finally:
                    setattr(current, part, getattr(current, part) + time.perf_counter() - started)
                    current.postgres_statements += statements

            setattr(target, name, wrapped_async)
            return

        def wrapped(*a: Any, **k: Any) -> Any:
            started = time.perf_counter()
            try:
                return original(*a, **k)
            finally:
                setattr(current, part, getattr(current, part) + time.perf_counter() - started)

        setattr(target, name, wrapped)

    timed(ProjectDetection, "_query", "clickhouse_reads")
    timed(ProjectDetection, "write", "readings_insert")
    timed(AsyncConnection, "execute", "postgres", statements=True)
    timed(AsyncConnection, "scalar", "postgres", statements=True)

    async def run() -> list[JobTime]:
        engine = create_async_engine(
            args.postgres.replace("postgresql://", "postgresql+asyncpg://"), pool_size=1
        )
        times: list[JobTime] = []
        try:
            settled = args.settled
            if settled is None:
                async with engine.connect() as connection:
                    settled = (
                        await connection.exec_driver_sql(
                            "SELECT settled_minute FROM detection_sweep"
                        )
                    ).scalar_one()
            for tenant in tenants:
                nonlocal current
                current = JobTime()
                started = time.perf_counter()
                outcome = await project_job.judge_project(
                    engine,
                    store,
                    uuid.UUID(tenant.project_id),
                    settled=settled,
                    now=datetime.now(UTC),
                )
                current.wall = time.perf_counter() - started
                if outcome.skipped is None:
                    times.append(current)
        finally:
            await engine.dispose()
        return times

    times = asyncio.run(run())
    if not times:
        print("Every project was locked by a running job; try again.")
        return 1
    print(f"{len(times)} project jobs, means in ms:")
    for part in ("wall", "clickhouse_reads", "readings_insert", "postgres", "python"):
        values = [getattr(each, part) * 1000 for each in times]
        print(f"  {part:18} mean {statistics.fmean(values):7.1f}, max {max(values):7.1f}")
    print(
        "  postgres statements per job: "
        f"{statistics.fmean(each.postgres_statements for each in times):.0f}"
    )
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    """Close every AC29 organization as its owner would; the nightly erasure (ADR-014) then
    removes their ClickHouse rows."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from nexovue_api.organizations.service import request_deletion

    tenants = tenants_or_exit(args.postgres)
    if not args.yes:
        print(f"This closes {len(tenants)} AC29 organizations. Run again with --yes to do it.")
        return 1

    async def close() -> None:
        engine = create_async_engine(
            args.postgres.replace("postgresql://", "postgresql+asyncpg://")
        )
        try:
            for tenant in tenants:
                async with AsyncSession(engine) as session, session.begin():
                    await request_deletion(session, org_id=uuid.UUID(tenant.org_id), payments=None)
        finally:
            await engine.dispose()

    asyncio.run(close())
    print(f"Closed {len(tenants)} organizations; their telemetry is erased by the nightly sweep.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--postgres", default=DEFAULT_POSTGRES)
    parser.add_argument("--clickhouse", default=DEFAULT_CLICKHOUSE, help="the admin account")
    commands = parser.add_subparsers(dest="command", required=True)

    tenants = commands.add_parser("tenants", help="sign up the AC29 projects")
    tenants.add_argument("--count", type=int, default=PROJECTS)
    tenants.set_defaults(run=cmd_tenants)

    seeding = commands.add_parser("seed", help="write each project's history")
    seeding.add_argument("--parallel", type=int, default=4)
    seeding.add_argument("--rpm", type=float, default=DEFAULT_RPM)
    seeding.add_argument("--weeks", type=int, default=4)
    seeding.set_defaults(run=cmd_seed)

    load = commands.add_parser("load", help="send live spans through the receiver")
    load.add_argument("--receiver", default=DEFAULT_RECEIVER)
    load.add_argument("--rpm", type=float, default=DEFAULT_RPM)
    load.add_argument("--minutes", type=float, default=0, help="0 runs until stopped")
    load.add_argument("--parallel", type=int, default=SEND_PARALLEL)
    load.add_argument("--seed", type=int, default=29)
    load.set_defaults(run=cmd_load)

    watch = commands.add_parser("watch", help="measure while the scheduler runs")
    watch.add_argument("--minutes", type=float, default=75)
    watch.add_argument("--poll", type=float, default=2.0, help="seconds between looks")
    watch.add_argument(
        "--process",
        action="append",
        default=[],
        help="LABEL=REGEX on a command line; the match and its descendants (Linux)",
    )
    watch.add_argument(
        "--container", action="append", default=[], help="a container to read `docker stats` of"
    )
    watch.add_argument(
        "--cgroup",
        action="append",
        type=Path,
        default=[],
        help="a memory cgroup directory a process runs in: its peak, limit and OOM kills",
    )
    watch.add_argument("--out", type=Path, default=Path("detection-scale.json"))
    watch.set_defaults(run=cmd_watch)

    profile = commands.add_parser("profile", help="time project jobs part by part")
    profile.add_argument("--projects", type=int, default=20)
    profile.add_argument(
        "--settled",
        type=datetime.fromisoformat,
        help="the minute to judge up to (default: the sweep's); stop the scheduler first",
    )
    profile.add_argument(
        "--reader", default="clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue"
    )
    profile.add_argument(
        "--writer",
        default=(
            "clickhouse://nexovue_detection_writer:nexovue_detection_writer_dev"
            "@localhost:18123/nexovue"
        ),
    )
    profile.set_defaults(run=cmd_profile)

    remove = commands.add_parser("remove", help="close every AC29 organization")
    remove.add_argument("--yes", action="store_true", help="really close them")
    remove.set_defaults(run=cmd_remove)

    args = parser.parse_args(argv)
    require_local(args.postgres, "Postgres")
    require_local(args.clickhouse, "ClickHouse")
    result: int = args.run(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
