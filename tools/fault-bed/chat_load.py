"""F4 plan T8.1 (AC80, AC98): chat under load, beside detection and alerts.

Three commands:

- `run` keeps one hostile question in flight for each `--email` through the real API and
  `scheduler-insight`, for `--minutes`: a person may have one question in flight, so AC80's four
  are four people of one organization, sharing the password in `NEXOVUE_LOAD_PASSWORD`. Each
  looper asks in one conversation of its own, so the
  conversation grows long, and reads its question back as the panel does: the message route every
  1.5 seconds while it runs, then the conversation route once it ends. The organization's AI
  connection must point at the fake provider in `week-reads` mode
  (`tools/ai-eval/fake_provider/server.py`): every step a 7-day `search_logs` or `list_traces`
  read, on whatever the install holds. Every question, every poll's time and size, and the
  memory of each `--process` or `--container` named, go to `--log` (JSON Lines).
- `summary` prints what one `run` log holds: questions by how they ended, steps, the two poll
  routes by conversation length, and peak memory (AC98's record).
- `compare` is AC80's alert half: two fault-bed run logs (`fault_bed.py run`), one with chat
  idle and one with `run` looping beside it, paired by fault and round. Each injection's first
  finding and first burn must be no more than 60 seconds later under chat, and match the idle
  run on `FINDING_COLUMNS` and `NOTIFICATION_COLUMNS`.

AC80's detection half (every minute judged within the 60-second cycle) is not measured here:
`just detection-scale watch` measures it for AC29's generated tenants only, not the fault bed's
project (F4 status, open decision). Dev stack only: the API must be on localhost. The password
is read from `NEXOVUE_LOAD_PASSWORD` and never printed or logged.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from fault_bed import require_local
from fault_model import BurnSeen, FindingSeen, RecordedRun, append, events, load

REPOSITORY: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tools" / "detection-measure"))

import scale  # noqa: E402  detection-scale's process and container samplers.

DEFAULT_API: Final = "http://localhost:18000"
PASSWORD_VARIABLE: Final = "NEXOVUE_LOAD_PASSWORD"
# AC80: four hostile questions at once (four people), an alert no more than 60 seconds later
# (a guess, fixed before any run), and the panel's read of a running question (`POLL_MS`).
QUESTIONS: Final = 4
LATE_LIMIT: Final = timedelta(seconds=60)
POLL_SECONDS: Final = 1.5
MEMORY_SECONDS: Final = 5.0
REQUEST_TIMEOUT_SECONDS: Final = 30.0
IN_FLIGHT: Final = frozenset({"queued", "running"})
QUESTION: Final = "Read this whole week's logs and traces and tell me what went wrong."
# What AC80 compares, finding by finding and notification by notification.
FINDING_COLUMNS: Final = ("scope", "services", "signals", "severity")
NOTIFICATION_COLUMNS: Final = ("kind", "state", "channel_kind", "reason")


# ---------- the log ----------


def _at(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def question_event(
    at: datetime, message_id: str, state: str, *, steps: int, failed_steps: int, seconds: float
) -> dict[str, Any]:
    return {
        "event": "question",
        "at": _at(at),
        "message_id": message_id,
        "state": state,
        "steps": steps,
        "failed_steps": failed_steps,
        "seconds": seconds,
    }


def poll_event(
    at: datetime, route: str, *, milliseconds: float, size: int, messages: int
) -> dict[str, Any]:
    """One read of a poll route: `message` (one question) or `conversation` (its first page)."""
    return {
        "event": "poll",
        "at": _at(at),
        "route": route,
        "milliseconds": milliseconds,
        "bytes": size,
        "messages": messages,
    }


def memory_event(at: datetime, label: str, *, rss: int, pss: int, processes: int) -> dict[str, Any]:
    """One reading of a process group (PSS shares pages among processes) or, for a container,
    its cgroup's use in both fields and `processes` 0."""
    return {
        "event": "memory",
        "at": _at(at),
        "label": label,
        "rss_bytes": rss,
        "pss_bytes": pss,
        "processes": processes,
    }


def read_log(path: Path) -> list[dict[str, Any]]:
    """This tool's own log. Not `fault_model.events`, which reads a fault-bed run's."""
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------- summary ----------


@dataclass(frozen=True, slots=True)
class RouteTimes:
    count: int
    median_ms: float
    p95_ms: float
    largest_ms: float
    largest_bytes: int


@dataclass(frozen=True, slots=True)
class Summary:
    questions: dict[str, int]
    steps: int
    failed_steps: int
    polls: dict[str, RouteTimes]
    longest_conversation: int
    peak_rss_bytes: dict[str, int]
    peak_pss_bytes: dict[str, int]


def _p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(0.95 * len(ordered)) - 1))]


def summarise(log: Iterable[Mapping[str, Any]]) -> Summary:
    questions: dict[str, int] = {}
    steps = failed = longest = 0
    times: dict[str, list[float]] = {}
    sizes: dict[str, int] = {}
    rss: dict[str, int] = {}
    pss: dict[str, int] = {}
    for event in log:
        kind = event.get("event")
        if kind == "question":
            questions[event["state"]] = questions.get(event["state"], 0) + 1
            steps += event["steps"]
            failed += event["failed_steps"]
        elif kind == "poll":
            times.setdefault(event["route"], []).append(event["milliseconds"])
            sizes[event["route"]] = max(sizes.get(event["route"], 0), event["bytes"])
            longest = max(longest, event["messages"])
        elif kind == "memory":
            rss[event["label"]] = max(rss.get(event["label"], 0), event["rss_bytes"])
            pss[event["label"]] = max(pss.get(event["label"], 0), event["pss_bytes"])
    polls = {
        route: RouteTimes(
            len(values), statistics.median(values), _p95(values), max(values), sizes[route]
        )
        for route, values in times.items()
    }
    return Summary(questions, steps, failed, polls, longest, rss, pss)


def _mib(size: int) -> str:
    return f"{size / 2**20:,.0f} MiB"


def render_summary(summary: Summary) -> str:
    asked = sum(summary.questions.values())
    ended = ", ".join(f"{state} {count}" for state, count in sorted(summary.questions.items()))
    lines = [
        "# Chat under load",
        "",
        f"- Questions ended: {asked} ({ended or 'none'}); steps {summary.steps}, "
        f"of which failed {summary.failed_steps}.",
        f"- Longest conversation read: {summary.longest_conversation} questions.",
        "",
        "| Route | Reads | Median | 95th percentile | Slowest | Largest answer |",
        "|---|---|---|---|---|---|",
    ]
    for route, times in sorted(summary.polls.items()):
        lines.append(
            f"| {route} | {times.count} | {times.median_ms:,.0f} ms | {times.p95_ms:,.0f} ms | "
            f"{times.largest_ms:,.0f} ms | {times.largest_bytes / 1000:,.0f} kB |"
        )
    lines += ["", "| Memory | Peak PSS | Peak RSS |", "|---|---|---|"]
    for label in sorted(summary.peak_pss_bytes):
        lines.append(
            f"| {label} | {_mib(summary.peak_pss_bytes[label])} | "
            f"{_mib(summary.peak_rss_bytes[label])} |"
        )
    return "\n".join(lines) + "\n"


# ---------- compare (AC80's alert half) ----------


@dataclass(frozen=True, slots=True)
class InjectionComparison:
    fault: str
    round: int
    # Chat's first finding (or burn) less the idle run's, both from their injection's start;
    # None when either run has none.
    finding_lateness: timedelta | None
    findings_match: bool
    burn_lateness: timedelta | None
    notifications_match: bool
    had_burn: bool

    @property
    def holds(self) -> bool:
        if self.finding_lateness is None or self.finding_lateness > LATE_LIMIT:
            return False
        if self.had_burn and (self.burn_lateness is None or self.burn_lateness > LATE_LIMIT):
            return False
        return self.findings_match and self.notifications_match


def _finding_row(finding: FindingSeen) -> tuple[Any, ...]:
    return tuple(getattr(finding, column) for column in FINDING_COLUMNS)


def _notification_rows(burn: BurnSeen) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        sorted(
            tuple(getattr(message, column) for column in NOTIFICATION_COLUMNS)
            for message in burn.messages
        )
    )


def _injections(run: RecordedRun) -> dict[tuple[str, int], tuple[datetime, datetime]]:
    return {(i.fault, i.round): (i.start, i.end) for i in run.plan.injections}


def _first_opened[Seen: (FindingSeen, BurnSeen)](
    seen: Iterable[Seen], start: datetime, end: datetime
) -> Seen | None:
    inside = sorted((s for s in seen if start <= s.opened_at < end), key=lambda s: s.opened_at)
    return inside[0] if inside else None


def _lateness(
    idle: FindingSeen | BurnSeen | None,
    idle_start: datetime,
    chat: FindingSeen | BurnSeen | None,
    chat_start: datetime,
) -> timedelta | None:
    if idle is None or chat is None:
        return None
    return (chat.opened_at - chat_start) - (idle.opened_at - idle_start)


def compare(idle: RecordedRun, chat: RecordedRun) -> list[InjectionComparison]:
    """Each injection both runs hold, chat's against idle's, from each injection's own start. The
    first finding (or burn) opened while the fault was on is the alert; the column match says
    whether it was the same one. An injection in a single run is not judged."""
    idle_plan, chat_plan = _injections(idle), _injections(chat)
    found: list[InjectionComparison] = []
    for key in sorted(idle_plan.keys() & chat_plan.keys()):
        (idle_from, idle_to), (chat_from, chat_to) = idle_plan[key], chat_plan[key]
        idle_findings = sorted(
            _finding_row(f) for f in idle.findings if idle_from <= f.opened_at < idle_to
        )
        chat_findings = sorted(
            _finding_row(f) for f in chat.findings if chat_from <= f.opened_at < chat_to
        )
        idle_burn = _first_opened(idle.burns, idle_from, idle_to)
        chat_burn = _first_opened(chat.burns, chat_from, chat_to)
        notifications_match = (
            idle_burn is None
            if chat_burn is None
            else idle_burn is not None
            and _notification_rows(idle_burn) == _notification_rows(chat_burn)
        )
        found.append(
            InjectionComparison(
                fault=key[0],
                round=key[1],
                finding_lateness=_lateness(
                    _first_opened(idle.findings, idle_from, idle_to),
                    idle_from,
                    _first_opened(chat.findings, chat_from, chat_to),
                    chat_from,
                ),
                findings_match=idle_findings == chat_findings,
                burn_lateness=_lateness(idle_burn, idle_from, chat_burn, chat_from),
                notifications_match=notifications_match,
                had_burn=idle_burn is not None,
            )
        )
    return found


def _seconds(lateness: timedelta | None) -> str:
    return "none" if lateness is None else f"{lateness.total_seconds():+,.0f} s"


def render_comparison(rows: Sequence[InjectionComparison]) -> str:
    if not rows:
        return "No injection is in both runs, so AC80's alert half is not judged.\n"
    lines = [
        "| Fault | Round | Finding, chat less idle | Findings match | Burn, chat less idle "
        "| Notifications match | Holds |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.fault} | {row.round} | {_seconds(row.finding_lateness)} | "
            f"{'yes' if row.findings_match else 'no'} | "
            f"{_seconds(row.burn_lateness) if row.had_burn else 'no burn'} | "
            f"{'yes' if row.notifications_match else 'no'} | {'yes' if row.holds else 'no'} |"
        )
    failed = sum(1 for row in rows if not row.holds)
    verdict = "holds" if not failed else f"fails ({failed} of {len(rows)} injections)"
    lines += [
        "",
        f"AC80's alert half: {verdict}. A finding or burn may be at most "
        f"{LATE_LIMIT.total_seconds():.0f} seconds later under chat, and must match on "
        f"{', '.join(FINDING_COLUMNS)} (findings) and {', '.join(NOTIFICATION_COLUMNS)} "
        "(notifications).",
    ]
    return "\n".join(lines) + "\n"


# ---------- run ----------


class Session:
    """A signed-in person's cookie session against the dev API."""

    def __init__(self, api: str) -> None:
        self._api = api.rstrip("/")
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method: str, path: str, body: Any = None) -> tuple[Any, int, float]:
        """The decoded answer, its size in bytes and how long it took in milliseconds."""
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"{self._api}/api/v1{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        started = time.perf_counter()
        try:
            with self._opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"{method} {path.split('?')[0]} answered {error.code}") from None
        took = (time.perf_counter() - started) * 1000
        return (json.loads(raw) if raw else None), len(raw), took


@dataclass(slots=True)
class Shared:
    log: Path
    until: float
    lock: threading.Lock = field(default_factory=threading.Lock)
    errors: list[str] = field(default_factory=list)

    def write(self, event: dict[str, Any]) -> None:
        with self.lock:
            append(self.log, event)


def _loop(session: Session, project_id: str, shared: Shared) -> None:
    """One looper: ask, read it back as the panel does, ask again in the same conversation."""
    conversation: str | None = None
    asked = 0
    while time.monotonic() < shared.until:
        started = time.monotonic()
        body = {"question": QUESTION, "context": {"time_zone": "UTC"}}
        try:
            if conversation is None:
                message, _, _ = session.call(
                    "POST", "/assistant/conversations", {**body, "project_id": project_id}
                )
                conversation = message["conversation_id"]
            else:
                message, _, _ = session.call(
                    "POST",
                    f"/assistant/conversations/{conversation}/messages?project_id={project_id}",
                    body,
                )
            asked += 1
            path = f"/assistant/conversations/{conversation}/messages/{message['id']}"
            while message["state"] in IN_FLIGHT:
                time.sleep(POLL_SECONDS)
                message, size, took = session.call("GET", path)
                shared.write(
                    poll_event(
                        datetime.now(UTC), "message", milliseconds=took, size=size, messages=asked
                    )
                )
            page, size, took = session.call("GET", f"/assistant/conversations/{conversation}")
            shared.write(
                poll_event(
                    datetime.now(UTC),
                    "conversation",
                    milliseconds=took,
                    size=size,
                    messages=len(page["messages"]),
                )
            )
        except RuntimeError as error:
            # A refusal (the day's limit, a rate limit) is recorded and the looper stops: a load
            # that is refused is not load, and the summary must not count it as such.
            shared.errors.append(str(error))
            return
        shared.write(
            question_event(
                datetime.now(UTC),
                message["id"],
                message["state"],
                steps=len(message["steps"]),
                failed_steps=sum(1 for step in message["steps"] if step["state"] != "read"),
                seconds=time.monotonic() - started,
            )
        )


def _watch_memory(
    groups: Sequence[scale.ProcessGroup], containers: Sequence[scale.ContainerPeak], shared: Shared
) -> None:
    while time.monotonic() < shared.until:
        now = datetime.now(UTC)
        if groups:
            scale.sample(groups)
            for group in groups:
                shared.write(
                    memory_event(
                        now,
                        group.label,
                        rss=group.peak_rss_bytes,
                        pss=group.peak_pss_bytes,
                        processes=group.peak_processes,
                    )
                )
        if containers:
            scale.sample_containers(containers)
            for container in containers:
                used = container.peak_bytes
                shared.write(memory_event(now, container.name, rss=used, pss=used, processes=0))
        time.sleep(MEMORY_SECONDS)


def run(args: argparse.Namespace) -> int:
    require_local(args.api)
    password = os.environ.get(PASSWORD_VARIABLE)
    if not password:
        print(f"{PASSWORD_VARIABLE} is not set: the load person's password.", file=sys.stderr)
        return 2
    groups = [
        scale.ProcessGroup(label, re.compile(pattern))
        for label, _, pattern in (spec.partition("=") for spec in args.process)
    ]
    containers = [scale.ContainerPeak(name) for name in args.container]
    if len(args.email) != QUESTIONS:
        print(f"{len(args.email)} people, so {len(args.email)} questions; AC80 names {QUESTIONS}.")
    sessions = []
    for email in args.email:
        session = Session(args.api)
        try:
            session.call("POST", "/auth/login", {"email": email, "password": password})
        except RuntimeError as error:
            print(f"{email} could not sign in: {error}", file=sys.stderr)
            return 2
        sessions.append(session)
    shared = Shared(args.log, time.monotonic() + args.minutes * 60)
    threads = [
        threading.Thread(target=_loop, args=(session, args.project_id, shared))
        for session in sessions
    ]
    threads.append(threading.Thread(target=_watch_memory, args=(groups, containers, shared)))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for refusal in shared.errors:
        print(f"a looper stopped: {refusal}", file=sys.stderr)
    print(render_summary(summarise(read_log(args.log))))
    return 1 if shared.errors else 0


def summary_command(args: argparse.Namespace) -> int:
    print(render_summary(summarise(read_log(args.log))))
    return 0


def compare_command(args: argparse.Namespace) -> int:
    rows = compare(load(events(args.idle)), load(events(args.chat)))
    text = render_comparison(rows)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0 if rows and all(row.holds for row in rows) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="keep hostile questions looping, and record")
    run_parser.add_argument("--api", default=DEFAULT_API, help=f"default {DEFAULT_API}")
    run_parser.add_argument("--project-id", required=True)
    run_parser.add_argument(
        "--email",
        action="append",
        required=True,
        help=f"one person per question, each once; their password in {PASSWORD_VARIABLE}",
    )
    run_parser.add_argument("--minutes", type=float, required=True)
    run_parser.add_argument(
        "--process",
        action="append",
        default=[],
        help="LABEL=REGEX: a process (and its children) whose memory to sample, from /proc",
    )
    run_parser.add_argument(
        "--container", action="append", default=[], help="a container whose memory to sample"
    )
    run_parser.add_argument("--log", type=Path, required=True, help="JSON Lines, appended")
    run_parser.set_defaults(handler=run)
    summary_parser = commands.add_parser("summary", help="what a run log holds (AC98)")
    summary_parser.add_argument("--log", type=Path, required=True)
    summary_parser.set_defaults(handler=summary_command)
    compare_parser = commands.add_parser("compare", help="AC80's alert half: two fault-bed runs")
    compare_parser.add_argument("--idle", type=Path, required=True, help="a run with chat idle")
    compare_parser.add_argument("--chat", type=Path, required=True, help="a run under `run`")
    compare_parser.add_argument("--out", type=Path)
    compare_parser.set_defaults(handler=compare_command)
    args = parser.parse_args(argv)
    handler: Any = args.handler
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
