"""Measure reliability targets (F19) at the cost target: each statement and job (plan T6.2).

Extends `scale.py`'s tenants with what SLO judging reads, and runs F19's own jobs in this
process as the scheduler would, timing every part. Commands, in order:

- `slo-checks` gives each AC29 project the checks its targets read, before `seed`, so the seeder
  writes their readings too;
- `seed` (scale.py's own) writes weeks of service, endpoint and check history;
- `slo-targets` writes each project's targets: all four kinds, one per service in an ordinary
  project, and the 200-target limit in project 1;
- `slo-measure` times a judging, a day count and a history read per project, each ClickHouse
  statement's time and memory from the server's query log, Postgres statements and rows written,
  the largest project beside the average, the list's read, the claim with and without the
  withholding statements at three counts of open burns, and what buckets on named endpoints would
  cost.

Every number it prints is from the machine it ran on. It states the size it ran at; it never
scales a figure up. Dev stack only: every store must be on localhost.
"""

from __future__ import annotations

import asyncio
import json
import math
import statistics
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from nexovue_platform import slo

# One target per service in an ordinary project, so 200 projects hold the 2,000 targets of the
# cost target (spec NFR1); project 1 holds the limit.
CHECKS_PER_PROJECT: Final = 2
CHECKS_PER_BIG_PROJECT: Final = 10
BIG_PROJECT: Final = 1
HEALTH_ENDPOINT: Final = "GET /health"
# A project's checks alternate between a one-minute and a five-minute interval, so both
# densities of reading are read.
CHECK_INTERVALS: Final = (60, 300)
LATENCY_ASKS_MS: Final = (100.0, 300.0, 1000.0)
OBJECTIVES: Final = ("99.0", "99.5", "99.9", "99.95", "99.99")
WINDOWS: Final = (7, 28)
ORDINARY_OBJECTIVE: Final = "99.9"


@dataclass(frozen=True, slots=True)
class Spec:
    """One target to write: its kind, the source it reads and how it is counted."""

    kind: str
    name: str
    objective: str
    window_days: int
    service: str | None = None
    endpoint: str | None = None
    check: int | None = None
    boundary_index: int | None = None
    asked_ms: float | None = None
    left_out: tuple[str, ...] = ()


def _boundary(asked_ms: float) -> int:
    index = slo.boundary_at_or_under(asked_ms)
    if index is None:
        raise ValueError(f"no boundary at or under {asked_ms} ms")
    return index


def _availability(service: str, objective: str, window: int, *, left_out: bool = True) -> Spec:
    return Spec(
        slo.SERVICE_AVAILABILITY,
        f"{service} availability {objective} {window}d",
        objective,
        window,
        service=service,
        left_out=(HEALTH_ENDPOINT,) if left_out else (),
    )


def _latency(service: str, asked_ms: float, objective: str, window: int) -> Spec:
    return Spec(
        slo.SERVICE_LATENCY,
        f"{service} latency {asked_ms:g}ms {objective} {window}d",
        objective,
        window,
        service=service,
        boundary_index=_boundary(asked_ms),
        asked_ms=asked_ms,
    )


def _endpoint(service: str, endpoint: str, objective: str, window: int) -> Spec:
    return Spec(
        slo.ENDPOINT_AVAILABILITY,
        f"{service} {endpoint} {objective} {window}d",
        objective,
        window,
        service=service,
        endpoint=endpoint,
    )


def _check(index: int, objective: str, window: int) -> Spec:
    return Spec(
        slo.CHECK_AVAILABILITY,
        f"check {index} availability {objective} {window}d",
        objective,
        window,
        check=index,
    )


def plan_targets(services: Sequence[str], checks: int, *, big: bool) -> list[Spec]:
    """An ordinary project's mix, or the 200 a project may hold. Both hold every kind. The
    availability targets leave the health check out, as the form pre-ticks it (FR34), so the
    named-endpoint read is measured where most projects would have it."""
    if big:
        specs = [
            _availability(service, objective, window)
            for service in services
            for window in WINDOWS
            for objective in OBJECTIVES
        ]
        specs += [
            _latency(service, asked, "99.0", 28)
            for service in services
            for asked in LATENCY_ASKS_MS
        ]
        specs += [
            _endpoint(service, f"{verb} /{service}", ORDINARY_OBJECTIVE, window)
            for service in services
            for verb in ("POST", "GET")
            for window in WINDOWS
        ]
        specs += [_check(index, "99.9", window) for index in range(checks) for window in WINDOWS]
        specs += [_availability(service, "99.97", 28) for service in services]
        return specs
    available, latent, routed = services[:4], services[4:6], services[6:8]
    specs = [_availability(service, ORDINARY_OBJECTIVE, 28) for service in available]
    specs += [_latency(service, 300.0, "99.0", 28) for service in latent]
    specs += [_endpoint(service, f"POST /{service}", ORDINARY_OBJECTIVE, 28) for service in routed]
    specs += [_check(index, ORDINARY_OBJECTIVE, 28) for index in range(min(checks, 2))]
    return specs


# ------------------------------------------------------------------ summaries


def percentile(values: Sequence[float], share: float) -> float:
    """The nearest-rank percentile of `values`."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


@dataclass(frozen=True, slots=True)
class Timing:
    count: int
    mean_ms: float
    p95_ms: float
    max_ms: float


def summarise_ms(seconds: Sequence[float]) -> Timing:
    """Times given in seconds, summarised in milliseconds."""
    if not seconds:
        return Timing(0, 0.0, 0.0, 0.0)
    millis = [value * 1000 for value in seconds]
    return Timing(len(millis), statistics.fmean(millis), percentile(millis, 0.95), max(millis))


@dataclass(slots=True)
class Measured:
    """Everything one run observed, written as JSON beside the printed table."""

    machine: dict[str, Any] = field(default_factory=dict)
    size: dict[str, Any] = field(default_factory=dict)
    figures: dict[str, Any] = field(default_factory=dict)

    def write(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as out:
            json.dump(
                {"machine": self.machine, "size": self.size, "figures": self.figures},
                out,
                indent=2,
                default=str,
            )


# ----------------------------------------------------------------- the stores

_CHECK_CONFIG: Final = '{"type": "tcp", "host": "example.test", "port": 443}'
_INSERT_CHECK: Final = """
INSERT INTO checks (org_id, project_id, name, type, config, interval_seconds, locations)
VALUES ($1, $2, $3, 'tcp', $4::jsonb, $5, ARRAY['local'])
"""
_PROJECT_CHECKS: Final = "SELECT id FROM checks WHERE project_id = $1 ORDER BY name"
_INSERT_TARGET: Final = """
INSERT INTO slo_targets (org_id, project_id, name, kind, service_name, endpoint_name, check_id,
    objective_percent, window_days, boundary_index, asked_ms, left_out_endpoints,
    upstream_sampling, definition_key, created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $15)
"""


def check_name(index: int) -> str:
    return f"scale check {index:02d}"


async def add_checks(postgres: str, tenants: Sequence[Any]) -> int:
    """Each project's checks, once: ten in the big project, two elsewhere."""
    import asyncpg

    connection = await asyncpg.connect(postgres)
    made = 0
    try:
        for tenant in tenants:
            wanted = CHECKS_PER_BIG_PROJECT if tenant.number == BIG_PROJECT else CHECKS_PER_PROJECT
            held = await connection.fetchval(
                "SELECT count(*) FROM checks WHERE project_id = $1", uuid.UUID(tenant.project_id)
            )
            for index in range(int(held), wanted):
                await connection.execute(
                    _INSERT_CHECK,
                    uuid.UUID(tenant.org_id),
                    uuid.UUID(tenant.project_id),
                    check_name(index),
                    _CHECK_CONFIG,
                    CHECK_INTERVALS[index % len(CHECK_INTERVALS)],
                )
                made += 1
    finally:
        await connection.close()
    return made


async def add_targets(postgres: str, tenants: Sequence[Any], services: Sequence[str]) -> int:
    """Each project's targets, once, as rows the API's create would write (no history read yet:
    `history_read_version` is 0, so the scheduler's hand-out queues one)."""
    import asyncpg

    connection = await asyncpg.connect(postgres)
    made = 0
    now = datetime.now(UTC)
    try:
        for tenant in tenants:
            project = uuid.UUID(tenant.project_id)
            if await connection.fetchval(
                "SELECT count(*) FROM slo_targets WHERE project_id = $1", project
            ):
                continue
            checks = [row["id"] for row in await connection.fetch(_PROJECT_CHECKS, project)]
            big = tenant.number == BIG_PROJECT
            for spec in plan_targets(services, len(checks), big=big):
                check_id = None if spec.check is None else checks[spec.check]
                key = slo.definition_key(
                    spec.kind,
                    objective=Decimal(spec.objective),
                    window_days=spec.window_days,
                    service_name=spec.service,
                    endpoint_name=spec.endpoint,
                    check_id=check_id,
                    boundary_index=spec.boundary_index,
                )
                await connection.execute(
                    _INSERT_TARGET,
                    uuid.UUID(tenant.org_id),
                    project,
                    spec.name,
                    spec.kind,
                    spec.service,
                    spec.endpoint,
                    check_id,
                    Decimal(spec.objective),
                    spec.window_days,
                    spec.boundary_index,
                    spec.asked_ms,
                    list(spec.left_out),
                    None if spec.kind == slo.CHECK_AVAILABILITY else "no",
                    key,
                    now,
                )
                made += 1
    finally:
        await connection.close()
    return made


def minutes_ago(moment: datetime, now: datetime) -> float:
    return (now - moment) / timedelta(minutes=1)


def wall(started: float) -> float:
    return time.perf_counter() - started


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


# ------------------------------------------------------------ the measurement

READER_DEFAULT: Final = "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue"
MINUTE: Final = timedelta(minutes=1)
# The seeded history ends five minutes before the seed ran; judge at its last minute, and read
# "now" four minutes later, as the scheduler's own sweep would.
SETTLE_LAG: Final = timedelta(minutes=4)
CLAIM_LIMIT: Final = 100
CLAIM_ROUNDS: Final = 5
BURN_OPEN_COUNTS: Final = (0, 200)
# The sources a burn is made of, written over the last hour so every tier sees them fail.
FAILING_FOR: Final = timedelta(minutes=75)
SLOW_BUCKET: Final = 60
WRITE_TABLES: Final = (
    "slo_status",
    "slo_daily_counts",
    "slo_targets",
    "incidents",
    "notifications",
    "slo_target_changes",
)


@dataclass(frozen=True, slots=True)
class StatementStat:
    name: str
    count: int
    mean_ms: float
    p95_ms: float
    max_ms: float
    mean_rows: float
    mean_mib: float
    max_mib: float


def slo_statements() -> dict[str, str]:
    """F19's ClickHouse statements by name and shape, from the scheduler's own store module."""
    from scale import shape

    from nexovue_scheduler.slos import store

    return {
        name: shape(value)
        for name, value in vars(store).items()
        if name.endswith("_SQL") and isinstance(value, str)
    }


def statement_stats(
    rows: Sequence[tuple[str, int, int, int]], statements: dict[str, str]
) -> list[StatementStat]:
    """Group logged queries (text, milliseconds, bytes of memory, rows read) by statement."""
    from scale import shape

    grouped: dict[str, list[tuple[int, int, int]]] = {}
    for query, millis, memory, read_rows in rows:
        logged = shape(query)
        name = next((n for n, sql in statements.items() if sql == logged), None)
        if name is not None:
            grouped.setdefault(name, []).append((millis, memory, read_rows))
    stats = []
    for name, found in sorted(grouped.items()):
        durations = [float(m) for m, _, _ in found]
        mebibytes = [mem / 2**20 for _, mem, _ in found]
        stats.append(
            StatementStat(
                name,
                len(found),
                statistics.fmean(durations),
                percentile(durations, 0.95),
                max(durations),
                statistics.fmean(r for _, _, r in found),
                statistics.fmean(mebibytes),
                max(mebibytes),
            )
        )
    return stats


def query_log(client: Any, user: str, since: datetime) -> list[tuple[str, int, int, int]]:
    client.command("SYSTEM FLUSH LOGS")
    result = client.query(
        """
        SELECT query, query_duration_ms, memory_usage, read_rows
        FROM system.query_log
        WHERE type = 'QueryFinish' AND user = {user:String} AND event_time >= {since:DateTime}
        """,
        parameters={"user": user, "since": since},
    )
    return [(str(q), int(m), int(mem), int(r)) for q, m, mem, r in result.result_rows]


async def table_writes(connection: Any) -> dict[str, tuple[int, int]]:
    """Rows inserted and updated so far, per table the SLO work writes, from the server's own
    counters. They lag a backend's work by up to a second, so read after a pause."""
    rows = await connection.fetch(
        "SELECT relname, n_tup_ins, n_tup_upd FROM pg_stat_user_tables WHERE relname = ANY($1)",
        list(WRITE_TABLES),
    )
    return {row["relname"]: (int(row["n_tup_ins"]), int(row["n_tup_upd"])) for row in rows}


def writes_between(
    before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]
) -> dict[str, tuple[int, int]]:
    moved = {}
    for table, (inserted, updated) in after.items():
        was_inserted, was_updated = before.get(table, (0, 0))
        if (inserted, updated) != (was_inserted, was_updated):
            moved[table] = (inserted - was_inserted, updated - was_updated)
    return moved


def peak_rss_mib() -> float:
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def machine_facts() -> dict[str, Any]:
    import os
    import platform

    memory = {}
    with open("/proc/meminfo", encoding="utf-8") as meminfo:
        for line in meminfo:
            key, _, value = line.partition(":")
            memory[key] = value.strip()
    return {
        "cpus": os.cpu_count(),
        "kernel": platform.release(),
        "memory_total": memory.get("MemTotal"),
        "note": "shared with other builders while measuring; not a deployed server",
    }


@dataclass(slots=True)
class Job:
    """One job's wall time and how many Postgres statements it ran."""

    project: str
    seconds: float
    statements: int


async def run_jobs(names: Sequence[tuple[str, Any]], counter: dict[str, int]) -> list[Job]:
    """Run each (project, coroutine factory) one after another, timing it."""
    jobs = []
    for project, make in names:
        counter["statements"] = 0
        started = time.perf_counter()
        await make()
        jobs.append(Job(project, wall(started), counter["statements"]))
    return jobs


def job_summary(jobs: Sequence[Job], largest: str) -> dict[str, Any]:
    timing = summarise_ms([job.seconds for job in jobs])
    big = [job for job in jobs if job.project == largest]
    return {
        "jobs": timing.count,
        "mean_ms": round(timing.mean_ms, 1),
        "p95_ms": round(timing.p95_ms, 1),
        "max_ms": round(timing.max_ms, 1),
        "total_s": round(sum(job.seconds for job in jobs), 2),
        "largest_project_ms": round(big[0].seconds * 1000, 1) if big else None,
        "mean_postgres_statements": round(statistics.fmean(job.statements for job in jobs), 1)
        if jobs
        else 0,
    }


_COUNT_BURNS: Final = (
    "SELECT count(*) FROM incidents WHERE condition_kind = 'slo_burn' AND resolved_at IS NULL"
)
_PROJECT_OF_TARGET: Final = (
    "SELECT t.id, t.kind, t.org_id, t.project_id FROM slo_targets AS t "
    "WHERE t.project_id = ANY($1::uuid[]) AND t.deleted_at IS NULL ORDER BY t.project_id, t.name"
)
_FAILING_SERVICE_ROWS: Final = """
INSERT INTO service_stats_1m (tenant_id, derived_retention_days, project_id, service_name, minute,
                              request_count, error_count, latency_buckets)
SELECT {tenant:String}, 30, {project:String}, service,
       toDateTime({end:DateTime}) - toIntervalMinute(tick.number), 500, 500,
       arrayReduce('sumForEachState', [arrayMap(
           i -> if(i = {slow:UInt8}, toUInt32(500), toUInt32(0)), range(64))])
FROM numbers({minutes:UInt32}) AS tick ARRAY JOIN {services:Array(String)} AS service
"""
_FAILING_ENDPOINT_ROWS: Final = """
INSERT INTO endpoint_stats_1m (tenant_id, derived_retention_days, project_id, service_name,
                               span_kind, span_name, minute, request_count, error_count)
SELECT {tenant:String}, 30, {project:String}, service, 'SERVER', concat(verb, ' /', service),
       toDateTime({end:DateTime}) - toIntervalMinute(tick.number), 500, 500
FROM numbers({minutes:UInt32}) AS tick
ARRAY JOIN {services:Array(String)} AS service
ARRAY JOIN ['POST', 'GET'] AS verb
"""
_CHANNELS_OF_TENANTS: Final = (
    "SELECT id, org_id, project_id FROM (SELECT c.id, c.org_id, p.id AS project_id, "
    "row_number() OVER (PARTITION BY c.org_id) AS n FROM notification_channels c "
    "JOIN projects p ON p.org_id = c.org_id "
    "WHERE c.kind = 'owners' AND c.org_id = ANY($1::uuid[])) "
    "x WHERE n = 1"
)


def inject_failures(
    client: Any, tenant: Any, services: Sequence[str], *, end: datetime, everything: bool
) -> None:
    """Make `services` fail every request, and be slow, for the last hour and a quarter before
    `end`. With `everything`, their endpoints fail as well, so every kind's target burns."""
    keys = {
        "tenant": tenant.org_id,
        "project": tenant.project_id,
        "end": end,
        "minutes": int(FAILING_FOR / timedelta(minutes=1)),
        "services": list(services),
    }
    client.command(_FAILING_SERVICE_ROWS, parameters=keys | {"slow": SLOW_BUCKET})
    if everything:
        client.command(_FAILING_ENDPOINT_ROWS, parameters=keys)


def inject_check_outages(
    client: Any, tenant: Any, checks: Sequence[tuple[str, int]], end: datetime
) -> int:
    """Every run of each check failing for the last hour and a quarter, as the prober writes
    them."""
    import seed as history

    project = history.Project(tenant.org_id, tenant.project_id, tenant.timezone, 30)
    start = end - FAILING_FOR
    written = 0
    for check_id, interval in checks:
        runs = (
            history.CheckRun(check_id, at, False, "down", True, False, 1)
            for at in (
                start + timedelta(seconds=n * interval)
                for n in range(int(FAILING_FOR.total_seconds() // interval))
            )
        )
        written += history._write(
            client, history._READING_INSERT, (history._reading_json(project, run) for run in runs)
        )
    return written


def patch(module: Any, **names: Any) -> None:
    """Put `names` on `module`, as a test's monkeypatch would, and for the same reason: to run
    the claim as it would be with a statement away. The module's own constants are Final."""
    for name, value in names.items():
        module.__dict__[name] = value


async def claim_times(
    engine: Any, owners: Sequence[Any], *, now: datetime, without_burns: bool = False
) -> dict[str, Any]:
    """The delivery claim over every project's owners channel, each holding a due message: as it
    runs now, and with F19's hold-back statements switched off. Each round is rolled back, so the
    rows stay for the next. The with-and-without difference is what the statements cost."""
    from sqlalchemy import text

    from nexovue_scheduler.alerts import outbox

    async def release_nothing(*_: Any, **__: Any) -> int:
        return 0

    real = (outbox._BURN_INVOLVED, outbox.release)
    findings_sql = text(
        "INSERT INTO findings (org_id, project_id, scope, service_name, severity, state, title, "
        "signals, started_at) VALUES (:org, :project, 'service', 'claim-load', 'warning', 'open', "
        "'claim load', '[]', now()) RETURNING id"
    )
    notice_sql = text(
        "INSERT INTO notifications (org_id, project_id, finding_id, channel_id, kind, sequence, "
        "severity, deliver_after, created_at) VALUES (:org, :project, :finding, :channel, "
        "'opened', 0, 'warning', :due, :due)"
    )
    seconds: dict[str, list[float]] = {"with": [], "without": []}
    channels = 0
    try:
        for label in ("with", "without") * CLAIM_ROUNDS:
            if label == "without":
                patch(outbox, _BURN_INVOLVED=text("SELECT false"), release=release_nothing)
            else:
                patch(outbox, _BURN_INVOLVED=real[0], release=real[1])
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    if without_burns:
                        # Inside the round, so the rows are back afterwards: what the claim
                        # costs where no burn is open.
                        await connection.execute(
                            text(
                                "UPDATE incidents SET resolved_at = now() WHERE "
                                "condition_kind = 'slo_burn' AND resolved_at IS NULL"
                            )
                        )
                        await connection.execute(
                            text(
                                "UPDATE notifications SET state = 'dropped' WHERE state = "
                                "'pending' AND incident_id IN (SELECT id FROM incidents WHERE "
                                "condition_kind = 'slo_burn')"
                            )
                        )
                    for channel in owners:
                        finding = await connection.scalar(
                            findings_sql,
                            {"org": channel["org_id"], "project": channel["project_id"]},
                        )
                        await connection.execute(
                            notice_sql,
                            {
                                "org": channel["org_id"],
                                "project": channel["project_id"],
                                "finding": finding,
                                "channel": channel["id"],
                                "due": now - timedelta(minutes=1),
                            },
                        )
                    started = time.perf_counter()
                    claimed = await outbox.claim(connection, now=now, limit=CLAIM_LIMIT)
                    seconds[label].append(wall(started))
                    channels = claimed.channels
                finally:
                    await transaction.rollback()
    finally:
        patch(outbox, _BURN_INVOLVED=real[0], release=real[1])
    return {
        "channels_claimed": channels,
        **{
            label: {
                "rounds": len(values),
                "mean_ms": round(statistics.fmean(values) * 1000, 1),
                "max_ms": round(max(values) * 1000, 1),
            }
            for label, values in seconds.items()
        },
    }


async def measure(
    *,
    postgres: str,
    clickhouse: Any,
    reader_dsn: str,
    tenants: Sequence[Any],
    services: Sequence[str],
    out: Measured,
    settled_at: datetime | None = None,
) -> None:
    """Run F19's jobs for `tenants` in this process and fill `out`. Phases, in the order the
    scheduler would do them for a new target: history, judging, counting, then burns open and
    the delivery claim beside them."""
    from urllib.parse import urlparse

    import asyncpg
    from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

    from nexovue_scheduler.slos import job
    from nexovue_scheduler.slos.store import connect_store

    started_at = datetime.now(UTC).replace(microsecond=0)
    reader_user = urlparse(reader_dsn).username or "nexovue_reader"
    store = connect_store(reader_dsn)
    engine = create_async_engine(postgres.replace("postgresql://", "postgresql+asyncpg://"))
    stats = await asyncpg.connect(postgres)
    counter = {"statements": 0}
    for method in ("execute", "scalar"):
        original = getattr(AsyncConnection, method)

        def counted(self: Any, *a: Any, _original: Any = original, **k: Any) -> Any:
            counter["statements"] += 1
            return _original(self, *a, **k)

        setattr(AsyncConnection, method, counted)

    # Projects were seeded at different times, so a run names the minute every one of them
    # holds a day before; with none named it is the newest minute any project has.
    last_minute = clickhouse.query("SELECT max(minute) FROM service_stats_1m").result_rows[0][0]
    settled = settled_at or last_minute.replace(tzinfo=UTC, second=0, microsecond=0)
    now = settled + SETTLE_LAG
    ids = [uuid.UUID(t.project_id) for t in tenants]
    rows = await stats.fetch(_PROJECT_OF_TARGET, ids)
    by_project: dict[str, list[Any]] = {}
    for row in rows:
        by_project.setdefault(str(row["project_id"]), []).append(row)
    largest = max(by_project, key=lambda p: len(by_project[p]))
    kinds = {kind: sum(1 for r in rows if r["kind"] == kind) for kind in slo.KINDS}
    out.size = {
        "projects": len(tenants),
        "services": len(tenants) * len(services),
        "targets": len(rows),
        "targets_by_kind": kinds,
        "largest_project_targets": len(by_project[largest]),
        "history_days": 28,
        "settled_minute": settled.isoformat(),
    }
    missing = [kind for kind, count in kinds.items() if count == 0]
    if missing:
        raise SystemExit(f"the measured mix lacks {missing}: AC50 fails if a kind is absent")

    phases: dict[str, Any] = {}
    claim: dict[str, Any] = {}
    # By reference, so a run that fails late still leaves what it measured before.
    out.figures = {"phases": phases, "claim": claim}

    async def phase(name: str, jobs: Sequence[tuple[str, Any]]) -> list[Job]:
        before = await table_writes(stats)
        done = await run_jobs(jobs, counter)
        await asyncio.sleep(1.5)
        after = await table_writes(stats)
        phases[name] = {
            **job_summary(done, str(largest)),
            "postgres_rows_written": writes_between(before, after),
            "process_peak_rss_mib": round(peak_rss_mib(), 1),
        }
        return done

    projects = [str(t.project_id) for t in tenants]
    await phase(
        "history",
        [
            (
                row["project_id"].__str__(),
                lambda row=row: job.read_slo_history(engine, store, row["id"], now=now),
            )
            for row in rows
        ],
    )
    for round_name, at in (("judge_first", settled), ("judge_steady", settled + MINUTE)):
        await phase(
            round_name,
            [
                (p, lambda p=p, at=at: job.judge_slos(engine, store, uuid.UUID(p), at))
                for p in projects
            ],
        )
    for round_name in ("count_first", "count_steady"):
        await phase(
            round_name,
            [
                (p, lambda p=p: job.count_slos(engine, store, uuid.UUID(p), now=now))
                for p in projects
            ],
        )

    owners = [
        dict(row)
        for row in await stats.fetch(_CHANNELS_OF_TENANTS, [uuid.UUID(t.org_id) for t in tenants])
    ]
    claim["open_burns_0"] = {
        "open": 0,
        **await claim_times(engine, owners, now=now, without_burns=True),
    }
    claim["open_burns_seeded_history"] = {
        "open": await stats.fetchval(_COUNT_BURNS),
        **await claim_times(engine, owners, now=now),
    }
    # 200 open across the install: one service failing in every project.
    for tenant in tenants:
        inject_failures(
            clickhouse, tenant, services[:1], end=settled + 2 * MINUTE, everything=False
        )
    await phase(
        "judge_with_one_burn_per_project",
        [
            (p, lambda p=p: job.judge_slos(engine, store, uuid.UUID(p), settled + 2 * MINUTE))
            for p in projects
        ],
    )
    claim["open_burns_install"] = {
        "open": await stats.fetchval(_COUNT_BURNS),
        **await claim_times(engine, owners, now=now),
    }
    # The big project with every source failing, so every one of its targets burns.
    big = next(t for t in tenants if t.project_id == largest)
    inject_failures(clickhouse, big, services, end=settled + 3 * MINUTE, everything=True)
    checks = [
        (str(r["id"]), CHECK_INTERVALS[i % len(CHECK_INTERVALS)])
        for i, r in enumerate(await stats.fetch(_PROJECT_CHECKS, uuid.UUID(big.project_id)))
    ]
    inject_check_outages(clickhouse, big, checks, settled + 3 * MINUTE)
    await phase(
        "judge_big_project_all_burning",
        [
            (
                largest,
                lambda: job.judge_slos(engine, store, uuid.UUID(largest), settled + 3 * MINUTE),
            )
        ],
    )
    claim["open_burns_big_project"] = {
        "open": await stats.fetchval(_COUNT_BURNS),
        "open_in_big_project": await stats.fetchval(
            "SELECT count(*) FROM incidents WHERE project_id = $1 AND condition_kind = 'slo_burn' "
            "AND resolved_at IS NULL",
            uuid.UUID(largest),
        ),
        **await claim_times(engine, owners, now=now),
    }

    log = query_log(clickhouse, reader_user, started_at)
    statements = [asdict(s) for s in statement_stats(log, slo_statements())]
    storage = clickhouse.query(
        "SELECT table, sum(rows), sum(bytes_on_disk) FROM system.parts "
        "WHERE active AND database = currentDatabase() AND table IN "
        "('service_stats_1m', 'endpoint_stats_1m', 'metrics_gauge') GROUP BY table"
    ).result_rows
    buckets = clickhouse.query(
        "SELECT sum(column_data_compressed_bytes), sum(rows) FROM system.parts_columns "
        "WHERE active AND database = currentDatabase() AND table = 'service_stats_1m' "
        "AND column = 'latency_buckets'"
    ).result_rows[0]
    out.figures |= {
        "clickhouse_statements": statements,
        "storage": {t: {"rows": int(r), "bytes": int(b)} for t, r, b in storage},
        "bucket_column": {"compressed_bytes": int(buckets[0]), "rows": int(buckets[1])},
    }
    await stats.close()
    await engine.dispose()
