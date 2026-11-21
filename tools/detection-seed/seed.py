"""Write weeks of synthetic per-minute service history into the dev ClickHouse (F6, plan T2c.4).

Anomaly detection learns each service's normal from four weeks of `service_stats_1m`, so a fresh dev
stack would sit in *learning* for weeks. The receiver accepts spans only up to 7 days old
(`clocktrust.PastSkew`), so `tools/gateseed` cannot reach back that far: this writes the rollup
rows directly, shaped like a business's traffic, and ends just before the live data begins.

The history is synthetic. A detection pass on it shows the path works, not that detection is right.

F19 (plan T2.6) adds what reliability targets read: `endpoint_stats_1m` rows that add up to each
service's, with a `/health`-style probe every busy minute, and `synthetic.check.status` readings for
the project's checks holding every kind of run the uptime rule tells apart: confirmed up and down,
an attempt a retry overruled, a run in a weekly maintenance hour, and a `prober_error`.

F28 (plan T3.0) adds releases: `service_versions_1m` rows split from each service minute so they add
up to it exactly, and the `releases`, `release_periods` and `deploy_events` rows the sync and the
webhook would have written. The first service deploys 60 times, the last just before the history
ends, so a finding `fault.py` then opens has a release before it; one release is older than the
project's limit and lives in Postgres alone, as the rollup's TTL would leave it; the second-to-last
service sends versions without reporting deploys, and the last sends neither.

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
from collections.abc import Iterable, Iterator, Mapping, Sequence
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
# Each service's requests a minute over the last hour, for the history to match.
_LIVE_RATES: Final = """
SELECT service_name, sum(request_count) / greatest(uniqExact(minute), 1)
FROM service_stats_1m
WHERE tenant_id = {tenant:String} AND project_id = {project:String}
  AND minute >= now() - toIntervalHour(1)
GROUP BY service_name
"""
_EARLIEST: Final = """
SELECT min(minute), count() FROM service_stats_1m
WHERE tenant_id = {tenant:String} AND project_id = {project:String}
"""
# F19: the endpoints under each service, and the probe a target leaves out (FR34).
HEALTH_ENDPOINT: Final = "GET /health"
PROBES_PER_MINUTE: Final = 2
# The busier of a service's two routes takes this share of its other requests.
MAIN_ROUTE_SHARE: Final = 0.7
INBOUND_KIND: Final = "SERVER"
_ENDPOINT_INSERT: Final = (
    "INSERT INTO endpoint_stats_1m (tenant_id, derived_retention_days, project_id, service_name, "
    "span_kind, span_name, minute, request_count, error_count) FORMAT JSONEachRow"
)
# A check's runs, as the prober publishes them (apps/scheduler/.../publish.py).
CHECK_STATUS_METRIC: Final = "synthetic.check.status"
PROBER_SERVICE: Final = "nexovue-prober"
# How often each kind of run happens (guesses, for a demo): an attempt a retry overruled, a
# confirmed down, and a run the prober itself could not make.
OVERRULED_SHARE: Final = 0.01
DOWN_SHARE: Final = 0.003
PROBER_ERROR_SHARE: Final = 0.002
# A maintenance hour each week: Sunday 03:00 to 04:00 UTC.
MAINTENANCE_WEEKDAY: Final = 6
MAINTENANCE_HOUR: Final = 3
_READING_INSERT: Final = (
    "INSERT INTO metrics_gauge (timestamp, start_timestamp, received_at, retention_days, "
    "tenant_id, project_id, service_name, metric_name, metric_unit, resource_attrs, attrs, value) "
    "FORMAT JSONEachRow"
)
_CHECKS: Final = """
SELECT id::text, interval_seconds FROM checks WHERE project_id::text = $1 AND enabled
"""

# F28 plan T3.0: what the releases look like (guesses, for a demo).
DEPLOYS_ON_BUSIEST: Final = 60
# The busiest service's last release, this long before the seeded history ends.
LAST_RELEASE_BEFORE_END: Final = timedelta(minutes=30)
# Every other reporting service releases weekly, each at its own hour.
RELEASE_EVERY: Final = timedelta(weeks=1)
# A rollout moves traffic to the new version in even steps over this long.
ROLLOUT: Final = timedelta(minutes=10)
# The release older than the project's limit sits this far past it.
PAST_THE_LIMIT: Final = timedelta(days=2)
NO_VERSION: Final = ""
SEED_SOURCE: Final = "seed"
_VERSION_INPUT: Final = (
    "tenant_id String, derived_retention_days UInt16, project_id String, service_name String, "
    "minute DateTime, version String, request_count UInt64, error_count UInt64, "
    "latency_buckets Array(UInt32)"
)
# The worker's own statement (derive/versionwriter.go).
_VERSION_INSERT: Final = (
    "INSERT INTO service_versions_1m (tenant_id, derived_retention_days, project_id, "
    "service_name, minute, version, request_count, error_count, latency_buckets) "
    "SELECT tenant_id, derived_retention_days, project_id, service_name, minute, version, "
    f"request_count, error_count, arrayReduce('sumForEachState', [latency_buckets]) FROM "
    f"input('{_VERSION_INPUT}') FORMAT JSONEachRow"
)
_RELEASE_INSERT: Final = """
INSERT INTO releases (org_id, project_id, service_name, version, first_seen_at, last_seen_at,
                      first_reported_at, last_reported_at, report_count, last_state,
                      last_activity_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $7, $8, $9, now())
ON CONFLICT (project_id, service_name, version) DO NOTHING
RETURNING id
"""
_PERIOD_INSERT: Final = """
INSERT INTO release_periods (org_id, release_id, project_id, service_name, started_at,
                             start_basis, ended_at, previous_version, already_running, is_return)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, false)
ON CONFLICT DO NOTHING
"""
_DEPLOY_INSERT: Final = """
INSERT INTO deploy_events (org_id, project_id, version, service_name, deployed_at, source,
                           delivery_id, metadata, state, release_id)
VALUES ($1, $2, $3, $4, $5, $6, $7, '{}', 'finished', $8)
ON CONFLICT DO NOTHING
"""
# The sync carries on after the seeded minutes rather than reading them again.
_SYNCED: Final = """
INSERT INTO release_sync_state (org_id, project_id, synced_through) VALUES ($1, $2, $3)
ON CONFLICT (project_id)
DO UPDATE SET synced_through = GREATEST(release_sync_state.synced_through, EXCLUDED.synced_through)
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


@dataclass(frozen=True, slots=True)
class EndpointRow:
    service_name: str
    span_kind: str
    span_name: str
    minute: datetime
    request_count: int
    error_count: int


@dataclass(frozen=True, slots=True)
class CheckRun:
    """One `synthetic.check.status` reading."""

    check_id: str
    at: datetime
    up: bool
    outcome: str
    final: bool
    maintenance: bool
    attempt: int


@dataclass(frozen=True, slots=True)
class VersionRow:
    service_name: str
    minute: datetime
    version: str
    request_count: int
    error_count: int
    latency_buckets: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PlannedRelease:
    """One version of one service, from when it was deployed."""

    service_name: str
    version: str
    started_at: datetime
    reported: bool


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    """A release and its one period, as the sync and the webhook would have written them."""

    service_name: str
    version: str
    started_at: datetime
    start_basis: str
    ended_at: datetime | None
    previous_version: str | None
    already_running: bool
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    reported_at: datetime | None


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


def live_peaks(rates: Mapping[str, float], now: datetime, zone: tzinfo) -> dict[str, float]:
    """The peak that puts each service's history at its live rate at this time of day."""
    load = load_factor(now.astimezone(zone))
    return {service: rate / load for service, rate in rates.items()}


def generate(
    services: Sequence[str],
    start: datetime,
    end: datetime,
    zone: tzinfo,
    peak_rpm: float,
    seed: int,
    peaks: Mapping[str, float] | None = None,
) -> Iterator[Row]:
    """Every service's rows for each minute from start to end inclusive; quiet minutes are skipped,
    as the worker writes nothing for a minute with no requests. A service in `peaks` peaks at that
    rate, so its history matches what it really sends; any other at a random share of `peak_rpm`."""
    for service in services:
        rng = random.Random(f"{seed}:{service}")
        character = profile(rng)
        known = peaks.get(service) if peaks else None
        service_peak = peak_rpm * character.scale if known is None else known
        at = start
        while at <= end:
            load = load_factor(at.astimezone(zone))
            # Over-dispersed, as real traffic is: the rate itself wanders a little each minute.
            requests = poisson(rng, service_peak * load * rng.lognormvariate(0.0, 0.1))
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


def endpoint_rows(rows: Iterable[Row]) -> Iterator[EndpointRow]:
    """Each service minute split into endpoints that add up to it: a probe that never fails, and
    two routes carrying the rest, failures on the routes. A route with no requests writes no row,
    as the worker writes none."""
    for row in rows:
        probes = min(PROBES_PER_MINUTE, row.request_count - row.error_count)
        rest = row.request_count - probes
        main = min(rest, max(row.error_count, round(rest * MAIN_ROUTE_SHARE)))
        main_errors = min(row.error_count, main)
        split = (
            (HEALTH_ENDPOINT, probes, 0),
            (f"POST /{row.service_name}", main, main_errors),
            (f"GET /{row.service_name}", rest - main, row.error_count - main_errors),
        )
        for name, requests, errors in split:
            if requests > 0:
                yield EndpointRow(
                    row.service_name, INBOUND_KIND, name, row.minute, requests, errors
                )


def check_runs(
    check_id: str, start: datetime, end: datetime, *, interval_seconds: int, seed: int
) -> Iterator[CheckRun]:
    """One confirmed run per interval from start to end inclusive. Now and then the first attempt
    fails and a retry overrules it at the same moment, a run is down, or the prober errs; runs in
    the weekly maintenance hour carry the maintenance flag."""
    rng = random.Random(f"{seed}:{check_id}")
    at = start
    while at <= end:
        maintenance = at.weekday() == MAINTENANCE_WEEKDAY and at.hour == MAINTENANCE_HOUR
        roll = rng.random()
        if roll < OVERRULED_SHARE:
            yield CheckRun(check_id, at, False, "down", False, maintenance, 1)
            yield CheckRun(check_id, at, True, "up", True, maintenance, 2)
        elif roll < OVERRULED_SHARE + DOWN_SHARE:
            yield CheckRun(check_id, at, False, "down", True, maintenance, 1)
        elif roll < OVERRULED_SHARE + DOWN_SHARE + PROBER_ERROR_SHARE:
            yield CheckRun(check_id, at, False, "prober_error", True, maintenance, 1)
        else:
            yield CheckRun(check_id, at, True, "up", True, maintenance, 1)
        at += timedelta(seconds=interval_seconds)


def release_plan(
    services: Sequence[str], start: datetime, end: datetime, *, retention_days: int
) -> list[PlannedRelease]:
    """Every service's releases, oldest first. The first deploys `DEPLOYS_ON_BUSIEST` times
    in the window, after one release older than the project's limit; the last sends no
    version; the second-to-last sends versions but reports nothing; the rest release weekly."""
    plan: list[PlannedRelease] = []
    silent = services[-1] if len(services) >= 2 else None
    unreported = services[-2] if len(services) >= 3 else None
    for index, service in enumerate(services):
        if service == silent:
            continue
        if index == 0:
            last = end - LAST_RELEASE_BEFORE_END
            step = (last - start) / (DEPLOYS_ON_BUSIEST - 1)
            expired = end - timedelta(days=retention_days) - PAST_THE_LIMIT
            starts = [min(expired, start - ROLLOUT)]
            starts += [_whole_minute(start + step * n) for n in range(DEPLOYS_ON_BUSIEST - 1)]
            starts.append(last)
        else:
            # The first at the window's start, so every seeded minute has a version.
            offset = timedelta(days=1, hours=3 * index)
            starts = [start]
            at = start + offset
            while at < end:
                starts.append(at)
                at += RELEASE_EVERY
        plan.extend(
            PlannedRelease(service, f"v1.{number}", at, reported=service != unreported)
            for number, at in enumerate(starts)
        )
    return plan


def _whole_minute(at: datetime) -> datetime:
    return at.replace(second=0, microsecond=0)


def version_rows(rows: Iterable[Row], plan: Sequence[PlannedRelease]) -> Iterator[VersionRow]:
    """Each service minute split by version, adding up to it in requests, failures and every
    latency bucket. During a rollout the new version takes an even step more each minute,
    from a version that was counted: one older than the rows has none to hand over."""
    counted: set[tuple[str, str]] = set()
    by_service: dict[str, list[PlannedRelease]] = {}
    for release in sorted(plan, key=lambda one: one.started_at):
        by_service.setdefault(release.service_name, []).append(release)
    for row in rows:
        releases = by_service.get(row.service_name, [])
        now = bisect.bisect_right([release.started_at for release in releases], row.minute) - 1
        if now < 0:
            yield VersionRow(
                row.service_name,
                row.minute,
                NO_VERSION,
                row.request_count,
                row.error_count,
                row.latency_buckets,
            )
            continue
        current = releases[now]
        into = row.minute - current.started_at + timedelta(minutes=1)
        before = releases[now - 1].version if now else None
        counted.add((row.service_name, current.version))
        if before is None or (row.service_name, before) not in counted or into >= ROLLOUT:
            yield VersionRow(
                row.service_name,
                row.minute,
                current.version,
                row.request_count,
                row.error_count,
                row.latency_buckets,
            )
            continue
        yield from _split(row, before, current.version, into / ROLLOUT)


def _split(row: Row, old: str, new: str, share: float) -> Iterator[VersionRow]:
    """One minute in two, each bucket by `share`, failures kept within each side's requests."""
    taken = tuple(math.floor(count * share) for count in row.latency_buckets)
    left = tuple(count - part for count, part in zip(row.latency_buckets, taken, strict=True))
    new_requests, old_requests = sum(taken), sum(left)
    new_errors = min(new_requests, math.floor(row.error_count * share))
    old_errors = row.error_count - new_errors
    if old_errors > old_requests:
        new_errors += old_errors - old_requests
        old_errors = old_requests
    for version, requests, errors, buckets in (
        (old, old_requests, old_errors, left),
        (new, new_requests, new_errors, taken),
    ):
        if requests:
            yield VersionRow(row.service_name, row.minute, version, requests, errors, buckets)


def seen_minutes(rows: Iterable[VersionRow]) -> dict[tuple[str, str], tuple[datetime, datetime]]:
    """Each named version's first and last counted minute."""
    seen: dict[tuple[str, str], tuple[datetime, datetime]] = {}
    for _ in noting(rows, seen):
        pass
    return seen


def noting(
    rows: Iterable[VersionRow], seen: dict[tuple[str, str], tuple[datetime, datetime]]
) -> Iterator[VersionRow]:
    """The rows as they pass, recording each named version's first and last minute, so
    weeks of rows are written without being held."""
    for row in rows:
        if row.version != NO_VERSION:
            key = (row.service_name, row.version)
            first, last = seen.get(key, (row.minute, row.minute))
            seen[key] = (min(first, row.minute), max(last, row.minute))
        yield row


def release_records(
    plan: Sequence[PlannedRelease], seen: Mapping[tuple[str, str], tuple[datetime, datetime]]
) -> list[ReleaseRecord]:
    """A period starts at its first counted minute, or its reported time before any; it ends
    when the next version's rollout has finished, or when the next starts if this one was
    never counted. The first version of a service, with no report, was already running when
    counting began (FR43)."""
    records: list[ReleaseRecord] = []
    by_service: dict[str, list[PlannedRelease]] = {}
    for release in sorted(plan, key=lambda one: one.started_at):
        by_service.setdefault(release.service_name, []).append(release)
    for releases in by_service.values():
        for number, release in enumerate(releases):
            counted = seen.get((release.service_name, release.version))
            following = releases[number + 1] if number + 1 < len(releases) else None
            records.append(
                ReleaseRecord(
                    service_name=release.service_name,
                    version=release.version,
                    started_at=counted[0] if counted else release.started_at,
                    start_basis="seen" if counted else "reported",
                    ended_at=None
                    if following is None
                    else following.started_at + (ROLLOUT if counted else timedelta(0)),
                    previous_version=releases[number - 1].version if number else None,
                    already_running=number == 0 and not release.reported,
                    first_seen_at=counted[0] if counted else None,
                    last_seen_at=counted[1] if counted else None,
                    reported_at=release.started_at if release.reported else None,
                )
            )
    return records


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


def _version_json(project: Project, row: VersionRow) -> dict[str, Any]:
    return {
        "tenant_id": project.tenant_id,
        "derived_retention_days": project.derived_retention_days,
        "project_id": project.project_id,
        "service_name": row.service_name,
        "minute": int(row.minute.timestamp()),
        "version": row.version,
        "request_count": row.request_count,
        "error_count": row.error_count,
        "latency_buckets": row.latency_buckets,
    }


def _endpoint_json(project: Project, row: EndpointRow) -> dict[str, Any]:
    return {
        "tenant_id": project.tenant_id,
        "derived_retention_days": project.derived_retention_days,
        "project_id": project.project_id,
        "service_name": row.service_name,
        "span_kind": row.span_kind,
        "span_name": row.span_name,
        "minute": int(row.minute.timestamp()),
        "request_count": row.request_count,
        "error_count": row.error_count,
    }


def _reading_json(project: Project, run: CheckRun) -> dict[str, Any]:
    stamp = run.at.strftime("%Y-%m-%d %H:%M:%S.000")
    return {
        "timestamp": stamp,
        "start_timestamp": stamp,
        "received_at": stamp,
        "retention_days": project.derived_retention_days,
        "tenant_id": project.tenant_id,
        "project_id": project.project_id,
        "service_name": PROBER_SERVICE,
        "metric_name": CHECK_STATUS_METRIC,
        "metric_unit": "1",
        "resource_attrs": {"synthetic": {"check": {"id": run.check_id, "type": "http"}}},
        "attrs": {
            "synthetic": {
                "check": {
                    "outcome": run.outcome,
                    "run": {"id": f"seed-{run.check_id}-{int(run.at.timestamp())}-{run.attempt}"},
                    "attempt": run.attempt,
                    "final": run.final,
                    "maintenance": run.maintenance,
                    "location": "local",
                }
            }
        },
        "value": 1.0 if run.up else 0.0,
    }


def _write(client: Any, statement: str, rows: Iterator[dict[str, Any]]) -> int:
    batch: list[str] = []
    written = 0
    for row in rows:
        batch.append(json.dumps(row, separators=(",", ":")))
        if len(batch) == BATCH_ROWS:
            client.command(statement, data="\n".join(batch).encode())
            written += len(batch)
            batch.clear()
    if batch:
        client.command(statement, data="\n".join(batch).encode())
        written += len(batch)
    return written


async def _project_checks(postgres: str, project_id: str) -> list[tuple[str, int]]:
    import asyncpg

    connection = await asyncpg.connect(postgres)
    try:
        return [(str(row[0]), int(row[1])) for row in await connection.fetch(_CHECKS, project_id)]
    finally:
        await connection.close()


async def _write_releases(
    postgres: str, project: Project, records: Sequence[ReleaseRecord], synced_through: datetime
) -> int:
    """The releases, their periods and their reported deploys, in one transaction; a release
    already held is left as it is. Answers how many were written."""
    import asyncpg

    org_id, project_id = uuid.UUID(project.tenant_id), uuid.UUID(project.project_id)
    written = 0
    connection = await asyncpg.connect(postgres)
    try:
        async with connection.transaction():
            for record in records:
                reported = record.reported_at is not None
                release_id = await connection.fetchval(
                    _RELEASE_INSERT,
                    org_id,
                    project_id,
                    record.service_name,
                    record.version,
                    record.first_seen_at,
                    record.last_seen_at,
                    record.reported_at,
                    1 if reported else 0,
                    "finished" if reported else None,
                )
                if release_id is None:
                    continue
                written += 1
                await connection.execute(
                    _PERIOD_INSERT,
                    org_id,
                    release_id,
                    project_id,
                    record.service_name,
                    record.started_at,
                    record.start_basis,
                    record.ended_at,
                    record.previous_version,
                    record.already_running,
                )
                if reported:
                    await connection.execute(
                        _DEPLOY_INSERT,
                        org_id,
                        project_id,
                        record.version,
                        record.service_name,
                        record.reported_at,
                        SEED_SOURCE,
                        f"{SEED_SOURCE}-{record.service_name}-{record.version}",
                        release_id,
                    )
            await connection.execute(_SYNCED, org_id, project_id, synced_through)
    finally:
        await connection.close()
    return written


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
    parser.add_argument(
        "--checks",
        help="comma-separated check ids to write readings for, every minute (default: the "
        "project's enabled checks at their own intervals; none with --tenant-id)",
    )
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
    zone = ZoneInfo(project.timezone)
    rates = client.query(_LIVE_RATES, parameters=ids).result_rows
    peaks = live_peaks({str(name): float(rate) for name, rate in rates}, datetime.now(UTC), zone)
    rows = generate(services, start, end, zone, args.rpm, args.seed, peaks)
    written = _write(client, _INSERT, (_json_row(project, row) for row in rows))
    # The same seed generates the same rows, so a second pass splits them into endpoints
    # without holding weeks of rows in memory.
    again = generate(services, start, end, zone, args.rpm, args.seed, peaks)
    written_endpoints = _write(
        client, _ENDPOINT_INSERT, (_endpoint_json(project, e) for e in endpoint_rows(again))
    )
    print(f"  {written} rows, {written_endpoints} endpoint rows")

    plan = release_plan(services, start, end, retention_days=project.derived_retention_days)
    seen: dict[tuple[str, str], tuple[datetime, datetime]] = {}
    versions = version_rows(generate(services, start, end, zone, args.rpm, args.seed, peaks), plan)
    written_versions = _write(
        client, _VERSION_INSERT, (_version_json(project, row) for row in noting(versions, seen))
    )
    if args.tenant_id:
        print(f"  {written_versions} version rows; no releases without Postgres")
    else:
        releases = asyncio.run(
            _write_releases(args.postgres, project, release_records(plan, seen), end)
        )
        print(f"  {written_versions} version rows, {releases} releases with their periods")

    if args.checks:
        checks = [(name.strip(), 60) for name in args.checks.split(",") if name.strip()]
    elif args.tenant_id:
        checks = []
    else:
        checks = asyncio.run(_project_checks(args.postgres, project.project_id))
    for check_id, interval in checks:
        readings = _write(
            client,
            _READING_INSERT,
            (
                _reading_json(project, run)
                for run in check_runs(
                    check_id, start, end, interval_seconds=interval, seed=args.seed
                )
            ),
        )
        print(f"  {readings} readings for check {check_id}")

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
