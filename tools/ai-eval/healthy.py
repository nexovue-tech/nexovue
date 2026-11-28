"""The healthy run's sampled windows (plan T2.16; spec AC16).

Over the week with no fault on, `rules.decide` runs on a stated number of sampled windows per
service **as if a finding had opened there**, reading the stored edges, deploys and traffic through
the same facts read the evidence job uses. Every cause stated is a false cause. Findings that
really opened in the week are listed on their own, with their stated cause; none opening is a
reported result, not a failure. Seeded data alone cannot prove this: `tools/detection-seed` writes
only `service_stats_1m` rows, so no rule would have input (AC16).

Each window is read as a finding with the signals `slower` and `failing`, the widest reading: the
traffic-rise rule (7) reads a slowdown, the edge rules read both.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from nexovue_scheduler.evidence import rules
from nexovue_scheduler.evidence.rules import Cause, Facts, FindingInput

FactsReader = Callable[[FindingInput], Awaitable[Facts]]
# A window opens at a whole minute, as a finding does.
_MINUTE: Final = timedelta(minutes=1)
# Windows keep clear of a real finding's open span by this long on each side.
KEEP_CLEAR: Final = timedelta(minutes=30)
# A guess: windows per service the founder's bar reads (AC16 says "a stated number").
DEFAULT_PER_SERVICE: Final = 100


@dataclass(frozen=True, slots=True)
class SampledWindow:
    service: str
    at: datetime


@dataclass(frozen=True, slots=True)
class RealFinding:
    """A finding that opened in the healthy week, listed on its own."""

    service: str
    started_at: datetime
    closed_at: datetime | None
    cause: Cause | None


@dataclass(frozen=True, slots=True)
class FalseCause:
    window: SampledWindow
    rule: str
    strength: str


@dataclass(frozen=True, slots=True)
class HealthyResult:
    windows: int
    false_causes: tuple[FalseCause, ...]
    # Windows whose facts were read with a failed read named: not a "none", and counted.
    windows_with_failed_reads: int
    real_findings: tuple[RealFinding, ...]

    @property
    def clean(self) -> bool:
        return not self.false_causes


def sample_windows(
    services: Sequence[str],
    *,
    start: datetime,
    end: datetime,
    per_service: int,
    seed: int = 1,
    real: Sequence[RealFinding] = (),
) -> list[SampledWindow]:
    """`per_service` distinct whole-minute times for each service, away from its real findings."""
    if per_service < 1 or end <= start:
        raise ValueError("sample at least one window, from a start before the end")
    minutes = int((end - start) / _MINUTE)
    rng = random.Random(seed)  # sampling, never a secret
    windows: list[SampledWindow] = []
    for service in sorted(services):
        spans = [
            (each.started_at - KEEP_CLEAR, (each.closed_at or end) + KEEP_CLEAR)
            for each in real
            if each.service == service
        ]
        taken: set[int] = set()
        attempts = 0
        while len(taken) < per_service and attempts < per_service * 50:
            attempts += 1
            offset = rng.randrange(minutes)
            at = start + offset * _MINUTE
            if offset in taken or any(low <= at < high for low, high in spans):
                continue
            taken.add(offset)
            windows.append(SampledWindow(service, at))
    return sorted(windows, key=lambda each: (each.service, each.at))


def finding_for(window: SampledWindow) -> FindingInput:
    stamp = window.at.strftime("%Y%m%dT%H%M")
    return FindingInput(
        id=f"healthy-{window.service}-{stamp}",
        scope="service",
        service=window.service,
        started_at=window.at,
        signals=("slower", "failing"),
    )


async def run_healthy(
    read_facts: FactsReader,
    windows: Sequence[SampledWindow],
    real: Sequence[RealFinding] = (),
    stopped: frozenset[str] = frozenset(),
) -> HealthyResult:
    false: list[FalseCause] = []
    failed = 0
    for window in windows:
        facts = await read_facts(finding_for(window))
        failed += bool(facts.failed_reads)
        cause = rules.decide(facts, stopped_rules=stopped)
        if cause is not None:
            false.append(FalseCause(window, cause.rule, cause.strength))
    return HealthyResult(len(windows), tuple(false), failed, tuple(real))


def render(result: HealthyResult) -> str:
    """The healthy run's section of the report, in counts."""
    lines = [
        f"- Sampled windows read as if a finding opened there: **{result.windows}**.",
        f"- Causes stated on them (every one false): **{len(result.false_causes)}**.",
        f"- Windows whose facts had a failed read: **{result.windows_with_failed_reads}**.",
        f"- Findings that really opened in the week: **{len(result.real_findings)}**"
        + (" (a reported result, not a failure)." if not result.real_findings else "."),
    ]
    if result.false_causes:
        lines += ["", "| Rule | False causes | Strength |", "|---|---|---|"]
        per_rule: dict[tuple[str, str], int] = {}
        for each in result.false_causes:
            key = (each.rule, each.strength)
            per_rule[key] = per_rule.get(key, 0) + 1
        lines += [
            f"| {rule} | {count} | {strength} |"
            for (rule, strength), count in sorted(per_rule.items())
        ]
    if result.real_findings:
        lines += ["", "| Service | Started | Stated cause |", "|---|---|---|"]
        lines += [
            f"| {each.service} | {each.started_at.isoformat()} | "
            f"{each.cause.rule if each.cause else 'none'} |"
            for each in result.real_findings
        ]
    return "\n".join(lines)
