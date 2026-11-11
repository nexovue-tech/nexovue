"""The Step 1a cost run for `service_versions_1m` (F28 plan T1.6, design *Performance and scale*).

The same traffic is sent twice over: once to a worker built at the commit before the version rollup
(the baseline), once to one built with it. Each run sends `send.py` traffic through the real
receiver for a fixed time, samples the worker's memory every 10 seconds, waits for the minutes to
drain, and then reads what the rollups hold. `report` turns the runs into the gate's table.

    just release-measure run --label baseline --repeat 1 --org-id ... --project-id ... --key ...
    (swap the worker binary)
    just release-measure run --label versions --repeat 1 ...
    just release-measure report --results <dir>

The worker is run by the operator, not by this tool, because the baseline is a different build. Its
process is found by `--worker-pattern` on its command line.

Every figure is from a development machine and labelled demo data. Dev stack only.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import yaml

ROOT: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "detection-measure"))

import scale  # noqa: E402  Its process and size readers, rather than a second copy.
import send  # noqa: E402

# The design's thresholds (F28 design, *The gate*): guesses, and the founder sees the numbers.
# The four ratios bound the demo's rollout traffic; churn at the arrival caps has a row of its own.
STEADY_ROWS_MAX: Final = 1.5
ROLLOUT_ROWS_MAX: Final = 3.0
PEAK_MEMORY_INCREASE_MAX: Final = 0.15
DISK_RATIO_MAX: Final = 2.0
DEMO_TRAFFIC: Final = "many-services"
CHURN_TRAFFIC: Final = "churn"
TRAFFIC_ROWS: Final = {
    DEMO_TRAFFIC: "one rollout an hour on every service: the design's four ratio rows",
    CHURN_TRAFFIC: "many versions on every partition, at the arrival caps: "
    "worker under GOMEMLIMIT, totals exact",
}
NOT_MEASURED_HERE: Final = (
    "Alert evaluation start delay: needs Step 1b's sync job, which does not exist yet"
)
# Churn's bound is the worker's heap ceiling as the shipped compose file sets it.
WORKER_COMPOSE: Final = ROOT / "deploy" / "compose" / "nexovue.yaml"
MIB: Final = 2**20
# Go's suffixes for `GOMEMLIMIT`, all powers of two (Go's `runtime` package documentation).
GO_SIZE_UNITS: Final = {"B": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40}
GO_SIZE: Final = re.compile(r"^(\d+)(B|KiB|MiB|GiB|TiB)?$")
# FR23: each side needs 20 requests for a failure share, which is what selects `side_by_side`.
FAILURE_FLOOR: Final = 20
SAMPLE_EVERY_SECONDS: Final = 10.0
# How long to wait after sending for the last minute to drain into both rollups.
DRAIN_TIMEOUT_SECONDS: Final = 420.0
ROLLUP_TABLES: Final = ("service_stats_1m", "service_versions_1m", "endpoint_stats_1m")
# What one row is, per table, to count rows as they stand once merged.
ROW_KEYS: Final = {
    "service_stats_1m": "service_name, minute",
    "service_versions_1m": "service_name, minute, version",
    "endpoint_stats_1m": "service_name, span_kind, span_name, minute",
}
LABELS: Final = ("baseline", "versions")
# One project's minutes in a run's window; every read below binds these four.
IN_WINDOW: Final = (
    "tenant_id = {tenant_id:String} AND project_id = {project_id:String} "
    "AND minute >= {start:DateTime} AND minute <= {end:DateTime}"
)
OTHER_VERSION: Final = "__other__"
NOT_A_RELEASE: Final = frozenset({"", OTHER_VERSION})


def go_bytes(value: str) -> int:
    """A `GOMEMLIMIT` in bytes, reading a compose default: `${WORKER_GOMEMLIMIT:-1088MiB}`."""
    text = value.strip()
    if text.startswith("${") and text.endswith("}"):
        _, has_default, text = text[2:-1].partition(":-")
        if not has_default:
            raise ValueError(f"{value!r} names no default size")
    match = GO_SIZE.match(text)
    if match is None:
        raise ValueError(f"{value!r} is not a GOMEMLIMIT size")
    amount, unit = match.groups()
    return int(amount) * GO_SIZE_UNITS[unit or "B"]


def worker_gomemlimit_bytes(compose: Path = WORKER_COMPOSE) -> int:
    services = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]
    return go_bytes(str(services["worker"]["environment"]["GOMEMLIMIT"]))


WORKER_GOMEMLIMIT_BYTES: Final = worker_gomemlimit_bytes()


# ------------------------------------------------------------------ the arithmetic


@dataclass(frozen=True, slots=True)
class Spread:
    least: float
    median: float
    most: float

    @classmethod
    def of(cls, values: Sequence[float]) -> Spread:
        if not values:
            raise ValueError("a spread of nothing")
        return cls(min(values), statistics.median(values), max(values))


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


@dataclass(frozen=True, slots=True)
class Rollout:
    service: str
    versions: tuple[str, str]
    minutes: int
    requests: tuple[int, int]

    @property
    def side_by_side(self) -> bool:
        """Both versions reached the failure floor over the minutes both served (FR23)."""
        return min(self.requests) >= FAILURE_FLOOR


def rollouts(rows: Iterable[tuple[str, datetime, str, int]]) -> list[Rollout]:
    """The rollouts in version rows of (service, minute, version, requests): each pair of release
    versions serving the same minutes of a service, with each side's requests over those minutes.
    A minute with more than two release versions is churn, not a rollout, and is left out."""
    by_minute: dict[tuple[str, datetime], dict[str, int]] = {}
    for service, minute, version, requests in rows:
        if version in NOT_A_RELEASE or requests <= 0:
            continue
        served = by_minute.setdefault((service, minute), {})
        served[version] = served.get(version, 0) + requests
    overlaps: dict[tuple[str, tuple[str, str]], list[dict[str, int]]] = {}
    for (service, _), served in sorted(by_minute.items()):
        if len(served) == 2:
            pair = tuple(sorted(served))
            overlaps.setdefault((service, (pair[0], pair[1])), []).append(served)
    return [
        Rollout(
            service,
            pair,
            len(minutes),
            (sum(m[pair[0]] for m in minutes), sum(m[pair[1]] for m in minutes)),
        )
        for (service, pair), minutes in sorted(overlaps.items())
    ]


def expected_side_by_side(rpm: float, rollout_minutes: int) -> bool:
    """Arithmetic, not measured: an even rollout over L minutes at R requests a minute gives each
    side about R * L / 2 requests in the minutes both serve (`send._new_share`)."""
    shares = [send._new_share(minute, 0, rollout_minutes) for minute in range(rollout_minutes)]
    new_side = rpm * sum(shares)
    old_side = rpm * sum(1 - share for share in shares)
    return min(new_side, old_side) >= FAILURE_FLOOR


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    measured: float | None
    threshold: float
    passed: bool | None
    unit: str = ""


def check(name: str, measured: float | None, threshold: float, unit: str = "") -> Check:
    passed = None if measured is None else measured <= threshold
    return Check(name, measured, threshold, passed, unit)


@dataclass(frozen=True, slots=True)
class TotalsCheck:
    """Whether every versioned run lost nothing, and how many services had versions folded into
    `__other__`: exact totals, but inexact version counts, as FR17 allows."""

    name: str
    exact_runs: int
    runs: int
    folded_per_run: tuple[int, ...]

    @property
    def passed(self) -> bool | None:
        return None if not self.runs else self.exact_runs == self.runs


# ------------------------------------------------------------------ one run


@dataclass(slots=True)
class Memory:
    """The worker's resident memory, sampled while traffic flows."""

    samples: list[int] = field(default_factory=list)

    def sample(self, pattern: str) -> None:
        import os

        found = re.compile(pattern)
        me = os.getpid()
        pids = [
            int(e.name) for e in Path("/proc").iterdir() if e.name.isdigit() and int(e.name) != me
        ]
        rss = sum(scale.process_memory(pid)[0] for pid in pids if found.search(scale.cmdline(pid)))
        if rss:
            self.samples.append(rss)


def sample_until(memory: Memory, pattern: str, stop: threading.Event) -> None:
    while not stop.is_set():
        memory.sample(pattern)
        stop.wait(SAMPLE_EVERY_SECONDS)


def window(ledger: send.Ledger) -> dict[str, Any]:
    return {
        "start": datetime.fromisoformat(ledger.started_at).replace(
            second=0, microsecond=0, tzinfo=None
        ),
        "end": datetime.fromisoformat(ledger.ended_at).replace(tzinfo=None) + timedelta(minutes=1),
    }


def wait_for_drain(client: Any, bound: Mapping[str, Any], expected: int) -> int:
    """Poll until `service_stats_1m` holds every request sent, or the timeout passes."""
    deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
    stored = 0
    while time.monotonic() < deadline:
        stored = int(
            client.query(
                f"SELECT sum(request_count) FROM service_stats_1m WHERE {IN_WINDOW}",
                parameters=bound,
            ).result_rows[0][0]
            or 0
        )
        if stored >= expected:
            return stored
        time.sleep(15)
    return stored


def rows_of(client: Any, table: str, bound: Mapping[str, Any]) -> dict[str, int]:
    where = IN_WINDOW
    stored = client.query(
        f"SELECT count() FROM {table} WHERE {where}", parameters=bound
    ).result_rows[0][0]
    merged = client.query(
        f"SELECT count() FROM (SELECT 1 FROM {table} WHERE {where} GROUP BY {ROW_KEYS[table]})",
        parameters=bound,
    ).result_rows[0][0]
    return {"stored": int(stored), "merged": int(merged)}


def steady_rows(client: Any, bound: Mapping[str, Any]) -> dict[str, int]:
    """Version rows and service rows over the service-minutes that served one version only."""
    single = client.query(
        "SELECT count() FROM (SELECT service_name, minute FROM service_versions_1m "
        f"WHERE {IN_WINDOW} GROUP BY service_name, minute HAVING uniqExact(version) = 1)",
        parameters=bound,
    ).result_rows[0][0]
    return {"service_minutes_with_one_version": int(single)}


def disk(client: Any) -> dict[str, dict[str, int]]:
    """Each rollup's active parts after a final merge, with the requests it holds, so bytes can be
    compared per request whatever else the table holds."""
    found: dict[str, dict[str, int]] = {}
    for table in ROLLUP_TABLES:
        client.command(f"OPTIMIZE TABLE {table} FINAL")
        parts = client.query(
            "SELECT sum(rows), sum(bytes_on_disk), sum(primary_key_bytes_in_memory) "
            "FROM system.parts "
            "WHERE database = currentDatabase() AND active AND table = {table:String}",
            parameters={"table": table},
        ).result_rows[0]
        requests = client.query(f"SELECT sum(request_count) FROM {table}").result_rows[0][0]
        found[table] = {
            "rows": int(parts[0] or 0),
            "bytes_on_disk": int(parts[1] or 0),
            "primary_key_bytes_in_memory": int(parts[2] or 0),
            "requests": int(requests or 0),
        }
    return found


def version_rows(client: Any, bound: Mapping[str, Any]) -> list[tuple[str, datetime, str, int]]:
    return [
        (str(s), m, str(v), int(r))
        for s, m, v, r in client.query(
            "SELECT toString(service_name), minute, toString(version), sum(request_count) "
            f"FROM service_versions_1m WHERE {IN_WINDOW} GROUP BY service_name, minute, version",
            parameters=bound,
        ).result_rows
    ]


def peak_mib(samples: Sequence[int]) -> float:
    return max(samples, default=0) / 2**20


def cmd_run(args: argparse.Namespace) -> int:
    send.require_local(args.receiver, "receiver")
    client = send.clickhouse(args.clickhouse)
    settings = send.Settings(
        mode=args.mode,
        services=args.services,
        rpm=args.rpm,
        rollout_minutes=args.rollout_minutes,
        versions_per_minute=args.versions_per_minute,
    )
    memory, stop = Memory(), threading.Event()
    sampler = threading.Thread(
        target=sample_until, args=(memory, args.worker_pattern, stop), daemon=True
    )
    sampler.start()
    try:
        ledger = send.run(
            settings,
            minutes=args.minutes,
            post=send.poster(args.receiver, args.key),
            seed=args.repeat,
        )
    finally:
        stop.set()
        sampler.join()
    bound = {"tenant_id": args.org_id, "project_id": args.project_id} | window(ledger)
    drained = wait_for_drain(client, bound, ledger.total_requests())
    versions, services = send.stored_totals(client, ledger, args.org_id, args.project_id)
    found = rollouts(version_rows(client, bound))
    result = {
        "demo": "demo data, development machine",
        "label": args.label,
        "repeat": args.repeat,
        "settings": asdict(settings),
        "minutes": args.minutes,
        "window": {"start": ledger.started_at, "end": ledger.ended_at},
        "requests_sent": ledger.total_requests(),
        "requests_drained": drained,
        "refused_exports": ledger.refused_exports,
        "memory_rss_bytes": memory.samples,
        "rows": {table: rows_of(client, table, bound) for table in ROLLUP_TABLES},
        "steady": steady_rows(client, bound),
        "rollouts": [asdict(r) | {"side_by_side": r.side_by_side} for r in found],
        "mismatches": [asdict(m) for m in send.compare(ledger.sent, versions)]
        if versions
        else None,
        "unsummed": send.unsummed_services(versions, services) if versions else None,
        "disk": disk(client),
    }
    out = Path(args.results) / f"{args.label}-{args.mode}-{args.repeat}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str))
    print(
        f"{args.label} {args.mode} repeat {args.repeat}: {ledger.total_requests()} requests, "
        f"{drained} drained, peak RSS {peak_mib(memory.samples):.0f} MiB, written {out}"
    )
    return 0


# ------------------------------------------------------------------ the report


def load(results: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in sorted(results.glob("*.json"))]


def peaks(runs: Sequence[Mapping[str, Any]], label: str, mode: str) -> list[float]:
    return [
        float(max(run["memory_rss_bytes"]))
        for run in runs
        if run["label"] == label and run["settings"]["mode"] == mode and run["memory_rss_bytes"]
    ]


def row_ratio(run: Mapping[str, Any]) -> float | None:
    rows = run["rows"]
    return ratio(rows["service_versions_1m"]["merged"], rows["service_stats_1m"]["merged"])


def steady_ratio(run: Mapping[str, Any]) -> float | None:
    """Over service-minutes with one version, each has exactly one version row; so this is the
    floor the design names, near 1.0 by construction."""
    single = run["steady"]["service_minutes_with_one_version"]
    return 1.0 if single else None


def bytes_per_request(run: Mapping[str, Any], table: str) -> float | None:
    held = run["disk"][table]
    return ratio(held["bytes_on_disk"], held["requests"])


def versioned_runs(runs: Sequence[Mapping[str, Any]], mode: str) -> list[Mapping[str, Any]]:
    return [r for r in runs if r["label"] == "versions" and r["settings"]["mode"] == mode]


def ratio_checks(runs: Sequence[Mapping[str, Any]], mode: str) -> list[Check]:
    """The four ratio rows, which the design sets for the demo's rollout traffic."""
    versioned = versioned_runs(runs, mode)
    base_peaks, version_peaks = peaks(runs, "baseline", mode), peaks(runs, "versions", mode)
    increase = (
        ratio(max(version_peaks) - max(base_peaks), max(base_peaks))
        if base_peaks and version_peaks
        else None
    )
    rollout = [x for x in (row_ratio(r) for r in versioned) if x is not None]
    steady = [x for x in (steady_ratio(r) for r in versioned) if x is not None]
    disk_ratios = [
        x
        for x in (
            ratio(
                bytes_per_request(r, "service_versions_1m") or 0,
                bytes_per_request(r, "service_stats_1m") or 0,
            )
            for r in versioned
        )
        if x is not None
    ]
    return [
        check(
            "Rows in steady traffic, versions over services (floor only)",
            max(steady, default=None),
            STEADY_ROWS_MAX,
        ),
        check(
            "Rows with one rollout an hour on every service",
            max(rollout, default=None),
            ROLLOUT_ROWS_MAX,
        ),
        check("Worker peak memory, increase over the baseline", increase, PEAK_MEMORY_INCREASE_MAX),
        check(
            "Bytes on disk per request, versions over services",
            max(disk_ratios, default=None),
            DISK_RATIO_MAX,
        ),
    ]


def churn_checks(
    runs: Sequence[Mapping[str, Any]], memory_limit_bytes: int
) -> list[Check | TotalsCheck]:
    """The design's row for many versions at the arrival caps: the worker under GOMEMLIMIT, and
    totals exact. Resident memory is compared, which is above what GOMEMLIMIT bounds (Go's
    runtime-managed memory), so the comparison errs towards failing."""
    version_peaks = peaks(runs, "versions", CHURN_TRAFFIC)
    return [
        check(
            "Worker peak memory, under GOMEMLIMIT",
            max(version_peaks) / MIB if version_peaks else None,
            memory_limit_bytes / MIB,
            unit=" MiB",
        ),
        totals_check(versioned_runs(runs, CHURN_TRAFFIC)),
    ]


def gate(
    runs: Sequence[Mapping[str, Any]],
    mode: str,
    memory_limit_bytes: int = WORKER_GOMEMLIMIT_BYTES,
) -> list[Check | TotalsCheck]:
    """The design's rows for one traffic (*The gate*); none for a traffic it does not name."""
    if mode == DEMO_TRAFFIC:
        return list(ratio_checks(runs, mode))
    if mode == CHURN_TRAFFIC:
        return churn_checks(runs, memory_limit_bytes)
    return []


def folded_services(run: Mapping[str, Any]) -> list[str]:
    """The services whose stored `__other__` holds requests (FR17's "Versions were combined")."""
    return sorted(
        {
            m["service"]
            for m in run["mismatches"] or []
            if m["version"] == OTHER_VERSION and m["stored"][0] > 0
        }
    )


def conserved(run: Mapping[str, Any]) -> bool:
    """Each service's versions, `__other__` included, hold exactly the requests and errors sent to
    it, and no named version holds more than was sent: folding moves counts into `__other__` only.
    The mismatches are every (service, version) that differs, so the rest cancel out."""
    balance: dict[str, list[int]] = {}
    for m in run["mismatches"] or []:
        pairs = list(zip(m["stored"], m["sent"], strict=True))  # (requests, errors)
        if m["version"] != OTHER_VERSION and any(kept > sent for kept, sent in pairs):
            return False
        held = balance.setdefault(m["service"], [0, 0])
        for index, (kept, sent) in enumerate(pairs):
            held[index] += kept - sent
    return all(held == [0, 0] for held in balance.values())


def run_is_exact(run: Mapping[str, Any]) -> bool:
    """Nothing lost: all sent was drained, each service's version rows sum to its service row
    (FR16), and its versions hold what was sent to it (`conserved`)."""
    return (
        run["mismatches"] is not None
        and run["unsummed"] is not None
        and run["requests_sent"] == run["requests_drained"]
        and not run["unsummed"]
        and conserved(run)
    )


def exact(runs: Sequence[Mapping[str, Any]]) -> bool:
    """Every versioned run among `runs` is exact."""
    return all(run_is_exact(run) for run in runs if run["label"] == "versions")


def totals_check(versioned: Sequence[Mapping[str, Any]]) -> TotalsCheck:
    return TotalsCheck(
        "Totals exact",
        sum(run_is_exact(run) for run in versioned),
        len(versioned),
        tuple(len(folded_services(run)) for run in versioned),
    )


def per_million(runs: Sequence[Mapping[str, Any]], mode: str) -> dict[str, float | None]:
    versioned = versioned_runs(runs, mode)
    if not versioned:
        return {}
    last = versioned[-1]
    return {
        table: (lambda b: None if b is None else b * 1e6)(bytes_per_request(last, table))
        for table in ROLLUP_TABLES
    }


VERDICTS: Final = {True: "pass", False: "FAIL", None: "not measured"}


def describe(row: Check | TotalsCheck) -> str:
    if isinstance(row, TotalsCheck):
        folded = ", ".join(str(count) for count in row.folded_per_run) or "-"
        return (
            f"exact in {row.exact_runs} of {row.runs} versioned runs; services with versions "
            f"folded into __other__ (totals conserved, versions inexact), per run: {folded}"
        )
    measured = "-" if row.measured is None else f"{row.measured:.3f}{row.unit}"
    return f"{measured} (at most {row.threshold:g}{row.unit})"


def print_rows(rows: Sequence[Check | TotalsCheck], verdict: Callable[[bool | None], str]) -> None:
    for row in rows:
        print(f"{verdict(row.passed):12} {row.name}: {describe(row)}")


def print_report(runs: Sequence[Mapping[str, Any]]) -> None:
    print("DEMO DATA, development machine. Not a server figure.")
    judged: list[Check | TotalsCheck] = []
    for mode in sorted({run["settings"]["mode"] for run in runs}):
        print(f"\n## {mode}: {TRAFFIC_ROWS.get(mode, 'no design row for this traffic')}")
        for label in LABELS:
            values = peaks(runs, label, mode)
            if values:
                spread = Spread.of([v / MIB for v in values])
                print(
                    f"{label:9} peak RSS MiB over {len(values)} runs: "
                    f"least {spread.least:.1f}, median {spread.median:.1f}, most {spread.most:.1f}"
                )
        rows = gate(runs, mode)
        judged += rows
        print_rows(rows, VERDICTS.__getitem__)
        if mode != DEMO_TRAFFIC:
            print("For information only, the demo's bounds, which the design does not set here:")
            print_rows(ratio_checks(runs, mode), lambda _: "info")
        if mode != CHURN_TRAFFIC:
            print_rows([totals_check(versioned_runs(runs, mode))], lambda _: "info")
        for table, figure in per_million(runs, mode).items():
            print(
                f"bytes on disk per million requests, {table}: "
                f"{'-' if figure is None else f'{figure / 2**10:.1f} KiB'}"
            )
        reached = [r for run in versioned_runs(runs, mode) for r in run["rollouts"]]
        if reached:
            print(
                f"side_by_side reached on {sum(r['side_by_side'] for r in reached)} "
                f"of {len(reached)} rollouts"
            )
    passed = [row.passed for row in judged]
    overall = "pass" if passed and all(passed) else "FAIL" if False in passed else "incomplete"
    print(f"\nGate, over the design rows measured here: {overall}")
    print(f"Not measured here: {NOT_MEASURED_HERE}")


def cmd_report(args: argparse.Namespace) -> int:
    runs = load(Path(args.results))
    if not runs:
        raise SystemExit(f"no runs in {args.results}")
    print_report(runs)
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = top.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="one timed run against the worker now running")
    run.add_argument("--label", choices=LABELS, required=True)
    run.add_argument("--repeat", type=int, required=True, help="also the traffic's seed")
    run.add_argument("--mode", choices=send.MODES, default="many-services")
    run.add_argument("--services", type=int, default=50)
    run.add_argument("--rpm", type=float, default=60.0)
    run.add_argument("--rollout-minutes", type=int, default=5)
    run.add_argument("--versions-per-minute", type=int, default=25)
    run.add_argument("--minutes", type=float, default=30.0)
    run.add_argument("--key", required=True)
    run.add_argument("--org-id", required=True)
    run.add_argument("--project-id", required=True)
    run.add_argument("--receiver", default=send.DEFAULT_RECEIVER)
    run.add_argument("--clickhouse", default=send.DEFAULT_CLICKHOUSE)
    run.add_argument("--worker-pattern", default=r"/worker( |$)|bin/worker")
    run.add_argument("--results", default="release-measure")
    run.set_defaults(handler=cmd_run)

    report = commands.add_parser("report", help="the gate's table from the runs written")
    report.add_argument("--results", default="release-measure")
    report.set_defaults(handler=cmd_report)
    return top


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
