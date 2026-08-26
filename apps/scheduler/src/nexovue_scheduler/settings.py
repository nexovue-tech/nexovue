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
