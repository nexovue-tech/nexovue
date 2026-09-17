"""The probe task's name, arguments and queues: the contract between whoever
dispatches a run — beat on a schedule, the API for Run now — and the worker.

**The whole definition travels in the task** (`probes/tasks.py`), so a worker
never reads Postgres on the path of a measurement. Secrets travel with it
*sealed*: the queue holds ciphertext only, and the worker opens a value
immediately before the request that uses it (ADR-010).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nexovue_platform.checks.config import parse_config, run_seconds_of
from nexovue_platform.checks.maintenance import Recurrence

RUN_CHECK_TASK: Final = "nexovue_scheduler.probes.run_check"
SWEEP_HEARTBEATS_TASK: Final = "nexovue_scheduler.probes.sweep_heartbeats"
RECORD_OUTCOME_TASK: Final = "nexovue_scheduler.probes.record_outcome"
SWEEP_UNATTACHED_FILES_TASK: Final = "nexovue_scheduler.probes.sweep_unattached_files"

# Queues shared by every location — state writes, sweeps, pollers.
SHARED_QUEUE: Final = "light"

# How long a Run now result waits to be collected. Long enough for a slow
# request and a console that polls, short enough that a response body does not
# linger in Redis.
RUN_NOW_RESULT_TTL_SECONDS: Final = 300


# Room above a check's own time limits for opening secrets, reading files and
# publishing, so our clock never runs out before the request's own timeout does.
TASK_OVERHEAD_SECONDS: Final = 15


def time_limits_for(config: dict[str, Any]) -> dict[str, int]:
    """The soft and hard task limits for one run, as `apply_async` options.

    Every dispatch sets them — beat, a confirmation, Run now. A run under the
    worker's default limit could be cut off mid-request, and libcurl reports our
    interruption as the target failing.
    """
    try:
        run_seconds = run_seconds_of(parse_config(config))
    except ValidationError:
        run_seconds = 0
    return {
        "soft_time_limit": run_seconds + TASK_OVERHEAD_SECONDS,
        "time_limit": run_seconds + 2 * TASK_OVERHEAD_SECONDS,
    }


def probe_queue(location: str) -> str:
    """Where a run from `location` is dispatched. A worker consumes only its own
    location's queue, which is what makes a location a place rather than a label."""
    return f"{SHARED_QUEUE}.{location}"


def run_now_result_key(org_id: str, request_id: str) -> str:
    """Tenant first, so a guessed request id from another organization finds nothing."""
    return f"nexovue:check-run:{org_id}:{request_id}"


class _Task(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileReference(_Task):
    name: str
    content_type: str
    size_bytes: int


class MaintenanceSchedule(_Task):
    starts_at: str
    ends_at: str
    recurrence: Recurrence = "none"
    repeat_until: str | None = None


class CheckDefinition(_Task):
    """Everything a run needs, and nothing a worker would have to look up."""

    # None for Run now on a check that has not been saved.
    check_id: str | None
    org_id: str
    project_id: str
    name: str
    type: str
    config: dict[str, Any]
    interval_seconds: int
    locations: Annotated[list[str], Field(min_length=1)]
    degraded_ms: int | None = None
    failing_ms: int | None = None
    retries: int = 1
    variables: dict[str, str] = {}
    # Name to sealed value, base64. Never plaintext.
    sealed_secrets: dict[str, str] = {}
    files: dict[str, FileReference] = {}
    maintenance: list[MaintenanceSchedule] = []


def new_run_id() -> str:
    return str(uuid.uuid4())


def run_now_arguments(
    definition: CheckDefinition, location: str, request_id: str
) -> dict[str, Any]:
    """The keyword arguments the API sends `run_check` for Run now. Stated here, beside
    the task's name, and held against the task's signature by a contract test."""
    return {
        "definition": definition.model_dump(mode="json"),
        "location": location,
        "attempt": 1,
        "run_now_request_id": request_id,
    }
