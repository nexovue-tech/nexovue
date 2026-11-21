"""F19: reliability targets — counting each target's days and judging it each settled minute.

`store` is F19's way into the telemetry store, through the scheduler's one reader
with bounds lowered for a short job. `counts` turns a source's minutes into a day's
row, pure. `job` holds the three jobs and `queue_jobs`, which the detection
coordinator calls each settled minute (design, *Where it runs*).

The queue names are the platform's (`nexovue_platform.slo`), because the API queues
a history read. The caps are here, beside detection's: all three queues run on
`scheduler-light` under its floor (D66), so probes and alert sweeps keep their slots.
"""

from __future__ import annotations

from typing import Final

from nexovue_platform.slo import SLO_COUNTS_QUEUE, SLO_HISTORY_QUEUE, SLO_QUEUE

# At most this many of each at once in one worker (D66; guesses). Burn judging
# matches detection's 2; the slow reads take one shared seat each.
SLO_CONCURRENCY: Final = 2
SLO_COUNTS_CONCURRENCY: Final = 1
SLO_HISTORY_CONCURRENCY: Final = 1
QUEUE_CAPS: Final = {
    SLO_QUEUE: SLO_CONCURRENCY,
    SLO_COUNTS_QUEUE: SLO_COUNTS_CONCURRENCY,
    SLO_HISTORY_QUEUE: SLO_HISTORY_CONCURRENCY,
}
QUEUES: Final = tuple(QUEUE_CAPS)

__all__ = [
    "QUEUES",
    "QUEUE_CAPS",
    "SLO_CONCURRENCY",
    "SLO_COUNTS_CONCURRENCY",
    "SLO_COUNTS_QUEUE",
    "SLO_HISTORY_CONCURRENCY",
    "SLO_HISTORY_QUEUE",
    "SLO_QUEUE",
]
