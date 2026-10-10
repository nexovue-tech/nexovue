"""The job queue's contract: what a job is, and the statement that adds one (ADR-020).

The queue is the `jobs` table. Beat, the scheduler's own tasks and the API all add
jobs; only the scheduler's workers claim and run them. This module holds what
every side must agree on — and nothing that runs SQL, because this package may
not import a database driver (`test_cross_service_contracts.py`). Each side
executes `ENQUEUE` on its own connection.

**Only an interactive click wakes a worker at once.** `NOTIFY` takes a lock every
committing transaction in the cluster waits on, so beat's steady volume is picked
up by the workers' one-second poll instead, and `NOTIFY` is spent only on Run now
and a send test, where a person is waiting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final

# One channel for every queue; the payload names the queue, and a worker wakes
# only for one it claims from.
NOTIFY_CHANNEL: Final = "nexovue_jobs"

# A task that names no limits gets these: long enough for a sweep or a delivery,
# short enough that a hung task frees its worker within a minute.
DEFAULT_SOFT_LIMIT_SECONDS: Final = 30
DEFAULT_HARD_LIMIT_SECONDS: Final = 60

ENQUEUE: Final = """
INSERT INTO jobs (
    queue, task, kwargs, org_id, run_after, expires_at,
    max_attempts, soft_limit_seconds, hard_limit_seconds
)
VALUES (
    :queue, :task, CAST(:kwargs AS jsonb), CAST(:org_id AS uuid),
    now() + make_interval(secs => CAST(:countdown_seconds AS double precision)),
    now() + make_interval(secs => CAST(:expires_seconds AS double precision)),
    :max_attempts, :soft_limit_seconds, :hard_limit_seconds
)
"""

WAKE: Final = "SELECT pg_notify(:channel, :queue)"


@dataclass(frozen=True, slots=True)
class JobRequest:
    """One job to add.

    `countdown_seconds` delays its first run; `expires_seconds` drops it unrun once
    that long has passed since it was added — a probe due every minute is worthless
    five minutes late. `max_attempts` counts runs, so a task that may retry twice
    says 3. `org_id` lets erasing an organization remove its queued work.
    """

    task: str
    queue: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    org_id: str | None = None
    countdown_seconds: float = 0
    expires_seconds: float | None = None
    max_attempts: int = 1
    soft_limit_seconds: int = DEFAULT_SOFT_LIMIT_SECONDS
    hard_limit_seconds: int = DEFAULT_HARD_LIMIT_SECONDS

    def parameters(self) -> dict[str, Any]:
        """`ENQUEUE`'s bound values."""
        return {
            "queue": self.queue,
            "task": self.task,
            "kwargs": json.dumps(self.kwargs),
            "org_id": self.org_id,
            "countdown_seconds": float(self.countdown_seconds),
            "expires_seconds": None
            if self.expires_seconds is None
            else float(self.expires_seconds),
            "max_attempts": self.max_attempts,
            "soft_limit_seconds": self.soft_limit_seconds,
            "hard_limit_seconds": self.hard_limit_seconds,
        }

    def wake_parameters(self) -> dict[str, str]:
        """`WAKE`'s bound values, for a job somebody is waiting on."""
        return {"channel": NOTIFY_CHANNEL, "queue": self.queue}
