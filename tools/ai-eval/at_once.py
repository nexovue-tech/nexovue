"""One hundred findings at once: how long the queue took to clear and how long a due probe waited
(plan T2.16; spec AC97, NFR1, NFR2).

The live test (`apps/scheduler/tests/test_bed_live.py`) opens the findings, enqueues their gathers
on a lane shaped like `scheduler-light` (evidence capped, a floor kept for probes), and hands the
times here. This module only reads those times, so what it says can be checked with no stack.

A number it reports is **a measurement of one machine under one load**, labelled as such wherever
it is quoted. It replaces the design's guess only when the bed's own run, on the install's
shape, says the same (Oracle, after Phase 2).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from score import Latency, time_to_first_explanation

LABEL = "opened_at_once"


@dataclass(frozen=True, slots=True)
class AtOnce:
    findings: int
    explained: int
    # From the first finding opening to the last explanation: how long the queue took to clear.
    cleared_after: timedelta | None
    # From the probe being due to its running: how long a due probe waited.
    probe_waited: timedelta | None
    # Whether the probe ran while explanations were still being written: a probe that ran after
    # the queue cleared waited for nothing and proves nothing about the floor.
    probe_ran_during_the_burst: bool
    latency: Latency | None


def summarize(
    *,
    started_at: Sequence[datetime],
    explained_at: Sequence[datetime | None],
    probe_due: datetime,
    probe_ran: datetime | None,
) -> AtOnce:
    """`explained_at[i]` is when finding `i` first had an explanation, or None if it never did."""
    if len(started_at) != len(explained_at):
        raise ValueError("one explanation time (or None) for each finding")
    samples = [(LABEL, begun, done) for begun, done in zip(started_at, explained_at, strict=True)]
    done = [each for each in explained_at if each is not None]
    cleared = (max(done) - min(started_at)) if done and started_at else None
    waited = None if probe_ran is None else probe_ran - probe_due
    during = bool(done) and probe_ran is not None and probe_ran < max(done)
    return AtOnce(
        findings=len(started_at),
        explained=len(done),
        cleared_after=cleared,
        probe_waited=waited,
        probe_ran_during_the_burst=during,
        latency=time_to_first_explanation(samples).get(LABEL),
    )


def _seconds(value: timedelta | None) -> str:
    return "never" if value is None else f"{value.total_seconds():.1f} s"


def render(result: AtOnce, *, machine: str) -> str:
    latency = result.latency
    return "\n".join(
        [
            f"- Measured on: {machine}. One run: a guess about any other machine.",
            f"- Findings opened at once: **{result.findings}**; explained: **{result.explained}**.",
            f"- The queue took **{_seconds(result.cleared_after)}** to clear.",
            f"- A due probe waited **{_seconds(result.probe_waited)}**"
            + ("" if result.probe_ran_during_the_burst else " (it ran after the burst: no proof)")
            + ".",
            f"- Time to the first explanation, 50th and 95th percentile: "
            f"**{_seconds(latency.p50 if latency else None)}** and "
            f"**{_seconds(latency.p95 if latency else None)}** over "
            f"{latency.count if latency else 0} findings, "
            f"{latency.missing if latency else 0} never explained.",
        ]
    )
