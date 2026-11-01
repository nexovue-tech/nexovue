"""Drive the OpenTelemetry Demo's fault flags against nexovue and record what detection found
(F6, plan T8.1). Scoring is `fault_score.py`, over the run log this writes.

The run: one week with no fault (the reference run: every finding is a false alarm), then each
fault 5 times, each at the same weekday and time as a stretch of the reference week, so the
reference run gives the same period to compare with (AC36). Interrupted, it resumes from its log;
an injection whose start passed while it was down is recorded as skipped, never run late.

Flags are set the way the demo's own flagd-ui sets them: the flag file is rewritten whole and
renamed into place, and flagd, which watches the file, picks the change up.

Run with `just fault-bed` (commands: `just fault-bed --help`). Dev stack only: refuses any API
host but localhost. Reads the API with a read token from NEXOVUE_TOKEN, which it never prints.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol

from fault_model import (
    CATCH_QUIET,
    DEMO_VERSION,
    FAULTS,
    REFERENCE,
    ROUNDS,
    FindingSeen,
    Injection,
    Observation,
    Plan,
    ServiceWindow,
    Skipped,
    append,
    events,
    fault_named,
    findings_event,
    flag_event,
    load,
    observation_event,
    opened_at,
    plan_event,
    reference_services_event,
    scaled_value,
    skipped_event,
)
from fault_score import render, score

REPOSITORY: Final = Path(__file__).resolve().parents[2]
DEFAULT_FLAG_FILE: Final = REPOSITORY / "tools/otel-demo/upstream/src/flagd/demo.flagd.json"
DEFAULT_API: Final = "http://localhost:18000"
TOKEN_VARIABLE: Final = "NEXOVUE_TOKEN"
LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
# Each fault is on for 30 minutes, past the 20-minute time to catch, then off for an hour so its
# finding can recover and close before the next fault starts.
DEFAULT_SLOT_MINUTES: Final = 90
DEFAULT_ACTIVE_MINUTES: Final = 30
# An injection is started up to this late; any later and its time to catch would be wrong.
START_GRACE: Final = timedelta(minutes=2)
FINDINGS_POLL: Final = timedelta(hours=1)
SERVICES_LIMIT: Final = 500
REQUEST_TIMEOUT_SECONDS: Final = 30.0


class FlagStore(Protocol):
    def resting(self, flag: str) -> str: ...
    def numeric(self, flag: str, variant: str) -> float: ...
    def add_variant(self, flag: str, variant: str, value: int) -> None: ...
    def set(self, flag: str, variant: str) -> None: ...


class Nexovue(Protocol):
    def services(self, start: datetime, end: datetime) -> list[ServiceWindow]: ...
    def findings(self) -> list[FindingSeen]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
    def sleep_until(self, at: datetime) -> None: ...


# ---------- the flag file ----------


class FlagFile:
    """The demo's `demo.flagd.json`, which flagd watches."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, Any]:
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("flags"), dict):
            raise SystemExit(f"{self.path} is not a flagd flag file")
        return document

    def _flag(self, document: dict[str, Any], flag: str) -> dict[str, Any]:
        found = document["flags"].get(flag)
        if not isinstance(found, dict):
            raise SystemExit(f"{self.path} has no flag {flag!r}: is the demo at {DEMO_VERSION}?")
        return found

    def names(self) -> set[str]:
        return set(self._read()["flags"])

    def resting(self, flag: str) -> str:
        definition = self._flag(self._read(), flag)
        branches = _targeted(definition)
        return str(branches[1] if branches else definition["defaultVariant"])

    def numeric(self, flag: str, variant: str) -> float:
        value = self._flag(self._read(), flag)["variants"].get(variant)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise SystemExit(f"flag {flag!r} variant {variant!r} is not a number")
        return float(value)

    def variants(self, flag: str) -> set[str]:
        return set(self._flag(self._read(), flag)["variants"])

    def add_variant(self, flag: str, variant: str, value: int) -> None:
        document = self._read()
        self._flag(document, flag)["variants"][variant] = value
        self._write(document)

    def set(self, flag: str, variant: str) -> None:
        document = self._read()
        definition = self._flag(document, flag)
        if variant not in definition["variants"]:
            raise SystemExit(f"flag {flag!r} has no variant {variant!r}")
        branches = _targeted(definition)
        # As flagd-ui does: a flag with a targeting rule changes its rule's "on" branch.
        if branches:
            branches[1] = variant
        else:
            definition["defaultVariant"] = variant
        self._write(document)

    def _write(self, document: dict[str, Any]) -> None:
        # Write-then-rename, as flagd-ui does, so flagd never reads a half-written file.
        staging = self.path.with_name(self.path.name + ".fault-bed.tmp")
        staging.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        staging.replace(self.path)


def _targeted(definition: dict[str, Any]) -> list[Any] | None:
    branches = definition.get("targeting", {}).get("if")
    return branches if isinstance(branches, list) and len(branches) == 3 else None


# ---------- nexovue's read API ----------


def require_local(url: str) -> None:
    host = urllib.parse.urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing API host {host}: the fault bed drives the dev stack only")


Fetch = Callable[[str], Any]


def http_fetch(api: str, token: str) -> Fetch:
    """GET a path under /api/v1 and decode its JSON. The token goes in a header, never a message."""

    def fetch(path_and_query: str) -> Any:
        request = urllib.request.Request(
            f"{api.rstrip('/')}/api/v1{path_and_query}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise SystemExit(f"GET {path_and_query.split('?')[0]} answered {error.code}") from None

    return fetch


@dataclass(frozen=True, slots=True)
class NexovueApi:
    fetch: Fetch
    project_id: str

    def services(self, start: datetime, end: datetime) -> list[ServiceWindow]:
        query = urllib.parse.urlencode(
            {
                "project_id": self.project_id,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "limit": SERVICES_LIMIT,
            }
        )
        body = self.fetch(f"/services?{query}")
        return [
            ServiceWindow(
                service=str(s["service_name"]),
                requests=int(s["request_count"]),
                errors=int(s["error_count"]),
                p95_ms=float(s["p95_ms"]),
            )
            for s in body["services"]
        ]

    def findings(self) -> list[FindingSeen]:
        seen: list[FindingSeen] = []
        # Muted findings are listed apart (`muted=true`); a test-bed project mutes nothing, but a
        # muted finding is still a finding the detector opened.
        for muted in ("false", "true"):
            query = urllib.parse.urlencode(
                {"project_id": self.project_id, "state": "all", "muted": muted}
            )
            for raw in self.fetch(f"/findings?{query}")["findings"]:
                seen.append(self._finding(raw))
        return seen

    def _finding(self, raw: dict[str, Any]) -> FindingSeen:
        if raw["scope"] == "project":
            # The list leaves out a project finding's services; its page names them.
            services = tuple(str(s) for s in self.fetch(f"/findings/{raw['id']}")["services"])
        else:
            services = (str(raw["service_name"]),)
        return FindingSeen(
            id=str(raw["id"]),
            opened_at=opened_at(str(raw["id"])),
            scope=str(raw["scope"]),
            services=services,
            signals=tuple(str(s["signal"]) for s in raw["signals"]),
            severity=str(raw["severity"]),
            closed_reason=raw["closed_reason"],
        )


def resolve_project(fetch: Fetch, wanted: str | None) -> str:
    projects = [str(p["id"]) for p in fetch("/projects")["projects"]]
    if wanted:
        if wanted not in projects:
            raise SystemExit(f"the token cannot read project {wanted}")
        return wanted
    if len(projects) != 1:
        raise SystemExit(f"the token reads {len(projects)} projects: name one with --project-id")
    return projects[0]


# ---------- the plan and the runner ----------


def plan_injections(
    faults: Sequence[str],
    rounds: int,
    first_start: datetime,
    slot: timedelta,
    active: timedelta,
    seed: int,
) -> tuple[Injection, ...]:
    """Every fault once per round, shuffled within each round so no fault always lands at the
    same time of day; each slot is the fault on for `active`, then off until the next."""
    if not CATCH_QUIET < active < slot:
        raise SystemExit(
            f"a fault must stay on longer than the longest time to catch ({CATCH_QUIET}) and "
            "shorter than its slot"
        )
    if len(faults) * rounds * slot > REFERENCE:
        raise SystemExit(
            "the injections must fit in one week, so each has its period in the reference run"
        )
    injections: list[Injection] = []
    at = first_start
    for round_number in range(1, rounds + 1):
        order = list(faults)
        random.Random(f"{seed}:{round_number}").shuffle(order)
        for name in order:
            injections.append(Injection(name, round_number, at, at + active))
            at += slot
    return tuple(injections)


@dataclass
class Runner:
    log: Path
    flags: FlagStore
    api: Nexovue
    clock: Clock
    say: Callable[[str], None] = print

    def start(self, plan: Plan) -> None:
        if self.log.exists():
            raise SystemExit(f"{self.log} exists: resume it with `run`, or choose another log")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        append(self.log, plan_event(plan))

    def run(self) -> None:
        run = load(events(self.log))
        plan = run.plan
        done = {(o.fault, o.round) for o in run.observations}
        done |= {(s.fault, s.round) for s in run.skipped}
        self._rest_all(plan)  # A run killed mid-injection left its flag on.
        try:
            self._reference(plan, recorded=run.reference_services is not None)
            for injection in plan.injections:
                if (injection.fault, injection.round) not in done:
                    self._inject(plan, injection)
            self._snapshot()
        finally:
            self._rest_all(plan)
        self.say(f"Done. Score it with: just fault-bed score --log {self.log}")

    def _reference(self, plan: Plan, *, recorded: bool) -> None:
        while (now := self.clock.now()) < plan.reference_end:
            self.clock.sleep_until(min(now + FINDINGS_POLL, plan.reference_end))
            self._snapshot()
        if not recorded:
            services = self.api.services(plan.reference_start, plan.reference_end)
            append(self.log, reference_services_event(w.service for w in services))

    def _inject(self, plan: Plan, injection: Injection) -> None:
        fault = fault_named(injection.fault)
        if self.clock.now() > injection.start + START_GRACE:
            reason = "its start passed while the bed was not running"
            append(self.log, skipped_event(Skipped(injection.fault, injection.round, reason)))
            self.say(f"skipped {injection.fault} round {injection.round}: {reason}")
            return
        self.clock.sleep_until(injection.start)
        if fault.scale_of_default is not None:
            resting = self.flags.numeric(fault.flag, plan.resting[fault.flag])
            self.flags.add_variant(
                fault.flag, fault.variant, scaled_value(resting, fault.scale_of_default)
            )
        self._set(fault.flag, fault.variant)
        self.say(f"{injection.fault} round {injection.round} on until {injection.end:%H:%M} UTC")
        self.clock.sleep_until(injection.end)
        self._set(fault.flag, plan.resting[fault.flag])
        first, last = injection.window
        observation = Observation(
            fault=injection.fault,
            round=injection.round,
            during=tuple(self.api.services(first, last)),
            reference=tuple(self.api.services(first - REFERENCE, last - REFERENCE)),
        )
        append(self.log, observation_event(observation))
        self._snapshot()

    def _set(self, flag: str, variant: str) -> None:
        self.flags.set(flag, variant)
        append(self.log, flag_event(self.clock.now(), flag, variant))

    def _rest_all(self, plan: Plan) -> None:
        for flag, variant in plan.resting.items():
            self.flags.set(flag, variant)

    def _snapshot(self) -> None:
        append(self.log, findings_event(self.clock.now(), self.api.findings()))


class RealClock:
    # Short sleeps, so a laptop that slept through a deadline wakes to it within a minute.
    _STEP_SECONDS: Final = 60.0

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep_until(self, at: datetime) -> None:
        while (remaining := (at - self.now()).total_seconds()) > 0:
            time.sleep(min(remaining, self._STEP_SECONDS))


# ---------- the command line ----------


def resting_variants(flags: FlagStore, faults: Sequence[str]) -> dict[str, str]:
    """Each fault's flag as it is before the run, which is what every injection restores. A fault
    flag already on would make the reference run a faulty one, so it is refused."""
    resting: dict[str, str] = {}
    for name in faults:
        fault = fault_named(name)
        variant = flags.resting(fault.flag)
        if fault.scale_of_default is None and variant != "off":
            raise SystemExit(f"flag {fault.flag} is {variant!r}: turn it off before the run")
        resting[fault.flag] = variant
    return resting


def _faults(names: str | None) -> list[str]:
    if not names:
        return [fault.name for fault in FAULTS]
    chosen = list(dict.fromkeys(name.strip() for name in names.split(",") if name.strip()))
    for name in chosen:
        fault_named(name)
    return chosen


def _connect(args: argparse.Namespace) -> NexovueApi:
    require_local(args.api)
    token = os.environ.get(TOKEN_VARIABLE)
    if not token:
        raise SystemExit(f"set {TOKEN_VARIABLE} to a read API token for the test-bed project")
    fetch = http_fetch(args.api, token)
    return NexovueApi(fetch, resolve_project(fetch, args.project_id))


def check(args: argparse.Namespace) -> int:
    """Everything a week-long run needs, checked in seconds before it starts."""
    require_local(args.api)
    if not args.flag_file.exists():
        print(f"No flag file at {args.flag_file}: start the demo once with `just otel-demo up`.")
        return 1
    flags = FlagFile(args.flag_file)
    names = flags.names()
    missing = sorted({fault.flag for fault in FAULTS} - names)
    if missing:
        print(
            f"Missing from {args.flag_file}: {', '.join(missing)}. Is the demo at {DEMO_VERSION}?"
        )
        return 1
    on = [f.flag for f in FAULTS if f.scale_of_default is None and flags.resting(f.flag) != "off"]
    if on:
        print(f"These flags are on; turn them off first: {', '.join(on)}")
        return 1
    for fault in FAULTS:
        if fault.scale_of_default is None and fault.variant not in flags.variants(fault.flag):
            print(f"Flag {fault.flag} has no variant {fault.variant!r}")
            return 1
    api = _connect(args)
    now = datetime.now(UTC)
    services = api.services(now - timedelta(hours=1), now)
    print(f"Flag file {args.flag_file}: all {len(FAULTS)} faults' flags present and off.")
    print(f"Project {api.project_id}: {len(services)} services reported in the last hour.")
    print(f"Findings readable: {len(api.findings())} listed now.")
    return 0


def run(args: argparse.Namespace) -> int:
    api = _connect(args)
    runner = Runner(args.log, FlagFile(args.flag_file), api, RealClock())
    if not args.log.exists():
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        faults = _faults(args.faults)
        resting = resting_variants(FlagFile(args.flag_file), faults)
        injections = plan_injections(
            faults,
            args.rounds,
            now + REFERENCE,
            timedelta(minutes=args.slot_minutes),
            timedelta(minutes=args.inject_minutes),
            args.seed,
        )
        runner.start(Plan(api.project_id, DEMO_VERSION, now, resting, injections))
        print(
            f"Reference run from {now:%Y-%m-%d %H:%M} UTC; {len(injections)} injections from "
            f"{injections[0].start:%Y-%m-%d %H:%M} to {injections[-1].end:%Y-%m-%d %H:%M} UTC."
        )
    else:
        print(f"Resuming {args.log}.")
    runner.run()
    return 0


def score_command(args: argparse.Namespace) -> int:
    table = render(score(load(events(args.log))))
    if args.out:
        args.out.write_text(table, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(table, end="")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)

    def connected(command: argparse.ArgumentParser) -> None:
        command.add_argument("--api", default=DEFAULT_API, help=f"default {DEFAULT_API}")
        command.add_argument("--project-id", help="default: the token's only project")
        command.add_argument("--flag-file", type=Path, default=DEFAULT_FLAG_FILE)

    check_parser = commands.add_parser("check", help="check the flags, the token and the project")
    connected(check_parser)
    check_parser.set_defaults(handler=check)

    run_parser = commands.add_parser("run", help="start a run, or resume the one in --log")
    connected(run_parser)
    run_parser.add_argument("--log", type=Path, required=True, help="the run log (JSON Lines)")
    run_parser.add_argument("--faults", help="comma-separated (default: every fault)")
    run_parser.add_argument("--rounds", type=int, default=ROUNDS, help=f"default {ROUNDS}")
    run_parser.add_argument("--slot-minutes", type=int, default=DEFAULT_SLOT_MINUTES)
    run_parser.add_argument("--inject-minutes", type=int, default=DEFAULT_ACTIVE_MINUTES)
    run_parser.add_argument("--seed", type=int, default=8, help="the injections' order")
    run_parser.set_defaults(handler=run)

    score_parser = commands.add_parser("score", help="write AC36's table from a run log")
    score_parser.add_argument("--log", type=Path, required=True)
    score_parser.add_argument("--out", type=Path, help="default: print it")
    score_parser.set_defaults(handler=score_command)

    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
