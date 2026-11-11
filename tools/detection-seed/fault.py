"""Make one service look broken, or healthy again, in the dev stack (F22, plan T1.0).

Writes abnormal — or with `--clear`, normal — per-minute `service_stats_1m` rows
for a named service over the last minutes, so the **real** project job opens and
closes a finding on its own judgement. Nothing is inserted into `findings`: the
tool only shapes the telemetry the detection engine reads, which is what makes
the judgeable check honest (plan, *Step 1: Judgeable*).

Run after `just detection-seed`, with the dev stack judging:

    just detection-fault --service checkout           # slower than normal
    just detection-fault --service checkout --clear   # back to normal

Dev stack only: it refuses any host but localhost, as `seed.py` does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from seed import (
    _INSERT,
    DEFAULT_CLICKHOUSE,
    DEFAULT_POSTGRES,
    Profile,
    Project,
    Row,
    _clickhouse,
    _json_row,
    _resolve,
    latency_bucket,
    require_local,
)

# The seeded demo's ordinary shape: what `--clear` returns the service to, and
# what the fault multiplies. Matching `seed.profile`'s middle of the road.
NORMAL: Final = Profile(scale=1.0, median_ms=120.0, sigma=0.5, failure_rate=0.01)

# Far enough outside any band that one window is judged unusual (seed sigma 0.5
# puts the band's edge well under 2x), and matching the demo script's words.
FAULT_TIMES: Final = 4.0

# Long enough to cover detection's judging window with room for settling lag.
DEFAULT_MINUTES: Final = 20
DEFAULT_RPM: Final = 120.0

# How the per-minute requests spread over the latency buckets: a tight cluster
# around the median, so the p95 follows the median it is built from.
_SPREAD: Final = (0.5, 0.25, 0.15, 0.1)
_SPREAD_STEP: Final = 1.35


def fault_rows(
    service: str, *, start: datetime, minutes: int, median_ms: float, rpm: float
) -> Iterator[Row]:
    """One row per minute, every request near `median_ms`: raise the median and
    the p95 rises with it; lower it and the service reads normal."""
    for index in range(minutes):
        buckets = [0] * 64
        requests = max(1, int(rpm))
        for step, share in enumerate(_SPREAD):
            buckets[latency_bucket(median_ms * (_SPREAD_STEP**step))] += int(requests * share)
        shortfall = requests - sum(buckets)
        buckets[latency_bucket(median_ms)] += shortfall
        yield Row(
            service_name=service,
            minute=start + timedelta(minutes=index),
            request_count=requests,
            error_count=max(0, math.floor(requests * NORMAL.failure_rate)),
            latency_buckets=tuple(buckets),
        )


def window(now: datetime, minutes: int) -> datetime:
    """The window's first minute: the last `minutes` full minutes, ending now, so
    the next settled minutes the job judges carry the fault."""
    return now.replace(second=0, microsecond=0) - timedelta(minutes=minutes - 1)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--service", required=True, help="the service to break or heal")
    parser.add_argument(
        "--clear", action="store_true", help="write normal rows instead: the fault recovers"
    )
    parser.add_argument("--clickhouse", default=DEFAULT_CLICKHOUSE, help="the admin account")
    parser.add_argument("--postgres", default=DEFAULT_POSTGRES)
    parser.add_argument("--project", default="production", help="slug or id (default production)")
    parser.add_argument("--org", default="acme", help="the organization a slug is in")
    parser.add_argument("--minutes", type=int, default=DEFAULT_MINUTES)
    parser.add_argument("--rpm", type=float, default=DEFAULT_RPM)
    parser.add_argument(
        "--median-ms", type=float, default=NORMAL.median_ms, help="the service's normal median"
    )
    args = parser.parse_args(argv)

    require_local(args.clickhouse, "ClickHouse")
    require_local(args.postgres, "Postgres")
    project: Project = asyncio.run(_resolve(args.postgres, args.project, args.org))

    median = args.median_ms if args.clear else args.median_ms * FAULT_TIMES
    start = window(datetime.now(UTC), args.minutes)
    rows = list(
        fault_rows(args.service, start=start, minutes=args.minutes, median_ms=median, rpm=args.rpm)
    )
    client = _clickhouse(args.clickhouse)
    client.command(
        _INSERT,
        data="\n".join(
            json.dumps(_json_row(project, row), separators=(",", ":")) for row in rows
        ).encode(),
    )
    shape = "normal" if args.clear else f"about {FAULT_TIMES:g}x slower than normal"
    print(
        f"Wrote {len(rows)} {shape} minutes for {args.service} "
        f"({start:%H:%M} to {rows[-1].minute:%H:%M} UTC). The next project job judges them; "
        f"watch the findings feed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
