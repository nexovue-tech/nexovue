"""F28: releases seen in traffic — the sync from the version rollup to `releases` and
`release_periods` (design 3, *From telemetry*).

`store` is the sync's way into the telemetry store, through the scheduler's one reader,
bound to one project. `sync` holds the minute's sweep, the per-project job and the
per-minute walk that decides periods.

**Its own queue** (D31, foundation section 8). The sweep and the project jobs run on
`releases`, taken by `scheduler-light` beside detection's and F19's, capped at two of
its slots and under the shared floor, so a slow ClickHouse read holds at most two seats
and probes and alert sweeps keep theirs (AC72). Detection and the alert evaluation
sweeps never import this package (`test_detection_isolation.py`, AC42).
"""

from __future__ import annotations

from typing import Final

RELEASES_QUEUE: Final = "releases"
# At most this many release jobs at once in one worker (a guess; design 3, step 2).
RELEASES_CONCURRENCY: Final = 2
QUEUE_CAPS: Final = {RELEASES_QUEUE: RELEASES_CONCURRENCY}
QUEUES: Final = tuple(QUEUE_CAPS)

# Named here rather than imported from `sync`, so beat schedules the sweep without
# loading the store's client.
SWEEP_RELEASES_TASK: Final = "nexovue_scheduler.releases.sweep"
OBSERVE_PROJECT_TASK: Final = "nexovue_scheduler.releases.observe_project"

__all__ = [
    "OBSERVE_PROJECT_TASK",
    "QUEUES",
    "QUEUE_CAPS",
    "RELEASES_CONCURRENCY",
    "RELEASES_QUEUE",
    "SWEEP_RELEASES_TASK",
]
