"""The fault bed's driving side against fakes: the flag file, the API reads, the schedule, and a
whole run with a clock that jumps instead of waiting a fortnight."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fault_bed import (
    FlagFile,
    NexovueApi,
    Runner,
    main,
    plan_injections,
    require_local,
    resolve_project,
    resting_variants,
)
from fault_model import REFERENCE, FindingSeen, Plan, ServiceWindow, events, load
from fault_score import render, score
from test_fault_score import uuid7

START = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
# A slice of the demo 3.0.0's own demo.flagd.json, including its one targeted flag.
FLAGS: dict[str, Any] = {
    "$schema": "https://flagd.dev/schema/v0/flags.json",
    "flags": {
        "paymentFailure": {
            "defaultVariant": "off",
            "state": "ENABLED",
            "variants": {"10%": 0.1, "100%": 1, "off": 0},
        },
        "productCatalogFailure": {
            "defaultVariant": "off",
            "state": "ENABLED",
            "targeting": {"if": [{"==": [{"var": "product_id"}, "OLJCESPC7Z"]}, "off", "off"]},
            "variants": {"off": False, "on": True},
        },
        "loadGeneratorVUs": {
            "defaultVariant": "5",
            "state": "ENABLED",
            "variants": {"10": 10, "5": 5, "50": 50},
        },
    },
}


@pytest.fixture
def flag_file(tmp_path: Path) -> FlagFile:
    path = tmp_path / "demo.flagd.json"
    path.write_text(json.dumps(FLAGS))
    return FlagFile(path)


def _document(flags: FlagFile) -> dict[str, Any]:
    flags_by_name: dict[str, Any] = json.loads(flags.path.read_text())["flags"]
    return flags_by_name


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.at = now

    def now(self) -> datetime:
        return self.at

    def sleep_until(self, at: datetime) -> None:
        self.at = max(self.at, at)


class FakeApi:
    """Steady services, except payment failing while its flag is on; one finding per injection."""

    def __init__(self, clock: FakeClock, flags: FlagFile) -> None:
        self.clock, self.flags = clock, flags
        self.windows: list[tuple[datetime, datetime]] = []
        self.opened: list[FindingSeen] = []
        # What the flag file held at each read, to prove what the services ran under.
        self.flags_at_read: list[dict[str, Any]] = []

    def services(self, start: datetime, end: datetime) -> list[ServiceWindow]:
        self.windows.append((start, end))
        self.flags_at_read.append(_document(self.flags))
        failing = self._payment_failing_at(start)
        return [
            ServiceWindow("payment", 100, 15 if failing else 0, 40.0),
            ServiceWindow("frontend", 900, 1, 120.0),
        ]

    def _payment_failing_at(self, at: datetime) -> bool:
        return at >= START + REFERENCE

    def findings(self) -> list[FindingSeen]:
        return [f for f in self.opened if f.opened_at <= self.clock.now()]


def test_setting_a_flag_changes_its_default_and_leaves_the_rest(flag_file: FlagFile) -> None:
    flag_file.set("paymentFailure", "10%")
    flags = _document(flag_file)
    assert flags["paymentFailure"]["defaultVariant"] == "10%"
    assert flags["loadGeneratorVUs"] == FLAGS["flags"]["loadGeneratorVUs"]
    assert flag_file.resting("paymentFailure") == "10%"
    assert not list(flag_file.path.parent.glob("*.tmp"))


def test_a_targeted_flag_changes_its_rules_on_branch_as_flagd_ui_does(flag_file: FlagFile) -> None:
    flag_file.set("productCatalogFailure", "on")
    flag = _document(flag_file)["productCatalogFailure"]
    assert flag["targeting"]["if"][1:] == ["on", "off"]
    assert flag["defaultVariant"] == "off"
    assert flag_file.resting("productCatalogFailure") == "on"


def test_an_unknown_flag_or_variant_is_refused(flag_file: FlagFile) -> None:
    with pytest.raises(SystemExit, match="no flag 'cartFailur'"):
        flag_file.set("cartFailur", "on")
    with pytest.raises(SystemExit, match="no variant '33%'"):
        flag_file.set("paymentFailure", "33%")


def test_a_fault_flag_already_on_is_refused_before_the_run(flag_file: FlagFile) -> None:
    assert resting_variants(flag_file, ["paymentFailure", "trafficAt40"]) == {
        "paymentFailure": "off",
        "loadGeneratorVUs": "5",
    }
    flag_file.set("paymentFailure", "10%")
    with pytest.raises(SystemExit, match="turn it off"):
        resting_variants(flag_file, ["paymentFailure"])


def test_each_round_holds_every_fault_once_in_its_own_order() -> None:
    faults = ["a", "b", "c", "d"]
    planned = plan_injections(faults, 5, START, timedelta(minutes=90), timedelta(minutes=30), 8)
    rounds = [[i.fault for i in planned if i.round == r] for r in range(1, 6)]
    assert all(sorted(r) == faults for r in rounds)
    assert len({tuple(r) for r in rounds}) > 1
    assert planned[1].start - planned[0].start == timedelta(minutes=90)
    assert planned[0].end - planned[0].start == timedelta(minutes=30)
    # The first full window starts after the flag has had a minute to reach the services.
    assert planned[0].window == (START + timedelta(minutes=1), START + timedelta(minutes=6))


def test_a_schedule_that_cannot_be_scored_is_refused() -> None:
    with pytest.raises(SystemExit, match="longer than the longest time to catch"):
        plan_injections(["a"], 5, START, timedelta(minutes=90), timedelta(minutes=15), 8)
    with pytest.raises(SystemExit, match="fit in one week"):
        plan_injections(["a"] * 30, 5, START, timedelta(minutes=90), timedelta(minutes=30), 8)


def test_a_whole_run_records_what_scoring_needs(flag_file: FlagFile, tmp_path: Path) -> None:
    clock = FakeClock(START)
    api = FakeApi(clock, flag_file)
    log = tmp_path / "run.jsonl"
    faults = ["paymentFailure", "trafficAt40"]
    injections = plan_injections(
        faults, 1, START + REFERENCE, timedelta(minutes=90), timedelta(minutes=30), 1
    )
    payment = next(i for i in injections if i.fault == "paymentFailure")
    api.opened = [
        FindingSeen(
            uuid7(START + timedelta(days=3)),
            START + timedelta(days=3),
            "service",
            ("frontend",),
            ("slower",),
            "warning",
            None,
        ),
        FindingSeen(
            uuid7(payment.start + timedelta(minutes=7)),
            payment.start + timedelta(minutes=7),
            "service",
            ("payment",),
            ("failing",),
            "warning",
            None,
        ),
    ]
    runner = Runner(log, flag_file, api, clock, say=lambda _: None)
    runner.start(Plan("p", "3.0.0", START, resting_variants(flag_file, faults), injections))
    runner.run()

    recorded = list(events(log))
    flags_set = [(e["flag"], e["variant"]) for e in recorded if e["kind"] == "flag"]
    traffic = ("loadGeneratorVUs", "faultbed-40pct")
    assert sorted(flags_set) == sorted(
        [("paymentFailure", "10%"), ("paymentFailure", "off"), traffic, ("loadGeneratorVUs", "5")]
    )
    # The traffic fault runs the load generator at 40% of its 5 users, then puts it back.
    assert _document(flag_file)["loadGeneratorVUs"]["variants"]["faultbed-40pct"] == 2
    assert flag_file.resting("loadGeneratorVUs") == "5"
    assert flag_file.resting("paymentFailure") == "off"
    # Each injection reads its first full window, then the same window a week earlier.
    first, last = payment.window
    assert (first, last) in api.windows
    assert (first - REFERENCE, last - REFERENCE) in api.windows
    assert (START, START + REFERENCE) in api.windows

    card = score(load(events(log)))
    assert [f.services for f in card.false_alarms] == [("frontend",)]
    payment_score = next(f for f in card.faults if f.fault == "paymentFailure")
    assert payment_score.injections[0].time_to_catch == timedelta(minutes=7)
    assert "## AC36: injected faults" in render(card)


def test_a_resumed_run_turns_a_stranded_flag_off_and_skips_what_it_missed(
    flag_file: FlagFile, tmp_path: Path
) -> None:
    clock = FakeClock(START)
    api = FakeApi(clock, flag_file)
    log = tmp_path / "run.jsonl"
    faults = ["paymentFailure", "productCatalogFailure"]
    injections = plan_injections(
        faults, 1, START + REFERENCE, timedelta(minutes=90), timedelta(minutes=30), 1
    )
    runner = Runner(log, flag_file, api, clock, say=lambda _: None)
    runner.start(Plan("p", "3.0.0", START, resting_variants(flag_file, faults), injections))

    # The process died with the first fault on, and came back 10 minutes into it.
    flag_file.set(injections[0].fault, "10%" if injections[0].fault == "paymentFailure" else "on")
    clock.at = injections[0].start + timedelta(minutes=10)
    runner.run()

    # Turned off before anything was read, not only when the run ends.
    stranded = api.flags_at_read[0][injections[0].fault]
    assert stranded.get("targeting", {}).get("if", [None, "off"])[1] == "off"
    assert stranded["defaultVariant"] == "off"
    run = load(events(log))
    assert [(s.fault, s.reason) for s in run.skipped] == [
        (injections[0].fault, "its start passed while the bed was not running")
    ]
    assert [o.fault for o in run.observations] == [injections[1].fault]
    assert flag_file.resting("paymentFailure") == "off"
    assert flag_file.resting("productCatalogFailure") == "off"
    with pytest.raises(SystemExit, match="exists"):
        runner.start(run.plan)


def test_the_api_reads_services_and_names_a_project_findings_services() -> None:
    project_finding = uuid7(START)
    answers: dict[str, Any] = {
        "/services": {
            "services": [
                {"service_name": "cart", "request_count": 50, "error_count": 2, "p95_ms": 12.5}
            ]
        },
        "/findings?project_id=p&state=all&muted=false": {
            "findings": [
                {
                    "id": project_finding,
                    "scope": "project",
                    "service_name": None,
                    "signals": [{"signal": "traffic"}],
                    "severity": "critical",
                    "closed_reason": None,
                }
            ]
        },
        "/findings?project_id=p&state=all&muted=true": {"findings": []},
        f"/findings/{project_finding}": {"services": ["cart", "checkout"]},
    }
    asked: list[str] = []

    def fetch(path: str) -> Any:
        asked.append(path)
        return answers[path.split("?")[0] if path.startswith("/services") else path]

    api = NexovueApi(fetch, "p")
    assert api.services(START, START + timedelta(minutes=5)) == [ServiceWindow("cart", 50, 2, 12.5)]
    assert "from=2026-11-02T09%3A00%3A00%2B00%3A00" in asked[0]
    [seen] = api.findings()
    assert seen.services == ("cart", "checkout")
    assert seen.opened_at == START
    assert seen.signals == ("traffic",)


def test_the_project_is_the_tokens_only_one_unless_named() -> None:
    def fetch(_: str) -> Any:
        return {"projects": [{"id": "a"}, {"id": "b"}]}

    assert resolve_project(fetch, "b") == "b"
    with pytest.raises(SystemExit, match="reads 2 projects"):
        resolve_project(fetch, None)
    with pytest.raises(SystemExit, match="cannot read project c"):
        resolve_project(fetch, "c")


@pytest.mark.parametrize("url", ["http://api.nexovue.com", "http://10.0.0.5:18000"])
def test_any_api_host_but_localhost_is_refused(url: str) -> None:
    with pytest.raises(SystemExit, match="refusing API host"):
        require_local(url)
    with pytest.raises(SystemExit, match="refusing API host"):
        main(["check", "--api", url])


def test_the_score_command_writes_the_table(tmp_path: Path, flag_file: FlagFile) -> None:
    clock = FakeClock(START)
    log = tmp_path / "run.jsonl"
    injections = plan_injections(
        ["paymentFailure"], 1, START + REFERENCE, timedelta(minutes=90), timedelta(minutes=30), 1
    )
    runner = Runner(log, flag_file, FakeApi(clock, flag_file), clock, say=lambda _: None)
    runner.start(Plan("p", "3.0.0", START, {"paymentFailure": "off"}, injections))
    runner.run()
    out = tmp_path / "ac36.md"
    assert main(["score", "--log", str(log), "--out", str(out)]) == 0
    assert "| `paymentFailure` | `paymentFailure` = `10%` |" in out.read_text()
