"""Score a recorded fault-bed run into AC36's table (F6, T8.1). Pure: it reads a `RecordedRun` and
nothing else, so a run recorded on the founder's Mac can be scored, and re-scored, anywhere.

The rules, from the spec (AC36, D16, D19, FR27):
- A flag *moves* a service's signal when an injection's first full window passes the normal-
  sensitivity minimum against the same window of the reference run, a week earlier, in a majority
  of its injections. A flag that moves nothing is a gap.
- An injection *counts* when it clears that minimum by a tenth on a signal the flag moves.
- It is *caught* when a finding opens on a moved service while the fault is on; *in time* when that
  took no longer than the service's time to catch.
- A flag passes when at least 4 of every 5 counted injections were caught in time, over 5 or more.
- Every finding that opened during the reference run is a false alarm.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from fault_model import (
    CLEAR_MARGIN,
    FAILING_MIN_FAILURES,
    FAILING_MIN_POINTS,
    FAILING_MIN_REQUESTS,
    REFERENCE,
    REQUIRED_SHARE,
    ROUNDS,
    SIGNALS,
    SLOWER_MIN_RATIO,
    SLOWER_MIN_REQUESTS,
    TRAFFIC_MAX_SHARE,
    TRAFFIC_MIN_EXPECTED,
    WINDOW,
    FindingSeen,
    Injection,
    Observation,
    RecordedRun,
    ServiceWindow,
    Signal,
    catch_limit,
    fault_named,
)

Move = tuple[str, Signal]
NOT_PAST: Final = 0
PAST: Final = 1
CLEARLY_PAST: Final = 2


@dataclass(frozen=True, slots=True)
class InjectionScore:
    fault: str
    round: int
    start: datetime
    # Each (service, signal) and how far past the minimum the first full window went.
    passed: dict[Move, int]
    counted: bool
    caught_by: FindingSeen | None
    time_to_catch: timedelta | None
    in_time: bool


@dataclass(frozen=True, slots=True)
class FaultScore:
    fault: str
    flag: str
    variant: str
    moves: tuple[Move, ...]
    injections: tuple[InjectionScore, ...]
    skipped: int
    expected: Move

    @property
    def gap(self) -> bool:
        return not self.moves

    @property
    def counted(self) -> int:
        return sum(1 for i in self.injections if i.counted)

    @property
    def in_time(self) -> int:
        return sum(1 for i in self.injections if i.counted and i.in_time)

    @property
    def verdict(self) -> str:
        if self.gap:
            return "gap (FR27)"
        if self.counted < ROUNDS:
            return f"not proved: {self.counted} clearly past the minimum, {ROUNDS} needed"
        needed = math.ceil(REQUIRED_SHARE * self.counted)
        return "pass" if self.in_time >= needed else f"fail: {needed} needed in time"


@dataclass(frozen=True, slots=True)
class Scorecard:
    faults: tuple[FaultScore, ...]
    false_alarms: tuple[FindingSeen, ...]
    reference_services: int | None
    reference_weeks: float
    # Findings in the injection week that caught nothing: on an unmoved service, or between faults.
    unattributed: tuple[FindingSeen, ...]

    @property
    def false_alarm_rate(self) -> float | None:
        if not self.reference_services or not self.reference_weeks:
            return None
        return len(self.false_alarms) / (self.reference_services * self.reference_weeks)


def how_far_past(signal: Signal, during: ServiceWindow | None, before: ServiceWindow) -> int:
    """How far one service's window moved past the normal-sensitivity minimum on one signal."""
    if signal == "traffic":
        if before.requests < TRAFFIC_MIN_EXPECTED:
            return NOT_PAST
        share = (during.requests if during else 0) / before.requests
        return _grade(TRAFFIC_MAX_SHARE - share, 1 - TRAFFIC_MAX_SHARE)
    if during is None:
        return NOT_PAST
    if signal == "slower":
        if min(during.requests, before.requests) < SLOWER_MIN_REQUESTS or before.p95_ms <= 0:
            return NOT_PAST
        return _grade(during.p95_ms / before.p95_ms - SLOWER_MIN_RATIO, SLOWER_MIN_RATIO - 1)
    if during.requests < FAILING_MIN_REQUESTS or during.errors < FAILING_MIN_FAILURES:
        return NOT_PAST
    rise = during.errors / during.requests - _rate(before)
    return _grade(rise - FAILING_MIN_POINTS, FAILING_MIN_POINTS)


def _rate(window: ServiceWindow) -> float:
    return window.errors / window.requests if window.requests else 0.0


def _grade(beyond: float, minimum_change: float) -> int:
    """`beyond` is how far past the minimum change the reading went, in the signal's units."""
    if beyond >= CLEAR_MARGIN * minimum_change - 1e-12:
        return CLEARLY_PAST
    return PAST if beyond >= 0 else NOT_PAST


def window_moves(observation: Observation) -> dict[Move, int]:
    """Every (service, signal) that passed the minimum in an injection's first full window."""
    during = {w.service: w for w in observation.during}
    moves: dict[Move, int] = {}
    for before in observation.reference:
        for signal in SIGNALS:
            grade = how_far_past(signal, during.get(before.service), before)
            if grade:
                moves[(before.service, signal)] = grade
    return moves


def flag_moves(passes: Sequence[dict[Move, int]]) -> tuple[Move, ...]:
    """What a flag moves: a (service, signal) past the minimum in a majority of its injections,
    so one noisy window neither makes nor hides a move."""
    counts: dict[Move, int] = {}
    for passed in passes:
        for move in passed:
            counts[move] = counts.get(move, 0) + 1
    return tuple(sorted(move for move, n in counts.items() if 2 * n > len(passes)))


def _reference_rpm(observation: Observation, service: str) -> float:
    for window in observation.reference:
        if window.service == service:
            return window.requests / (WINDOW.total_seconds() / 60)
    return 0.0


def _first_catch(
    injection: Injection, moved: set[str], findings: Iterable[FindingSeen]
) -> FindingSeen | None:
    for finding in findings:  # Sorted by when each opened, so the first match is the earliest.
        if injection.start <= finding.opened_at < injection.end and moved & set(finding.services):
            return finding
    return None


def _score_fault(
    run: RecordedRun, name: str, findings: Sequence[FindingSeen]
) -> tuple[FaultScore, set[str]]:
    fault = fault_named(name)
    planned = {(i.fault, i.round): i for i in run.plan.injections}
    observations = sorted((o for o in run.observations if o.fault == name), key=lambda o: o.round)
    passes = [window_moves(o) for o in observations]
    moves = flag_moves(passes)
    moved_services = {service for service, _ in moves}
    scores: list[InjectionScore] = []
    catches: set[str] = set()
    for observation, passed in zip(observations, passes, strict=True):
        injection = planned[(name, observation.round)]
        counted = any(passed.get(move) == CLEARLY_PAST for move in moves)
        caught = _first_catch(injection, moved_services, findings) if moves else None
        taken = caught.opened_at - injection.start if caught else None
        in_time = False
        if caught is not None and taken is not None:
            catches.add(caught.id)
            limit = min(
                catch_limit(_reference_rpm(observation, service))
                for service in moved_services & set(caught.services)
            )
            in_time = taken <= limit
        scores.append(
            InjectionScore(
                name, observation.round, injection.start, passed, counted, caught, taken, in_time
            )
        )
    skipped = sum(1 for s in run.skipped if s.fault == name)
    score = FaultScore(
        fault=name,
        flag=fault.flag,
        variant=fault.variant,
        moves=moves,
        injections=tuple(scores),
        skipped=skipped,
        expected=(fault.expected_service, fault.expected_signal),
    )
    return score, catches


def score(run: RecordedRun) -> Scorecard:
    reference_end = run.plan.reference_start + REFERENCE
    false_alarms = tuple(
        f for f in run.findings if run.plan.reference_start <= f.opened_at < reference_end
    )
    planned_faults = list(dict.fromkeys(i.fault for i in run.plan.injections))
    faults: list[FaultScore] = []
    catches: set[str] = set()
    for name in planned_faults:
        fault_score, caught = _score_fault(run, name, run.findings)
        faults.append(fault_score)
        catches |= caught
    unattributed = tuple(
        f for f in run.findings if f.opened_at >= reference_end and f.id not in catches
    )
    services = None if run.reference_services is None else len(run.reference_services)
    return Scorecard(
        faults=tuple(faults),
        false_alarms=false_alarms,
        reference_services=services,
        reference_weeks=REFERENCE / timedelta(weeks=1),
        unattributed=unattributed,
    )


# ---------- the Markdown ----------


def _moves(moves: Iterable[Move]) -> str:
    return ", ".join(f"{service} · {signal}" for service, signal in moves) or "none"


def _minutes(taken: timedelta) -> str:
    return f"{taken.total_seconds() / 60:.1f} min"


def _caught_by(finding: FindingSeen) -> str:
    services = ", ".join(finding.services) or "project"
    return f"{services} · {finding.signals[0] if finding.signals else '?'}"


def _row(fault: FaultScore) -> str:
    taken = [i.time_to_catch for i in fault.injections if i.counted and i.time_to_catch]
    median = f"{statistics.median(t.total_seconds() for t in taken) / 60:.1f} min" if taken else "—"
    by = sorted({_caught_by(i.caught_by) for i in fault.injections if i.caught_by})
    injected = len(fault.injections)
    skipped = f" ({fault.skipped} skipped)" if fault.skipped else ""
    cells = [
        f"`{fault.fault}`",
        f"`{fault.flag}` = `{fault.variant}`",
        _moves(fault.moves),
        f"{fault.counted} of {injected}{skipped}",
        "—" if fault.gap else f"{fault.in_time} of {fault.counted}",
        "—" if fault.gap else median,
        "; ".join(by) or "—",
        fault.verdict,
    ]
    return "| " + " | ".join(cells) + " |"


def render(card: Scorecard) -> str:
    """AC36's table, the gap record (FR27), the false alarms and every injection's detail."""
    lines = [
        "## AC36: injected faults",
        "",
        "| Fault | Flag | Moves (service · signal) | Clearly past the minimum | Caught in time "
        "| Median time to catch | Caught by | Verdict |",
        "|---|---|---|---|---|---|---|---|",
        *(_row(fault) for fault in card.faults),
        "",
        "## Gaps (FR27)",
        "",
    ]
    gaps = [f for f in card.faults if f.gap]
    if gaps:
        lines += ["| Flag | Expected to move | Injections |", "|---|---|---|"]
        lines += [
            f"| `{f.flag}` = `{f.variant}` | {_moves([f.expected])} | {len(f.injections)} |"
            for f in gaps
        ]
    else:
        lines.append("None: every flag moved at least one signal past the minimum.")
    rate = card.false_alarm_rate
    lines += [
        "",
        "## False alarms in the reference run",
        "",
        f"{len(card.false_alarms)} findings over {card.reference_weeks:g} week(s) and "
        + (
            f"{card.reference_services} services: {rate:.2f} per service per week (budget 1)."
            if rate is not None
            else "an unknown number of services: the reference services were not recorded."
        ),
    ]
    if card.false_alarms:
        lines += ["", "| Opened | Service · signal | Severity | Closed as |", "|---|---|---|---|"]
        lines += [
            f"| {f.opened_at:%Y-%m-%d %H:%M} UTC | {_caught_by(f)} | {f.severity} "
            f"| {f.closed_reason or 'open'} |"
            for f in card.false_alarms
        ]
    lines += ["", "## Findings in the injection week that caught no fault", ""]
    if card.unattributed:
        lines += ["| Opened | Service · signal | Severity |", "|---|---|---|"]
        lines += [
            f"| {f.opened_at:%Y-%m-%d %H:%M} UTC | {_caught_by(f)} | {f.severity} |"
            for f in card.unattributed
        ]
    else:
        lines.append("None.")
    lines += [
        "",
        "## Every injection",
        "",
        "| Fault | Round | Started | Past the minimum (** clearly) | Caught | Time to catch |",
        "|---|---|---|---|---|---|",
    ]
    for fault in card.faults:
        for i in fault.injections:
            passed = ", ".join(
                f"{service} · {signal}{' **' if grade == CLEARLY_PAST else ''}"
                for (service, signal), grade in sorted(i.passed.items())
            )
            caught = _caught_by(i.caught_by) if i.caught_by else "no"
            taken = _minutes(i.time_to_catch) if i.time_to_catch is not None else "—"
            lines.append(
                f"| `{i.fault}` | {i.round} | {i.start:%Y-%m-%d %H:%M} UTC | {passed or 'none'} "
                f"| {caught} | {taken}{'' if i.in_time or not i.caught_by else ' (late)'} |"
            )
    return "\n".join(lines) + "\n"
