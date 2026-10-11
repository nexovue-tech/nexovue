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
from urllib.parse import urlsplit

from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.email.settings import EmailProvider, EmailSettings, email_settings
from pydantic import (
    AnyUrl,
    Field,
    PostgresDsn,
    SecretStr,
    UrlConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic_settings import SettingsError as SettingsSourceError

Environment = Literal["development", "staging", "production"]
LogLevel = Literal["debug", "info", "warn", "error"]
LogFormat = Literal["json", "console"]

# Which implementation of a port is used — `EmailProvider`'s rule, now in
# `nexovue_platform.email.settings` because the scheduler sends mail too. The
# vendor's name is the *value*, never the key.
#
# Payments are expected to end up as two of these — a domestic gateway and a
# merchant of record, because a non-EU seller owes EU VAT from the first euro —
# which is why the payment one is named for the role it fills.
DomesticPaymentProvider = Literal["razorpay"]


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

# Hosts that mean 'this machine'. Correct in development and a silent disaster
# anywhere else, because signup prints this address to the customer.
_ADDRESSES_ONLY_WE_CAN_REACH: Final = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

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


@dataclass(frozen=True, slots=True)
class DomesticPaymentSettings:
    """A complete domestic-gateway configuration, which is all four values or none.

    Same shape and same reason as `EmailSettings`: a half-filled configuration
    fails at the first checkout, and a gateway's "authentication failed" tells a
    reader nothing about which variable was missing.

    Named for the *role* rather than the processor, which is the port's rule in
    another file: the likely end state is two of these — a domestic gateway for
    India and a merchant of record for everywhere else, because a non-EU seller
    owes EU VAT from the first euro. A type called after one of them would
    describe half a system.
    """

    provider: DomesticPaymentProvider
    # The processor's account identifier. Public-ish, but paired with the key.
    app_id: str
    # The signing key. It is both the API credential *and* the key a webhook
    # signature is verified with, which is why nothing else may hold it.
    secret_key: str
    # The processor's API root. Derived from the provider unless overridden —
    # see `PROVIDER_API_ROOTS`, and the misconfiguration that motivated it.
    base_url: str
    # What a webhook signature is verified with. **Not always the API key.** One
    # gateway signs with the same secret it authenticates with; another issues a
    # separate webhook secret in its dashboard, and verifying with the API key
    # there refuses every delivery with a message that reads like a forgery.
    # Falls back to `secret_key` for the processors where they are the same.
    webhook_secret: str
    # Which currencies this *account* may actually charge in. Not a property of
    # the gateway — a property of the arrangement with it: an Indian account
    # takes rupees from the day it opens and dollars only once the provider has
    # approved international payments, which is an application with a waiting
    # time. Empty means no restriction is known, and everything priced is
    # offered.
    currencies: frozenset[str] = frozenset()


# Each provider's API root.
#
# **Derived rather than configured, because it was got wrong the first time it
# mattered**: the provider was switched to `razorpay` while `base_url` still held
# the other gateway's sandbox host, which authenticates as a bad key rather than
# as a wrong address. A value that can only be derived cannot be stale.
#
# Still overridable by `PAYMENTS_DOMESTIC_BASE_URL` — a test transport and a
# vendor's regional host both need it — but nothing has to set it to be right.
PROVIDER_API_ROOTS: Final[dict[str, str]] = {
    # https://razorpay.com/docs/api/
    "razorpay": "https://api.razorpay.com/v1",
}

# **The key decides test or live, not a setting** (readiness group 3). Razorpay
# has one host for both; a `rzp_test_` key writes to the test books and takes no
# money, a `rzp_live_` key debits real cards. `PAYMENTS_DOMESTIC_ENV` chose
# between two identical hosts and was deleted for it.
LIVE_KEY_PREFIXES: Final[dict[str, str]] = {"razorpay": "rzp_live_"}


# The environment variables an unconfigured payment processor tells the caller
# to set. Spelled once so the message and the fields cannot drift apart.
DOMESTIC_PAYMENT_VARIABLES: Final = (
    "PAYMENTS_DOMESTIC_PROVIDER",
    "PAYMENTS_DOMESTIC_APP_ID",
    "PAYMENTS_DOMESTIC_SECRET_KEY",
    "PAYMENTS_DOMESTIC_BASE_URL",
)


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
    # The reader: every screen and the assistant read ClickHouse through it
    # (deploy/clickhouse/users.d/reader.xml). It can only read, and the server
    # bounds its memory and time whatever a query asks for.
    clickhouse_dsn: ClickHouseDsn

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

    # The key that seals the one credential this system cannot hash: the TOTP
    # shared secret, which has to be recoverable because verifying a code means
    # recomputing an HMAC with it. `auth/encryption.py` states what this does and
    # does not protect.
    #
    # Optional at boot and deliberately so, like the object store above: a
    # deployment where nobody has enrolled a second factor needs no key, and an
    # API that refused to start without one would stop an existing install on
    # upgrade. The endpoints that need it refuse with 503 instead — never by
    # storing a secret in the clear. Generate one with
    # `auth.encryption.generate_key()`.
    credential_encryption_key: str = ""
    # Keys that sealed TOTP secrets before the current one, comma-separated. They
    # only open; `python -m nexovue_api.keys reseal` moves what they hold to the
    # current key, after which they are removed (docs/operations/key-rotation.md).
    credential_encryption_previous_keys: str = ""

    # --- synthetic checks ----------------------------------------------------
    # Seals check secrets (ADR-010). Its own key rather than the TOTP one, so a
    # rotation of either leaves the other alone; the scheduler holds the same
    # value to open what a request uses. Optional at boot for the same reason as
    # the key above: the routes that need it refuse with 503 instead.
    check_secret_key: str = ""
    # As `credential_encryption_previous_keys`, for this key. The scheduler holds
    # the same list, to open jobs queued before a rotation.
    check_secret_previous_keys: str = ""

    # The probe locations a check can run from. The hosted service lists its
    # regions; a self-hosted install has one. Each is a scheduler worker
    # consuming `light.<location>`.
    probe_locations: Annotated[list[str], NoDecode, Field(min_length=1)] = ["local"]

    # Where a customer points their OTLP exporter. Signup hands this back, so it
    # has to name the receiver as the *customer* reaches it, not as we reach it.
    otlp_endpoint: str = DEFAULT_OTLP_ENDPOINT

    rate_limit_enabled: bool = True
    # How many API processes share the traffic. Each enforces its share of every
    # in-memory limit — the limit divided by this — so a deployment running
    # three replicas says 3, or each would allow three times the policy.
    api_instances: Annotated[int, Field(ge=1)] = 1
    # Whether anyone who finds the page may create an organization at /signup
    # (F31, D10). **Closed unless a deployment opens it**, and every target says
    # which it wants rather than inheriting one.
    #
    # This defaulted to open, for a reason that no longer holds: a self-hosted
    # install had no other way in, so closing signup left it with no account at
    # all. `FIRST_OWNER_EMAIL` is that other way in now
    # (`docs/changes/seeded-from-the-environment/`), and an open default on
    # somebody else's install means a stranger who finds the page creates an
    # organization inside it and sends telemetry at their expense.
    #
    # nexovue's cloud has never wanted it: a customer pays on the website and is
    # onboarded by staff (ADR-008). Development and the e2e suite do, and
    # `docker-compose.yaml` says so.
    signup_enabled: bool = False
    # How many proxies sit in front of us. Zero means X-Forwarded-For is a value
    # the client chose, and the direct peer is the only honest client address.
    trusted_proxy_count: Annotated[int, Field(ge=0, le=10)] = 0

    cors_allowed_origins: list[str] = Field(default_factory=list)

    # --- object storage: source map bytes now, the cold tier later ----------
    # The generic S3 API only: SeaweedFS locally, AWS S3 in
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

    # --- email: all of these or none, see EmailSettings ---------------------
    email_provider: EmailProvider = "resend"
    email_api_key: str = ""
    email_sending_domain: str = ""
    email_from_address: str = ""
    email_reply_to: str = ""

    # --- payments: all of these or none, see DomesticPaymentSettings --------
    # The vendor's name is the *value*, never the key: `PAYMENTS_DOMESTIC_*`
    # survives a switch from one gateway to another, which is the point.
    payments_domestic_provider: DomesticPaymentProvider = "razorpay"
    payments_domestic_app_id: str = ""
    payments_domestic_secret_key: str = ""
    payments_domestic_base_url: str = ""
    payments_domestic_webhook_secret: str = ""
    # Comma-separated, e.g. "INR" or "INR,USD". Empty until a deployment knows
    # its own answer; see `DomesticPaymentSettings.currencies`.
    payments_domestic_currencies: str = ""

    @model_validator(mode="after")
    def _reachable_by_a_customer(self) -> Settings:
        """Refuse to hand every new customer an endpoint only we can reach.

        `otlp_endpoint` is returned by signup and printed on the onboarding
        screen, so its default — localhost, correct for a laptop — becomes the
        address a deployed install tells the world to export to. Nothing fails
        when that happens: the customer sets two variables, their exporter posts
        to their own machine, and the screen waits for a first span that cannot
        arrive. They conclude the product does not work, and no error reaches us
        because no request does.

        Caught at start-up rather than at the first signup, for the reason the
        provider literals above are: a deployment that is wrong should refuse to
        run, not wait to be wrong in front of somebody.
        """
        if self.env == "development":
            return self
        host = urlsplit(self.otlp_endpoint).hostname or ""
        if host in _ADDRESSES_ONLY_WE_CAN_REACH:
            raise ValueError(
                f"OTLP_ENDPOINT is {self.otlp_endpoint!r}, which no customer can reach. "
                f"Signup hands this to every new account and the onboarding screen prints "
                f"it. Set it to the receiver's public address in {self.env}."
            )
        return self

    @model_validator(mode="after")
    def _payments_match_the_estate(self) -> Settings:
        """Production takes real money with a live key; nothing else may hold one.

        A test key in production grants paid plans for payments that never
        happen; a live key on a laptop or a staging install debits real cards.
        Both are refused at start-up, naming the variable.
        """
        app_id = self.payments_domestic_app_id
        if not app_id:
            return self
        live = app_id.startswith(LIVE_KEY_PREFIXES[self.payments_domestic_provider])
        if self.env == "production" and not live:
            raise ValueError(
                "PAYMENTS_DOMESTIC_APP_ID is not a live key, so production would grant paid "
                "plans for payments that never happen. Set the live key."
            )
        if self.env != "production" and live:
            raise ValueError(
                f"PAYMENTS_DOMESTIC_APP_ID is a live key, so {self.env} would debit real cards. "
                "Use a test key outside production."
            )
        return self

    @model_validator(mode="after")
    def _keys_are_set_in_production(self) -> Settings:
        """A deployed install seals with both keys (readiness blocker 17).

        Optional elsewhere so development and tests start without them, the
        routes answering 503. In production a missing key is a customer finding
        out first — two-factor sign-in that cannot be enabled, a check secret
        that cannot be saved — so the API refuses to start, naming it.
        """
        if self.env != "production":
            return self
        for variable, value in (
            ("CREDENTIAL_ENCRYPTION_KEY", self.credential_encryption_key),
            ("CHECK_SECRET_KEY", self.check_secret_key),
        ):
            if not value:
                raise ValueError(f"{variable} is not set; production seals secrets with it.")
        return self

    @field_validator("probe_locations", mode="before")
    @classmethod
    def _split_locations(cls, value: object) -> object:
        """Comma-separated, the way the scheduler reads the same variable."""
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    @field_validator("payments_domestic_provider", "email_provider", mode="before")
    @classmethod
    def _folded(cls, value: object) -> object:
        """A provider name is matched however it was typed.

        `model_config` already folds variable *names*; the values were still
        case-sensitive, so `PAYMENTS_DOMESTIC_PROVIDER=Razorpay` failed to start
        with a validation error naming a literal rather than the typo.
        """
        return value.strip().lower() if isinstance(value, str) else value

    # Where a verification or reset link points. It is the customer-facing app,
    # never this API: the link is clicked in a browser and lands on a screen that
    # then calls the endpoint. Two hostnames, and this is the app one.
    # No default — a link built against a guessed host is a link to nowhere, and
    # the failure is a customer who cannot verify rather than a stack trace.
    app_base_url: str = ""

    # nexovue's own operator (`docs/changes/super-admin-in-the-console/`). Both
    # absent means no super admin is seeded and the platform surface is
    # unreachable, which is the right default for a self-hosted install: it has
    # no nexovue to administer it. Set together or not at all.
    super_admin_email: str = ""
    super_admin_password: SecretStr = SecretStr("")

    # **The customer's own first account, for a self-hosted install** — the other
    # half of the pair above, and its mirror image. There, nexovue administers
    # the platform; here, somebody who installed nexovue administers their own
    # organization, and there is nobody to invite them because they are the first
    # person in the system.
    #
    # All three absent means nothing is seeded, which is the right default for
    # the hosted service: customers there arrive through signup, and an
    # organization conjured at boot would belong to nobody.
    #
    # **Read once, on the first run, and never again** — unlike the operator
    # above, whose password this rewrites every boot. After the first boot the
    # account is the customer's: they change the password in the console, and a
    # restart that reset it to whatever is in a compose file would take their
    # account away from them. It is the convention a self-hoster expects
    # (Grafana's `GF_SECURITY_ADMIN_PASSWORD` behaves the same way).
    first_organization_name: str = "My organization"
    first_owner_email: str = ""
    first_owner_password: SecretStr = SecretStr("")

    # This API's own public base URL — the other hostname of the pair above.
    # It exists for one reason: the MCP server (F36) must be told which `Host`
    # headers it answers on, and the SDK refuses everything else with 421. Left
    # empty it falls back to the app's host and to localhost, which is right in
    # development, where both are the same machine.
    api_public_url: str = ""

    # A verification link is followed at leisure; a reset link is a live
    # credential to somebody's account. Different lifetimes, and the difference
    # is the reason they are two settings rather than one.
    verify_email_ttl_seconds: int = 24 * 60 * 60
    reset_password_ttl_seconds: int = 60 * 60

    @property
    def check_secret_keys(self) -> ConfiguredKeys:
        """Check and channel secrets' keyring, as configured."""
        return ConfiguredKeys(self.check_secret_key, self.check_secret_previous_keys)

    @property
    def credential_keys(self) -> ConfiguredKeys:
        """TOTP secrets' keyring, as configured."""
        return ConfiguredKeys(
            self.credential_encryption_key, self.credential_encryption_previous_keys
        )

    @property
    def email(self) -> EmailSettings | None:
        """The email settings, or None when mail is not configured — the rule in
        `nexovue_platform.email.settings.email_settings`, which the scheduler applies too."""
        return email_settings(
            provider=self.email_provider,
            api_key=self.email_api_key,
            sending_domain=self.email_sending_domain,
            from_address=self.email_from_address,
            reply_to=self.email_reply_to,
        )

    @property
    def domestic_payments(self) -> DomesticPaymentSettings | None:
        """The domestic gateway's settings, or None when payments are not configured.

        None rather than an error, for `email`'s reason: an API that cannot take
        money still serves every screen, and an install that never charges
        anybody needs no gateway. The routes that need one refuse by variable
        name instead.
        """
        provider = self.payments_domestic_provider
        # Only the credentials have to be supplied; the host follows the provider.
        if not (self.payments_domestic_app_id and self.payments_domestic_secret_key):
            return None
        return DomesticPaymentSettings(
            provider=provider,
            app_id=self.payments_domestic_app_id,
            secret_key=self.payments_domestic_secret_key,
            # A trailing slash would make every path double-slashed, which some
            # gateways answer with a redirect and others with a 404.
            base_url=(self.payments_domestic_base_url or PROVIDER_API_ROOTS[provider]).rstrip("/"),
            webhook_secret=(
                self.payments_domestic_webhook_secret or self.payments_domestic_secret_key
            ),
            currencies=frozenset(
                part.strip().upper()
                for part in self.payments_domestic_currencies.split(",")
                if part.strip()
            ),
        )

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
    def console_docs_published(self) -> bool:
        """Whether the *console* API describes itself to anyone who asks.

        Off outside development (founder's decision, 2026-09-20). The document
        names every endpoint, field and shape the console uses, including the
        admin and billing routes — a map of the building, not a key to it, but a
        free reconnaissance step for anyone probing us, and nothing a customer
        needs. What a customer needs is the read surface, which is published
        separately and always (`readsurface/`, F36 FR20).
        """
        return self.is_development

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
