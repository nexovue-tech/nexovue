"""Measure how bursty and how variable the dev stack's services really are (F6, spec D15).

The F6 simulation broke the false-alarm budget on short failure bursts, at a rate that was a guess.
This reads the per-minute rollup the OpenTelemetry Demo has filled over some days, and reports what
the guess should have been: how often failures burst, for how long, and how much each service's
failure rate and latency wander.

Dev stack only: it refuses any host but localhost, and reads across every project on purpose.
Run with `just detection-measure` after `just dev` has run for at least 3 days.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

# The spec's normal-sensitivity minimum change for *failing more* (Product values).
MIN_POINTS: Final = 0.02
MIN_FAILURES: Final = 5
WINDOW_MINUTES: Final = 5
# A burst minute is this unlikely at the hour's normal rate, so ordinary noise is never a burst.
BURST_TAIL: Final = 1e-3
# Bursts shorter than this are what D15 asks about: report them, or treat them as noise.
SHORT_BURST_MINUTES: Final = 10
LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})

_PER_MINUTE: Final = """
SELECT project_id, service_name, minute,
       sum(request_count) AS requests,
       sum(error_count) AS errors,
       quantilesMerge(0.5, 0.95, 0.99)(latency_quantiles)[2] AS p95_ms
FROM endpoint_stats_1m
WHERE minute >= now() - toIntervalDay({days:UInt16})
GROUP BY project_id, service_name, minute
ORDER BY project_id, service_name, minute
"""


@dataclass(frozen=True, slots=True)
class Minute:
    at: datetime
    requests: int
    errors: int
    p95_ms: float


@dataclass(frozen=True, slots=True)
class ServiceReport:
    project_id: str
    service: str
    minutes: int
    median_requests_per_minute: float
    bursts_per_day: float
    short_bursts_per_day: float
    burst_minutes_median: float | None
    burst_minutes_p90: float | None
    # Five-minute windows clearing the normal-sensitivity minimum on their own: the decisive number.
    windows_past_minimum_per_day: float
    hourly_failure_rate_log_sd: float | None
    minute_p95_log_mad: float | None


def poisson_tail(at_least: int, mean: float) -> float:
    """P(X >= at_least) for X ~ Poisson(mean), summed directly; failures per minute stay small."""
    if at_least <= 0:
        return 1.0
    if mean <= 0:
        return 0.0
    term = math.exp(-mean)
    below = term
    for k in range(1, at_least):
        term *= mean / k
        below += term
    return max(0.0, 1.0 - below)


def hourly_rates(minutes: Sequence[Minute]) -> dict[int, float]:
    """Each hour of day's failure rate across all days: the normal a burst is measured against."""
    requests: dict[int, int] = defaultdict(int)
    errors: dict[int, int] = defaultdict(int)
    for minute in minutes:
        requests[minute.at.hour] += minute.requests
        errors[minute.at.hour] += minute.errors
    return {hour: errors[hour] / requests[hour] for hour in requests if requests[hour]}


def burst_lengths(minutes: Sequence[Minute], rates: dict[int, float]) -> list[int]:
    """Runs of burst minutes, a one-minute gap allowed inside a run."""
    lengths: list[int] = []
    run = gap = 0
    for minute in minutes:
        normal = rates.get(minute.at.hour, 0.0)
        bursting = (
            minute.requests > 0
            and minute.errors / minute.requests >= normal + MIN_POINTS
            and poisson_tail(minute.errors, minute.requests * normal) < BURST_TAIL
        )
        if bursting:
            run, gap = run + 1 + gap, 0
        elif run and gap == 0:
            gap = 1
        else:
            if run:
                lengths.append(run)
            run = gap = 0
    if run:
        lengths.append(run)
    return lengths


def windows_past_minimum(minutes: Sequence[Minute], rates: dict[int, float]) -> int:
    """Five-minute windows whose failures alone clear the normal-sensitivity minimum change."""
    count = 0
    for end in range(WINDOW_MINUTES, len(minutes) + 1):
        window = minutes[end - WINDOW_MINUTES : end]
        requests = sum(minute.requests for minute in window)
        errors = sum(minute.errors for minute in window)
        normal = rates.get(window[-1].at.hour, 0.0)
        if requests and errors >= MIN_FAILURES and errors / requests >= normal + MIN_POINTS:
            count += 1
    return count


def _log_sd(values: Iterable[float]) -> float | None:
    logs = [math.log(value) for value in values if value > 0]
    return statistics.stdev(logs) if len(logs) >= 3 else None


def _log_mad(values: Iterable[float]) -> float | None:
    logs = [math.log(value) for value in values if value > 0]
    if len(logs) < 3:
        return None
    middle = statistics.median(logs)
    return 1.4826 * statistics.median(abs(value - middle) for value in logs)


def report(project_id: str, service: str, minutes: Sequence[Minute]) -> ServiceReport:
    days = max(len(minutes) / 1440, 1 / 1440)
    rates = hourly_rates(minutes)
    lengths = sorted(burst_lengths(minutes, rates))
    by_hour: dict[tuple[str, int], list[Minute]] = defaultdict(list)
    for minute in minutes:
        by_hour[(minute.at.date().isoformat(), minute.at.hour)].append(minute)
    hourly = [
        sum(m.errors for m in group) / total
        for group in by_hour.values()
        if (total := sum(m.requests for m in group))
    ]
    return ServiceReport(
        project_id=project_id,
        service=service,
        minutes=len(minutes),
        median_requests_per_minute=statistics.median(m.requests for m in minutes) if minutes else 0,
        bursts_per_day=len(lengths) / days,
        short_bursts_per_day=sum(1 for n in lengths if n < SHORT_BURST_MINUTES) / days,
        burst_minutes_median=statistics.median(lengths) if lengths else None,
        burst_minutes_p90=lengths[math.ceil(0.9 * len(lengths)) - 1] if lengths else None,
        windows_past_minimum_per_day=windows_past_minimum(minutes, rates) / days,
        hourly_failure_rate_log_sd=_log_sd(hourly),
        minute_p95_log_mad=_log_mad(m.p95_ms for m in minutes if m.requests >= 20),
    )


def _read(url: str, days: int) -> dict[tuple[str, str], list[Minute]]:
    from urllib.parse import urlparse

    import clickhouse_connect  # Here, so the analysis is testable without a driver.

    parsed = urlparse(url)
    if parsed.hostname not in LOCAL_HOSTS:
        raise SystemExit(f"refusing {parsed.hostname}: this reads every tenant, so dev stack only")
    client = clickhouse_connect.get_client(
        host=parsed.hostname,
        port=parsed.port or 18123,
        username=parsed.username or "nexovue_reader",
        password=parsed.password or "",
        database=(parsed.path or "/nexovue").lstrip("/"),
    )
    series: dict[tuple[str, str], list[Minute]] = defaultdict(list)
    for project_id, service, at, requests, errors, p95 in client.query(
        _PER_MINUTE, parameters={"days": days}
    ).result_rows:
        series[(str(project_id), str(service))].append(
            Minute(at=at, requests=int(requests), errors=int(errors), p95_ms=float(p95 or 0))
        )
    return series


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument(
        "--clickhouse", default="http://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue"
    )
    parser.add_argument("--days", type=int, default=7, help="how far back to read (default 7)")
    parser.add_argument("--out", type=Path, default=Path("detection-measure.json"))
    args = parser.parse_args(argv)

    try:
        series = _read(args.clickhouse, args.days)
    except Exception as error:  # The driver's errors share no base worth naming here.
        print(f"Could not read ClickHouse ({type(error).__name__}). Is `just dev` running?")
        return 2
    reports = [
        report(project, service, minutes)
        for (project, service), minutes in sorted(series.items())
        if minutes
    ]
    if not reports:
        print("No rollup rows in that period. Is `just dev` running, and is the demo sending?")
        return 1
    days_seen = max(r.minutes for r in reports) / 1440
    print(f"{len(reports)} services, up to {days_seen:.1f} days of minutes.\n")
    header = ("service", "rpm", "bursts/d", "short/d", "len p50", "past-min/d")
    print(f"{header[0]:32} " + " ".join(f"{h:>10}" for h in header[1:]))
    for r in reports:
        p50 = "—" if r.burst_minutes_median is None else f"{r.burst_minutes_median:.0f}"
        print(
            f"{r.service[:32]:32} {r.median_requests_per_minute:10.1f} {r.bursts_per_day:10.2f} "
            f"{r.short_bursts_per_day:10.2f} {p50:>10} {r.windows_past_minimum_per_day:10.1f}"
        )
    if days_seen < 3:
        print(
            "\nUnder 3 days of data, and D15 asks for 3. Leave the demo running and run this again."
        )
    args.out.write_text(
        json.dumps({"days": args.days, "services": [asdict(r) for r in reports]}, indent=1)
    )
    print(f"\nWritten to {args.out}. Send that file back to continue the F6 design.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
