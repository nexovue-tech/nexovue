"""The API's complete settings surface, read from the environment once, at boot.

Two rules hold here and in every other service:

* nothing that carries a credential has a default — a service that invents a
  password fails later, somewhere confusing, instead of now;
* a missing or malformed value stops the process with a message naming the
  environment variable, never a ``None`` that surfaces as an attribute error on
  the first request.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Annotated, Final, Literal

from pydantic import AnyUrl, Field, PostgresDsn, RedisDsn, UrlConstraints, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings import SettingsError as SettingsSourceError

Environment = Literal["development", "staging", "production"]
LogLevel = Literal["debug", "info", "warn", "error"]
LogFormat = Literal["json", "console"]

# ClickHouse speaks two protocols on two ports. Every service names the store
# with the same variable and the same scheme; only the port differs, because the
# API talks HTTP and the Go workers talk the native protocol.
ClickHouseDsn = Annotated[
    AnyUrl,
    UrlConstraints(allowed_schemes=["clickhouse", "clickhouses"], host_required=True),
]

DEFAULT_CLICKHOUSE_HTTP_PORT: Final = 8123

# Route prefix. Versioned from the very first route so no client is ever asked
# to move later.
API_PREFIX: Final = "/api/v1"


class SettingsError(Exception):
    """Configuration that stops the process, phrased for the person fixing it."""


class Settings(BaseSettings):
    """Everything the API reads from its environment."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    env: Environment
    log_level: LogLevel = "info"
    log_format: LogFormat = "json"

    # --- infrastructure, named identically in every service -----------------
    postgres_dsn: PostgresDsn
    clickhouse_dsn: ClickHouseDsn
    redis_url: RedisDsn

    # --- Postgres pool: sized here, never left to the library default -------
    postgres_pool_size: Annotated[int, Field(ge=1, le=100)] = 10
    postgres_max_overflow: Annotated[int, Field(ge=0, le=100)] = 5
    postgres_pool_timeout_seconds: Annotated[float, Field(gt=0)] = 10.0
    # Recycled below the usual one-hour idle cut of a proxy or a load balancer.
    postgres_pool_recycle_seconds: Annotated[int, Field(gt=0)] = 1800

    # --- ClickHouse: pool size and the two guards on every query ------------
    clickhouse_pool_size: Annotated[int, Field(ge=1, le=100)] = 8
    clickhouse_query_timeout_seconds: Annotated[int, Field(gt=0)] = 15
    clickhouse_max_result_rows: Annotated[int, Field(gt=0)] = 100_000

    # --- sessions -----------------------------------------------------------
    session_ttl_seconds: Annotated[int, Field(gt=0)] = 60 * 60 * 24 * 7

    rate_limit_enabled: bool = True
    # How many proxies sit in front of us. Zero means X-Forwarded-For is a value
    # the client chose, and the direct peer is the only honest client address.
    trusted_proxy_count: Annotated[int, Field(ge=0, le=10)] = 0

    cors_allowed_origins: list[str] = Field(default_factory=list)

    @property
    def clickhouse_host(self) -> str:
        return self.clickhouse_dsn.host or ""

    @property
    def clickhouse_port(self) -> int:
        return self.clickhouse_dsn.port or DEFAULT_CLICKHOUSE_HTTP_PORT

    @property
    def clickhouse_database(self) -> str:
        return (self.clickhouse_dsn.path or "/").lstrip("/")

    @property
    def clickhouse_secure(self) -> bool:
        return self.clickhouse_dsn.scheme == "clickhouses"

    @property
    def is_development(self) -> bool:
        return self.env == "development"


def describe_validation_error(error: ValidationError) -> str:
    """Render a settings failure as one line per offending environment variable.

    Pydantic reports the field name; the person reading the crash needs the
    variable name they have to set.
    """
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
        sys.exit(f"api: {error}")
