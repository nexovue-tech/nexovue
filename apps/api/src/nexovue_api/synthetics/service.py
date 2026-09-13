"""Turning synthetic readings into the shapes the API promises.

No SQL text and no HTTP. What lives here is the arithmetic ClickHouse should not
be asked to do twice: the unit the store keeps against the unit the wire names,
the bucket width a window deserves, and the pivot from one row per phase into
one point per bucket.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Final

from nexovue_api.checks.schemas import MIN_INTERVAL_SECONDS
from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.synthetics import queries
from nexovue_api.synthetics.schemas import (
    PHASE_ORDER,
    CheckResultListResponse,
    CheckResultSummary,
    CheckRun,
    CheckRunListResponse,
    CheckTrend,
    CheckTrendPoint,
    CheckTrendResponse,
    PhaseBreakdownResponse,
    PhaseBucket,
    TlsCertificate,
)
from nexovue_api.window import receive_window

MILLISECONDS_PER_SECOND: Final = 1000.0
PERCENT: Final = 100.0

# One project's checks are bounded at 100 by `checks.schemas`, so this ceiling
# is the same fact stated where the read happens rather than a second policy.
MAX_CHECKS: Final = 200

DEFAULT_RUN_LIMIT: Final = 200
MAX_RUN_LIMIT: Final = 2_000

# Roughly how many points a chart wants when the caller does not say. Enough
# for a shape to be visible, few enough that the browser plots it at once.
TARGET_BUCKETS: Final = 120

# A check cannot run more often than once a minute, so a narrower bucket than
# that holds at most one probe and only makes the series sparser.
MIN_BUCKET_SECONDS: Final = MIN_INTERVAL_SECONDS
MAX_BUCKET_SECONDS: Final = 86_400

# Buckets times phases. Well above what `TARGET_BUCKETS` produces, so it binds
# only on a caller who asked for a very fine bucket over a very long window.
MAX_PHASE_ROWS: Final = 20_000

# One row per check per bucket. Sized so a project at the check cap still gets a full
# window of points rather than a prefix.
MAX_TREND_ROWS: Final = MAX_CHECKS * 200


def resolve_bucket_seconds(from_time: datetime, to_time: datetime, requested: int | None) -> int:
    """The bucket width, either the caller's or one that fits the window."""
    if requested is not None:
        return _clamped_bucket(requested)
    span_seconds = int((to_time - from_time).total_seconds())
    return _clamped_bucket(span_seconds // TARGET_BUCKETS)


def _clamped_bucket(seconds: int) -> int:
    return min(max(seconds, MIN_BUCKET_SECONDS), MAX_BUCKET_SECONDS)


# What a check can be. Enumerated so a typo is refused at the edge rather
# than answering with an empty list the reader would take for "all healthy".
CHECK_TYPES: Final = ("http", "browser")


async def list_check_results(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    check_type: str | None = None,
    down_only: bool = False,
) -> CheckResultListResponse:
    """Every check that reported something in the window, with its readings.

    A check that ran but published nothing in this window is simply absent. That
    is the honest answer here — the definitions endpoint lists what exists, and
    inventing a zeroed row would say "measured, and it was zero".
    """
    parameters = _window_parameters(project_id, from_time, to_time) | {"limit": MAX_CHECKS}
    if check_type is not None:
        parameters["check_type"] = check_type

    rows = await repository.query(
        queries.check_summaries_sql(check_type=check_type is not None, down_only=down_only),
        parameters,
    )
    return CheckResultListResponse(checks=[_to_summary(row) for row in rows])


async def check_trends(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
) -> CheckTrendResponse:
    """Each check's history across the window, for the sparkline in its row.

    A bucket in which nothing succeeded carries no duration. That is the whole reason
    this returns `None` rather than `0.0`: a failed probe has no response time, and a
    line drawn at zero claims the site got instantly fast at the moment it went down.
    """
    bucket_seconds = resolve_bucket_seconds(from_time, to_time, None)
    rows = await repository.query(
        queries.CHECK_TRENDS_SQL,
        _window_parameters(project_id, from_time, to_time)
        | {"bucket_seconds": bucket_seconds, "limit": MAX_TREND_ROWS},
    )

    by_check: dict[str, list[CheckTrendPoint]] = defaultdict(list)
    for row in rows:
        successful = int(row["successful_runs"])
        by_check[values.text(row["check_id"])].append(
            CheckTrendPoint(
                bucket=values.nanos_to_datetime(row["bucket_nanos"]),
                runs=int(row["runs"]),
                successful_runs=successful,
                mean_duration_ms=(
                    float(row["mean_seconds"]) * MILLISECONDS_PER_SECOND if successful else None
                ),
            )
        )

    return CheckTrendResponse(
        window_start=from_time,
        window_end=to_time,
        bucket_seconds=bucket_seconds,
        checks=[
            CheckTrend(check_id=check_id, points=points)
            for check_id, points in sorted(by_check.items())
        ],
    )


async def phase_breakdown(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    check_id: str,
    from_time: datetime,
    to_time: datetime,
    bucket_seconds: int,
) -> PhaseBreakdownResponse:
    """One check's request phases over time, in a shape a chart stacks directly."""
    rows = await repository.query(
        queries.PHASE_BREAKDOWN_SQL,
        _window_parameters(project_id, from_time, to_time)
        | {"check_id": check_id, "bucket_seconds": bucket_seconds, "limit": MAX_PHASE_ROWS},
    )
    phases = _phases_in_request_order({values.text(row["phase"]) for row in rows})
    return PhaseBreakdownResponse(
        check_id=check_id,
        bucket_seconds=bucket_seconds,
        phases=phases,
        buckets=_to_buckets(rows, phases),
    )


async def list_runs(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    check_id: str,
    from_time: datetime,
    to_time: datetime,
    limit: int,
) -> CheckRunListResponse:
    """Individual probes, newest first."""
    rows = await repository.query(
        queries.CHECK_RUNS_SQL,
        _window_parameters(project_id, from_time, to_time) | {"check_id": check_id, "limit": limit},
    )
    # A group with no status point is a duration or a phase whose status
    # reading has not landed yet; it is half a run, not a run.
    return CheckRunListResponse(
        check_id=check_id,
        runs=[_to_run(row) for row in rows if int(row["status_points"])],
    )


def _window_parameters(project_id: str, from_time: datetime, to_time: datetime) -> dict[str, Any]:
    """Both clocks. `received_at` is what prunes partitions; `timestamp` is what
    the caller asked about."""
    received_from, received_to = receive_window(from_time, to_time)
    return {
        "project_id": project_id,
        "from_time": from_time,
        "to_time": to_time,
        "received_from": received_from,
        "received_to": received_to,
    }


def _phases_in_request_order(observed: set[str]) -> list[str]:
    """Known phases in the order a request performs them, then anything new."""
    return sorted(observed, key=_phase_position)


def _phase_position(phase: str) -> tuple[int, str]:
    if phase in PHASE_ORDER:
        return PHASE_ORDER.index(phase), ""
    return len(PHASE_ORDER), phase


def _to_buckets(rows: list[dict[str, Any]], phases: list[str]) -> list[PhaseBucket]:
    """One row per phase becomes one point per bucket carrying every phase.

    Every bucket is filled with every phase, missing ones at zero, so a chart
    never has to decide what an absent key means.
    """
    by_bucket: dict[int, dict[str, float]] = {}
    samples: dict[int, int] = {}
    for row in rows:
        bucket_nanos = int(row["bucket_nanos"])
        durations = by_bucket.setdefault(bucket_nanos, dict.fromkeys(phases, 0.0))
        durations[values.text(row["phase"])] = _milliseconds(row["mean_seconds"])
        samples[bucket_nanos] = samples.get(bucket_nanos, 0) + int(row["samples"])

    return [
        PhaseBucket(
            timestamp=values.nanos_to_datetime(bucket_nanos),
            durations_ms=durations,
            total_ms=sum(durations.values()),
            samples=samples[bucket_nanos],
        )
        for bucket_nanos, durations in sorted(by_bucket.items())
    ]


def _to_summary(row: dict[str, Any]) -> CheckResultSummary:
    runs = int(row["runs"])
    successful = int(row["successful_runs"])
    return CheckResultSummary(
        check_id=values.text(row["check_id"]),
        check_type=values.text(row["check_type"]),
        runs=runs,
        successful_runs=successful,
        uptime_percent=(successful / runs * PERCENT) if runs else 0.0,
        # `argMaxIf` over no matching row returns zero, which would read as
        # "down". Without a run there is no answer, so it is null.
        up=bool(row["latest_up"]) if runs else None,
        latest_status_code=int(row["latest_status_code"]),
        latest_run_at=values.nanos_to_datetime(row["latest_run_nanos"]) if runs else None,
        mean_duration_ms=_milliseconds(row["mean_seconds"]),
        p95_duration_ms=_milliseconds(row["p95_seconds"]),
        max_duration_ms=_milliseconds(row["max_seconds"]),
        tls=_to_certificate(row),
    )


def _to_certificate(row: dict[str, Any]) -> TlsCertificate | None:
    """The certificate reading, or None when the check never made a handshake."""
    if not int(row["tls_readings"]):
        return None
    return TlsCertificate(
        days_remaining=float(row["tls_days_remaining"]),
        issuer=values.text(row["tls_issuer"]),
        server_address=values.text(row["tls_server"]),
    )


def _to_run(row: dict[str, Any]) -> CheckRun:
    return CheckRun(
        timestamp=values.nanos_to_datetime(row["observed_nanos"]),
        up=float(row["up_value"]) == queries.UP_VALUE,
        status_code=int(row["status_code"]),
        duration_ms=_milliseconds(row["duration_seconds"]),
        phase_total_ms=_milliseconds(row["phase_total_seconds"]),
    )


def _milliseconds(seconds: Any) -> float:
    """Seconds in the store, milliseconds on the wire — converted in one place."""
    return float(seconds) * MILLISECONDS_PER_SECOND
