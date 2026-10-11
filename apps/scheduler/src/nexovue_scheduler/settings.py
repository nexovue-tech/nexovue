"""The scheduler's complete settings surface, read from the environment at boot.

Same two rules as every other service: nothing carrying a credential has a
default, and a missing or malformed value stops the process with a message
naming the environment variable.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Annotated, Final, Literal

from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.email.settings import (
    EmailProvider,
    EmailSettings,
    email_settings,
    refuse_console_mail_in_production,
)
from pydantic import Field, PostgresDsn, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic_settings import SettingsError as SettingsSourceError

Environment = Literal["development", "staging", "production"]
LogLevel = Literal["debug", "info", "warn", "error"]
LogFormat = Literal["json", "console"]

SERVICE_NAME: Final = "scheduler"


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
    # NoDecode keeps pydantic-settings from JSON-decoding the value, so the
    # validator below sees the comma-separated string the Go services read.
    kafka_brokers: Annotated[list[str], NoDecode, Field(min_length=1)]

    # --- publishing ---------------------------------------------------------
    # Named here rather than shared, the same way the receiver and the worker
    # each name their own: one service's topic configuration should not be able
    # to move another's.
    kafka_metrics_topic: str = "metrics-raw"
    # A run's evidence — the request sent, the response received, the failing
    # assertion — is a log record: text, not a number.
    kafka_logs_topic: str = "logs-raw"

    # The probe location this worker is, stamped on every run as `cloud.region`
    # and `synthetic.check.location`. A check answers differently from different
    # places, and a reading with no location cannot be compared with one taken
    # elsewhere. A worker consumes `light.<this>` as well as `light`.
    prober_region: str = "local"

    # Every location this deployment has, so beat can run a check "from
    # everywhere" and a retry can go somewhere else. Must match the API's list.
    probe_locations: Annotated[list[str], NoDecode, Field(min_length=1)] = ["local"]

    # Where a DNS check asks when the customer named no resolver. Public resolvers,
    # never this container's own: that one answers `postgres` with our network.
    # Each is still an address the connection guard judges.
    dns_resolvers: Annotated[list[str], NoDecode, Field(min_length=1)] = ["1.1.1.1", "8.8.8.8"]

    # Opens check secrets immediately before a request uses them (ADR-010).
    # Empty means a check that uses a secret cannot run here, and says so.
    check_secret_key: str = ""
    # Keys that sealed check and channel secrets before the current one,
    # comma-separated; they only open. Kept until jobs queued before a rotation
    # have drained and the API's re-seal has run (docs/operations/key-rotation.md).
    check_secret_previous_keys: str = ""

    # The API's object store: the files checks send, read here, and every
    # upload a deleted organization left, erased here at night. All four or
    # none, as the API reads them.
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_region: str = "us-east-1"

    # Which region's API to call when assuming a customer's role. Not a
    # credential: customers grant us a role to assume rather than handing over
    # keys, so there is nothing secret in this service's configuration.
    aws_region: str = "eu-west-1"

    # Amazon's RDS CA bundle, for verifying a customer database's certificate.
    # asyncpg cannot use the system trust store, and RDS certificates are signed
    # by Amazon's own authority — so the path is supplied rather than assumed.
    # Empty is not a fallback: `rds_tls_context` refuses, because the alternative
    # is an unverified connection carrying a database credential.
    rds_ca_bundle_path: str = "/etc/ssl/certs/rds-global-bundle.pem"

    # --- alerts (F21) --------------------------------------------------------
    # Alert email goes through the same port, provider and variables the API's
    # account mail does — `nexovue_platform.email` — so both processes agree on
    # whether mail is configured. All of these or none, as the API reads them.
    email_provider: EmailProvider = "resend"
    email_api_key: str = ""
    email_sending_domain: str = ""
    email_from_address: str = ""
    email_reply_to: str = ""

    # Where the console is, for the link every alert carries to its evidence
    # (FR16). The same value the API puts in verification links.
    app_base_url: str = ""

    # The telemetry store, for the service threshold and new-error rules (ADR-012).
    # A read-only user belongs here. Empty means those rules are not evaluated on
    # this deployment, and the sweeps say so every minute rather than failing.
    clickhouse_dsn: str = ""

    # --- erasing deleted organizations (ADR-014) ----------------------------
    # The one credential in nexovue that can delete telemetry. Empty means no
    # deleted organization is erased on this deployment, and the erasure task
    # says so at ERROR every hour rather than doing nothing quietly.
    clickhouse_eraser_dsn: str = ""
    # The UTC hour the nightly erasure runs in. Move it to the hour with the
    # least ingest once production traffic shows which that is.
    erasure_hour_utc: Annotated[int, Field(ge=0, le=23)] = 2
    # The most organizations one group of mutations names. A larger queue is
    # erased in groups, waiting out `old_parts_lifetime` between them.
    erasure_batch_size: Annotated[int, Field(gt=0, le=1000)] = 100

    # --- beat leadership ----------------------------------------------------
    # More than one beat process runs, so that a dead one does not stop every
    # customer's checks. Only the holder of the leader lock (`leader.py`)
    # schedules; the others ask this often whether it is free. It is also the
    # longest a check can wait for a successor once a leader dies.
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

    # Where this process answers /healthz, /readyz and /metrics (`health.py`).
    # Internal: no edge routes it. Every role uses the same port in its own
    # container.
    health_addr: str = ":8081"

    @model_validator(mode="after")
    def _check_key_is_set_in_production(self) -> Settings:
        """A deployed prober opens check and channel secrets (readiness blocker 17);
        without the key every check that uses one fails as our error."""
        if self.env == "production" and not self.check_secret_key:
            raise ValueError("CHECK_SECRET_KEY is not set; production opens secrets with it.")
        return self

    @model_validator(mode="after")
    def _mail_is_real_in_production(self) -> Settings:
        refuse_console_mail_in_production(env=self.env, provider=self.email_provider)
        return self

    @field_validator("email_provider", mode="before")
    @classmethod
    def _provider_folded(cls, value: object) -> object:
        """Matched however it was typed, and blank is unset: both deployment files
        pass `EMAIL_PROVIDER: ${EMAIL_PROVIDER:-}`, the API reads it the same way."""
        if not isinstance(value, str):
            return value
        return value.strip().lower() or cls.model_fields["email_provider"].default

    @field_validator("probe_locations", "dns_resolvers", mode="before")
    @classmethod
    def _split_locations(cls, value: object) -> object:
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    @field_validator("kafka_brokers", mode="before")
    @classmethod
    def _split_broker_list(cls, value: object) -> object:
        """Comma-separated, the same shape the Go services read."""
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    # Lets a probe reach a private address, which the connect guard otherwise
    # refuses. Off unless asked for by name, and the property below requires two
    # independent things to be true — so a staging or production deployment that
    # somehow inherits this variable still refuses, because `env` is not
    # development there.
    #
    # It exists for one reason: the dev stack's probes have to reach the
    # OpenTelemetry Demo storefront, which compose publishes on the Docker host,
    # and a Docker host is a private address by construction.
    allow_private_probe_targets: bool = False

    @property
    def check_secret_keys(self) -> ConfiguredKeys:
        """Check and channel secrets' keyring, as configured. The API holds the same."""
        return ConfiguredKeys(self.check_secret_key, self.check_secret_previous_keys)

    @property
    def email(self) -> EmailSettings | None:
        """The email settings, or None when mail is not configured."""
        return email_settings(
            provider=self.email_provider,
            api_key=self.email_api_key,
            sending_domain=self.email_sending_domain,
            from_address=self.email_from_address,
            reply_to=self.email_reply_to,
        )

    @property
    def is_development(self) -> bool:
        return self.env == "development"

    @property
    def private_probe_targets_allowed(self) -> bool:
        """Whether a probe may open a socket to a private address.

        Loopback and link-local stay refused regardless; see
        `probes.addresses.may_connect_to`, which is where that line is drawn.
        """
        return self.allow_private_probe_targets and self.is_development


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
