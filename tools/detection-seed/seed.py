"""Write weeks of synthetic per-minute service history into the dev ClickHouse (F6, plan T2c.4).

Anomaly detection learns each service's normal from four weeks of `service_stats_1m`, so a fresh dev
stack would sit in *learning* for weeks. The receiver accepts spans only up to 7 days old
(`clocktrust.PastSkew`), so `tools/gateseed` cannot reach back that far: this writes the rollup
rows directly, shaped like a business's traffic, and ends just before the live data begins.

The history is synthetic. A detection pass on it shows the path works, not that detection is right.

Dev stack only: it refuses any host but localhost, for ClickHouse and Postgres alike.
Run with `just detection-seed` (options: `just detection-seed --help`).
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import math
import random
import sys
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, Final
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})

# The worker's latency buckets (apps/workers/internal/pipeline/derive/services.go): 64 buckets over
# log-spaced boundaries from 1 ms to 60 s. Bucket i holds durations above bound i-1 and at most
# bound i. test_seed.py reads the Go constants and formula, so the two cannot drift apart.
LATENCY_BUCKET_COUNT: Final = 64
LATENCY_FLOOR_MS: Final = 1.0
LATENCY_CEILING_MS: Final = 60_000.0
LATENCY_BOUNDS: Final[tuple[float, ...]] = tuple(
    LATENCY_FLOOR_MS * (LATENCY_CEILING_MS / LATENCY_FLOOR_MS) ** ((i + 1) / LATENCY_BUCKET_COUNT)
    for i in range(LATENCY_BUCKET_COUNT - 1)
)

# The OpenTelemetry Demo 3.0.0's services that receive requests or consume messages, by the
# OTEL_SERVICE_NAME its compose files set. Used only when the project has no live rows to copy.
DEMO_SERVICES: Final = (
    "accounting",
    "ad",
    "cart",
    "checkout",
    "currency",
    "email",
    "fraud-detection",
    "frontend",
    "frontend-proxy",
    "image-provider",
    "payment",
    "product-catalog",
    "quote",
    "recommendation",
    "shipping",
)

DEFAULT_CLICKHOUSE: Final = "http://nexovue:nexovue_dev@localhost:18123/nexovue"
DEFAULT_POSTGRES: Final = "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
# The seeded history ends this far before now, short of the minutes the worker still has open.
LIVE_LAG: Final = timedelta(minutes=5)
WEEKEND_FACTOR: Final = 0.45
NIGHT_FACTOR: Final = 0.15
BATCH_ROWS: Final = 20_000

_INPUT: Final = (
    "tenant_id String, derived_retention_days UInt16, project_id String, service_name String, "
    "minute DateTime, request_count UInt64, error_count UInt64, latency_buckets Array(UInt32)"
)
# The worker's own statement (derive/servicewriter.go), so seeded rows hold the same state.
_INSERT: Final = (
    "INSERT INTO service_stats_1m (tenant_id, derived_retention_days, project_id, service_name, "
    "minute, request_count, error_count, latency_buckets) "
    "SELECT tenant_id, derived_retention_days, project_id, service_name, minute, request_count, "
    f"error_count, arrayReduce('sumForEachState', [latency_buckets]) FROM input('{_INPUT}') "
    "FORMAT JSONEachRow"
)
_LIVE_SERVICES: Final = """
SELECT DISTINCT service_name FROM service_stats_1m
WHERE tenant_id = {tenant:String} AND project_id = {project:String}
  AND minute >= now() - toIntervalDay(7)
ORDER BY service_name
"""
_EARLIEST: Final = """
SELECT min(minute), count() FROM service_stats_1m
WHERE tenant_id = {tenant:String} AND project_id = {project:String}
"""
_READ_BACK: Final = """
SELECT count(), uniqExact(service_name), min(minute), max(minute), sum(request_count),
       sum(error_count), arraySum(sumForEachMerge(latency_buckets))
FROM service_stats_1m
WHERE tenant_id = {tenant:String} AND project_id = {project:String}
  AND minute BETWEEN {start:DateTime} AND {end:DateTime}
"""
_PROJECT: Final = """
SELECT projects.id::text, projects.org_id::text, projects.timezone,
       project_retention.derived_retention_days
FROM projects
JOIN organizations ON organizations.id = projects.org_id
JOIN project_retention ON project_retention.project_id = projects.id
WHERE projects.deleted_at IS NULL
  AND (projects.id::text = $1 OR (projects.slug = $1 AND organizations.slug = $2))
"""


@dataclass(frozen=True, slots=True)
class Project:
    tenant_id: str
    project_id: str
    timezone: str
    derived_retention_days: int


@dataclass(frozen=True, slots=True)
class Profile:
    """One service's character: how busy, how fast, how often it fails."""

    scale: float
    median_ms: float
    sigma: float
    failure_rate: float


@dataclass(frozen=True, slots=True)
class Row:
    service_name: str
    minute: datetime
    request_count: int
    error_count: int
    latency_buckets: tuple[int, ...]


def require_local(url: str, what: str) -> None:
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing {what} host {host}: this writes synthetic history, dev only")


def latency_bucket(duration_ms: float) -> int:
    """The bucket the worker counts a duration in: the first bound at or above it."""
    if math.isnan(duration_ms) or duration_ms <= 0:
        return 0
    return bisect.bisect_left(LATENCY_BOUNDS, duration_ms)


def load_factor(local: datetime) -> float:
    """Share of peak traffic at a local time: a business-day curve, lower at weekends."""
    hour = local.hour + local.minute / 60
    # A bell from 07:00 to 21:00 peaking mid-afternoon, over a floor of night traffic.
    day = math.sin(math.pi * (hour - 7) / 14) ** 2 if 7 <= hour <= 21 else 0.0
    factor = NIGHT_FACTOR + (1 - NIGHT_FACTOR) * day
    return factor * (WEEKEND_FACTOR if local.weekday() >= 5 else 1.0)


def profile(rng: random.Random) -> Profile:
    return Profile(
        scale=math.exp(rng.uniform(math.log(0.08), 0.0)),
        median_ms=math.exp(rng.uniform(math.log(2.0), math.log(250.0))),
        sigma=rng.uniform(0.35, 0.8),
        failure_rate=math.exp(rng.uniform(math.log(0.0005), math.log(0.01))),
    )


def poisson(rng: random.Random, mean: float) -> int:
    if mean <= 0:
        return 0
    if mean > 30:  # A normal approximation is close enough here and keeps the run fast.
        return max(0, round(rng.gauss(mean, math.sqrt(mean))))
    limit, k, product = math.exp(-mean), 0, rng.random()
    while product > limit:
        k += 1
        product *= rng.random()
    return k


def spread(rng: random.Random, count: int, mu: float, sigma: float) -> tuple[int, ...]:
    """`count` lognormal(mu, sigma) durations, bucketed as the worker buckets them."""
    buckets = [0] * LATENCY_BUCKET_COUNT
    if count <= LATENCY_BUCKET_COUNT:
        for _ in range(count):
            buckets[latency_bucket(rng.lognormvariate(mu, sigma))] += 1
        return tuple(buckets)
    # Many requests: each bucket's expected share, rounded by largest remainder to sum exactly.
    cdf = [0.0, *(_lognormal_cdf(bound, mu, sigma) for bound in LATENCY_BOUNDS), 1.0]
    exact = [count * (cdf[i + 1] - cdf[i]) for i in range(LATENCY_BUCKET_COUNT)]
    for i, share in enumerate(exact):
        buckets[i] = int(share)
    by_remainder = sorted(range(LATENCY_BUCKET_COUNT), key=lambda i: exact[i] - buckets[i])
    for i in by_remainder[len(by_remainder) - (count - sum(buckets)) :]:
        buckets[i] += 1
    return tuple(buckets)


def _lognormal_cdf(x: float, mu: float, sigma: float) -> float:
    return 0.5 * math.erfc(-(math.log(x) - mu) / (sigma * math.sqrt(2)))


def generate(
    services: Sequence[str],
    start: datetime,
    end: datetime,
    zone: tzinfo,
    peak_rpm: float,
    seed: int,
) -> Iterator[Row]:
    """Every service's rows for each minute from start to end inclusive; quiet minutes are skipped,
    as the worker writes nothing for a minute with no requests."""
    for service in services:
        rng = random.Random(f"{seed}:{service}")
        character = profile(rng)
        at = start
        while at <= end:
            load = load_factor(at.astimezone(zone))
            # Over-dispersed, as real traffic is: the rate itself wanders a little each minute.
            requests = poisson(
                rng, peak_rpm * character.scale * load * rng.lognormvariate(0.0, 0.1)
            )
            if requests:
                errors = min(requests, poisson(rng, requests * character.failure_rate))
                # Busier minutes run slightly slower, plus a small minute-to-minute wobble.
                mu = math.log(character.median_ms) + 0.15 * load + rng.gauss(0.0, 0.05)
                yield Row(
                    service_name=service,
                    minute=at,
                    request_count=requests,
                    error_count=errors,
                    latency_buckets=spread(rng, requests, mu, character.sigma),
                )
            at += timedelta(minutes=1)


def window(now: datetime, weeks: int, earliest: datetime | None) -> tuple[datetime, datetime]:
    """The minutes to seed: `weeks` back from just before the live data, never overlapping rows the
    project already has, which would be added to rather than replaced."""
    end = now.replace(second=0, microsecond=0) - LIVE_LAG
    start = end - timedelta(weeks=weeks)
    if earliest is not None:
        if earliest <= start:
            raise SystemExit(
                f"this project already has rows from {earliest:%Y-%m-%d %H:%M} UTC, as far back as "
                f"{weeks} weeks: it is seeded already"
            )
        end = min(end, earliest - timedelta(minutes=1))
    return start, end


def _json_row(project: Project, row: Row) -> dict[str, Any]:
    return {
        "tenant_id": project.tenant_id,
        "derived_retention_days": project.derived_retention_days,
        "project_id": project.project_id,
        "service_name": row.service_name,
        "minute": int(row.minute.timestamp()),
        "request_count": row.request_count,
        "error_count": row.error_count,
        "latency_buckets": row.latency_buckets,
    }


async def _resolve(postgres: str, project: str, org: str) -> Project:
    import asyncpg  # Here, so the generator is testable without a driver.

    connection = await asyncpg.connect(postgres)
    try:
        found = await connection.fetch(_PROJECT, project, org)
    finally:
        await connection.close()
    if len(found) != 1:
        raise SystemExit(f"no live project {project!r} (organization {org!r}) in {postgres}")
    project_id, tenant_id, timezone, retention = found[0]
    return Project(tenant_id, project_id, timezone, retention)


def _clickhouse(url: str) -> Any:
    import clickhouse_connect

    parsed = urlparse(url)
    return clickhouse_connect.get_client(
        host=parsed.hostname,
        port=parsed.port or 18123,
        username=parsed.username or "nexovue",
        password=parsed.password or "",
        database=(parsed.path or "/nexovue").lstrip("/"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--clickhouse", default=DEFAULT_CLICKHOUSE, help="the admin account")
    parser.add_argument("--postgres", default=DEFAULT_POSTGRES)
    parser.add_argument("--project", default="production", help="slug or id (default production)")
    parser.add_argument(
        "--org", default="acme", help="the organization a slug is in (default acme)"
    )
    parser.add_argument(
        "--tenant-id", help="with --project as an id: skip Postgres and write these ids as given"
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        help="derived retention to write (default: the plan's, or 30 with --tenant-id)",
    )
    parser.add_argument(
        "--services", help="comma-separated (default: the project's live services, or the demo's)"
    )
    parser.add_argument("--weeks", type=int, default=4, help="how far back (default 4)")
    parser.add_argument("--seed", type=int, default=6, help="the same seed writes the same rows")
    parser.add_argument("--rpm", type=float, default=120.0, help="the busiest service's peak rpm")
    args = parser.parse_args(argv)

    require_local(args.clickhouse, "ClickHouse")
    if args.tenant_id:
        uuid.UUID(args.project)  # Ids given directly must be ids, not a slug.
        project = Project(args.tenant_id, args.project, "UTC", args.retention_days or 30)
    else:
        require_local(args.postgres, "Postgres")
        project = asyncio.run(_resolve(args.postgres, args.project, args.org))
        if args.retention_days:
            project = Project(
                project.tenant_id, project.project_id, project.timezone, args.retention_days
            )

    client = _clickhouse(args.clickhouse)
    ids = {"tenant": project.tenant_id, "project": project.project_id}
    if args.services:
        services = [name.strip() for name in args.services.split(",") if name.strip()]
    else:
        live = client.query(_LIVE_SERVICES, parameters=ids).result_rows
        services = [str(name) for (name,) in live] or list(DEMO_SERVICES)
    earliest_at, existing = client.query(_EARLIEST, parameters=ids).result_rows[0]
    earliest = earliest_at.replace(tzinfo=UTC) if existing else None
    start, end = window(datetime.now(UTC), args.weeks, earliest)

    print(
        f"Seeding project {project.project_id} (tenant {project.tenant_id}, zone "
        f"{project.timezone}, retention {project.derived_retention_days} d): {len(services)} "
        f"services, {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC."
    )
    batch: list[str] = []
    written = 0
    rows = generate(services, start, end, ZoneInfo(project.timezone), args.rpm, args.seed)
    for row in rows:
        batch.append(json.dumps(_json_row(project, row), separators=(",", ":")))
        if len(batch) == BATCH_ROWS:
            client.command(_INSERT, data="\n".join(batch).encode())
            written += len(batch)
            batch.clear()
            print(f"  {written} rows")
    if batch:
        client.command(_INSERT, data="\n".join(batch).encode())
        written += len(batch)

    count, kinds, first, last, requests, errors, bucketed = client.query(
        _READ_BACK, parameters={**ids, "start": start, "end": end}
    ).result_rows[0]
    print(
        f"Wrote {written} rows. Read back: {count} rows, {kinds} services, {first} to {last}, "
        f"{requests} requests, {errors} failures, {bucketed} in the merged latency buckets."
    )
    if bucketed != requests:
        print("The merged buckets do not add up to the requests: the rows are not what was sent.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
