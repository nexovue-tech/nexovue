"""The scheduler's complete settings surface, read from the environment at boot.

Same two rules as every other service: nothing carrying a credential has a
default, and a missing or malformed value stops the process with a message
naming the environment variable.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Annotated, Final, Literal

from pydantic import Field, PostgresDsn, RedisDsn, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic_settings import SettingsError as SettingsSourceError

Environment = Literal["development", "staging", "production"]
LogLevel = Literal["debug", "info", "warn", "error"]
LogFormat = Literal["json", "console"]

SERVICE_NAME: Final = "scheduler"

# The two queues are separated deliberately: Playwright is memory-hungry and
# slow, and must not share a worker pool with sub-second HTTP checks.
QUEUE_LIGHT: Final = "light"
QUEUE_BROWSER: Final = "browser"


class SettingsError(Exception):
    """Configuration that stops the process, phrased for the person fixing it."""


class Settings(BaseSettings):
    """Everything the scheduler reads from its environment."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    env: Environment
    log_level: LogLevel = "info"
    log_format: LogFormat = "json"

    # --- infrastructure, named identically in every service -----------------
    postgres_dsn: PostgresDsn
    redis_url: RedisDsn
    # NoDecode keeps pydantic-settings from JSON-decoding the value, so the
    # validator below sees the comma-separated string the Go services read.
    kafka_brokers: Annotated[list[str], NoDecode, Field(min_length=1)]

    # Probes are served on their own port: a Celery worker has no HTTP surface
    # of its own, and an orchestrator still has to be able to ask.
    scheduler_health_port: Annotated[int, Field(ge=1, le=65535)] = 8081

    # Every task gets a hard ceiling. A probe that hangs holds a worker slot,
    # and a held slot is a check that silently stops running.
    task_soft_time_limit_seconds: Annotated[int, Field(gt=0)] = 30
    task_hard_time_limit_seconds: Annotated[int, Field(gt=0)] = 60

    # A browser flow is many steps, each of which may wait on a page. Sharing
    # the limits above meant a multi-step flow was killed part way through and
    # reported as the customer's site failing when it was our clock that ran
    # out. Per-step timeouts are divided out of this budget, so a flow always
    # finishes inside it.
    browser_task_soft_time_limit_seconds: Annotated[int, Field(gt=0)] = 180
    browser_task_hard_time_limit_seconds: Annotated[int, Field(gt=0)] = 240

    # --- publishing ---------------------------------------------------------
    # Named here rather than shared, the same way the receiver and the worker
    # each name their own: one service's topic configuration should not be able
    # to move another's.
    kafka_metrics_topic: str = "metrics-raw"
    # Browser flows are logs, not metrics — the frozen fixture for that door
    # carries SIGNAL_LOGS, because a flow's result is a narrative of steps with
    # a failure message, not a number.
    kafka_logs_topic: str = "logs-raw"

    # Stamped on every probe result as `cloud.region`. A check answers
    # differently from different places, and a reading with no location cannot
    # be compared with one taken elsewhere — so this is part of the measurement,
    # not deployment trivia.
    prober_region: str = "local"

    # Where a browser flow's screenshot is kept. The log record carries a
    # reference rather than the image, because a screenshot is megabytes and a
    # log record that size would be refused long before it reached storage.
    synthetics_bucket: str = "nexovue-synthetics"
    synthetics_endpoint_url: str = "http://seaweedfs:8333"

    # Which region's API to call when assuming a customer's role. Not a
    # credential: customers grant us a role to assume rather than handing over
    # keys, so there is nothing secret in this service's configuration.
    aws_region: str = "eu-west-1"

    # --- beat leadership ----------------------------------------------------
    # More than one beat process runs, so that a dead one does not stop every
    # customer's checks. Only the holder of this lock schedules; the others wait
    # and take over. Two beats scheduling at once would double every check,
    # which is the failure this exists to prevent.
    beat_leader_key: str = "nexovue:scheduler:beat-leader"

    # How long the lock survives without a refresh. This is the longest a check
    # can be delayed by a leader dying: the successor cannot know the holder is
    # gone until the key expires. Short enough to bound that gap, long enough to
    # outlive a slow tick.
    beat_leader_ttl_seconds: Annotated[int, Field(gt=0)] = 30

    # How often a follower asks whether the lock is free. It is also how often
    # the leader refreshes, and must stay well under the TTL or the leader will
    # expire itself while still running.
    beat_leader_poll_seconds: Annotated[int, Field(gt=0)] = 5

    # How often the schedule is re-read from Postgres. This is the delay between
    # a customer saving a check in the UI and it running, which is the whole
    # reason the schedule lives in a table rather than in code.
    beat_refresh_seconds: Annotated[int, Field(gt=0)] = 30

    # How often beat says what it is doing. The Go workers have had an ingest
    # heartbeat since M1; the scheduler had nothing, so a beat that had stopped
    # scheduling looked exactly like one with nothing due. For an uptime product
    # that is the worst blind spot there is — the checker failing silently.
    beat_heartbeat_seconds: Annotated[int, Field(gt=0)] = 60

    @field_validator("kafka_brokers", mode="before")
    @classmethod
    def _split_broker_list(cls, value: object) -> object:
        """Comma-separated, the same shape the Go services read."""
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    @property
    def is_development(self) -> bool:
        return self.env == "development"


def describe_validation_error(error: ValidationError) -> str:
    """Render a settings failure as one line per offending environment variable."""
    lines = [f"  {_variable_name(problem['loc'])}: {problem['msg']}" for problem in error.errors()]
    return "invalid configuration:\n" + "\n".join(lines)


def _variable_name(location: tuple[int | str, ...]) -> str:
    return str(location[0]).upper() if location else "(unknown variable)"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The one Settings instance, built on first use and cached for the process."""
    try:
        return Settings()  # values come from the environment, not the caller
    except ValidationError as error:
        raise SettingsError(describe_validation_error(error)) from error
    except SettingsSourceError as error:
        # A value pydantic-settings could not even parse into the field's type.
        raise SettingsError(f"invalid configuration: {error}") from error


def load_settings_or_exit() -> Settings:
    """Settings for a process entry point: bad configuration exits immediately."""
    try:
        return get_settings()
    except SettingsError as error:
        sys.exit(f"{SERVICE_NAME}: {error}")
