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
from dataclasses import dataclass
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

# Where a customer points their OTLP exporter in local development. Named here
# rather than on the field alone so the seed can report it without building the
# full settings object, which needs credentials the seed has no use for.
DEFAULT_OTLP_ENDPOINT: Final = "http://localhost:14318"

# Route prefix. Versioned from the very first route so no client is ever asked
# to move later.
API_PREFIX: Final = "/api/v1"


class SettingsError(Exception):
    """Configuration that stops the process, phrased for the person fixing it."""


@dataclass(frozen=True)
class ObjectStoreSettings:
    """A complete object store configuration, which is all four values or none.

    Separate from Settings so the store's client cannot be handed a half-filled
    configuration: a caller either has one of these or has nothing to try.
    """

    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str


# The environment variables an unconfigured object store tells the caller to
# set. Spelled once so the message and the fields cannot drift apart.
OBJECT_STORE_VARIABLES: Final = (
    "S3_ENDPOINT",
    "S3_BUCKET",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
)


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

    # Where a customer points their OTLP exporter. Signup hands this back, so it
    # has to name the receiver as the *customer* reaches it, not as we reach it.
    otlp_endpoint: str = DEFAULT_OTLP_ENDPOINT

    rate_limit_enabled: bool = True
    # How many proxies sit in front of us. Zero means X-Forwarded-For is a value
    # the client chose, and the direct peer is the only honest client address.
    trusted_proxy_count: Annotated[int, Field(ge=0, le=10)] = 0

    cors_allowed_origins: list[str] = Field(default_factory=list)

    # --- object storage: source map bytes now, the cold tier later ----------
    # The generic S3 API only (ADR 0015): SeaweedFS locally, AWS S3 in
    # deployment. Optional as a group, and deliberately not required at boot —
    # a deployment that uploads no source maps needs no object store, and an
    # API that refused to start without one would break every existing install
    # on upgrade. The endpoints that need it refuse by variable name instead.
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    # SeaweedFS ignores the region and AWS requires one; signing needs a value
    # either way, so the default is the region AWS itself treats as canonical.
    s3_region: str = "us-east-1"

    @property
    def object_store(self) -> ObjectStoreSettings | None:
        """The object store's settings, or None when it is not configured.

        All four or none: a half-configured store fails at the first upload with
        a signing error naming nothing, where this names the variables.
        """
        parts = (self.s3_endpoint, self.s3_bucket, self.s3_access_key_id, self.s3_secret_access_key)
        if not all(parts):
            return None
        return ObjectStoreSettings(
            endpoint=str(self.s3_endpoint),
            bucket=str(self.s3_bucket),
            access_key_id=str(self.s3_access_key_id),
            secret_access_key=str(self.s3_secret_access_key),
            region=self.s3_region,
        )

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

    @property
    def session_cookie_secure(self) -> bool:
        """Secure everywhere but development, which is served over plain http."""
        return not self.is_development


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
