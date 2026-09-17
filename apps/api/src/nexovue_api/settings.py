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

from pydantic import (
    AnyUrl,
    Field,
    PostgresDsn,
    RedisDsn,
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

# Which implementation of a port is used. The vendor's name is the *value* here,
# never the key — `EMAIL_API_KEY` stays neutral so it survives the switch, while
# `EMAIL_PROVIDER` is the thing meant to change. A Literal rather than a free
# string so an unknown value fails when settings are read, at start-up, rather
# than at the first send in front of somebody waiting for a password reset.
#
# Payments are expected to end up as two of these — a domestic gateway and a
# merchant of record, because a non-EU seller owes EU VAT from the first euro —
# which is why the payment one is named for the role it fills.
#
# `console` is not a vendor: it renders the message and writes it to the log.
# It is what development runs, so a signup in a test does not spend a real
# message — see `notifications/console.py`.
EmailProvider = Literal["resend", "console"]
DomesticPaymentProvider = Literal["razorpay"]

# Who logged mail claims to be from when nothing configured a sender. A
# domain that cannot resolve, so a message that escaped to a real provider
# would be refused rather than delivered from a plausible-looking address.
CONSOLE_FROM_ADDRESS: Final = "nexovue@console.invalid"


# A processor runs two estates with two sets of credentials and two hostnames.
# Named rather than a bool because "is production" is a question about the
# *account*, not about our own deployment: a staging install may legitimately
# hold live keys, and a production install may not yet have any.
PaymentEnvironment = Literal["sandbox", "production"]

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
class EmailSettings:
    """A complete email configuration, which is all four values or none.

    Same shape and same reason as `ObjectStoreSettings`: a half-filled
    configuration fails at the first send, and a provider's "domain not
    verified" tells a reader nothing about which variable was missing.

    No provider is named. A vendor's vocabulary stays out of the port,
    and a vendor's name in a settings field is that leak in another file — the
    key's own prefix already identifies which one it is.
    """

    provider: EmailProvider
    api_key: str
    # The domain whose SPF, DKIM and DMARC were verified. `from_address` must be
    # at it; a provider refuses anything else, which is the point of both fields
    # existing rather than one.
    sending_domain: str
    from_address: str
    # A real inbox, deliberately not on the sending domain. Without it a
    # customer who replies to a `noreply` sender is talking to nobody.
    reply_to: str


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
    environment: PaymentEnvironment
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


# Each provider's API root, per environment.
#
# **Derived rather than configured, because it was got wrong the first time it
# mattered**: the provider was switched to `razorpay` while `base_url` still held
# the other gateway's sandbox host, which authenticates as a bad key rather than
# as a wrong address. A value that can only be derived cannot be stale.
#
# Still overridable by `PAYMENTS_DOMESTIC_BASE_URL` — a test transport and a
# vendor's regional host both need it — but nothing has to set it to be right.
PROVIDER_API_ROOTS: Final[dict[str, dict[str, str]]] = {
    # One host for both, and the key's own prefix decides which books it writes
    # to: `rzp_test_` is the sandbox, `rzp_live_` is not.
    # https://razorpay.com/docs/api/
    "razorpay": {
        "sandbox": "https://api.razorpay.com/v1",
        "production": "https://api.razorpay.com/v1",
    },
}


# The environment variables an unconfigured email sender tells the caller to
# set. Spelled once so the message and the fields cannot drift apart.
EMAIL_VARIABLES: Final = (
    "EMAIL_PROVIDER",
    "EMAIL_API_KEY",
    "EMAIL_SENDING_DOMAIN",
    "EMAIL_FROM_ADDRESS",
    "EMAIL_REPLY_TO",
)


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

    # --- synthetic checks ----------------------------------------------------
    # Seals check secrets (ADR-010). Its own key rather than the TOTP one, so a
    # rotation of either leaves the other alone; the scheduler holds the same
    # value to open what a request uses. Optional at boot for the same reason as
    # the key above: the routes that need it refuse with 503 instead.
    check_secret_key: str = ""

    # The probe locations a check can run from. The hosted service lists its
    # regions; a self-hosted install has one. Each is a scheduler worker
    # consuming `light.<location>`.
    probe_locations: Annotated[list[str], NoDecode, Field(min_length=1)] = ["local"]

    # The scheduler's broker, which Run now dispatches through (ADR-011). Unset
    # means Run now answers 503 rather than dispatching nowhere.
    probe_broker_url: RedisDsn | None = None

    # Where a customer points their OTLP exporter. Signup hands this back, so it
    # has to name the receiver as the *customer* reaches it, not as we reach it.
    otlp_endpoint: str = DEFAULT_OTLP_ENDPOINT

    rate_limit_enabled: bool = True
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
    payments_domestic_env: PaymentEnvironment = "sandbox"
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

    # A verification link is followed at leisure; a reset link is a live
    # credential to somebody's account. Different lifetimes, and the difference
    # is the reason they are two settings rather than one.
    verify_email_ttl_seconds: int = 24 * 60 * 60
    reset_password_ttl_seconds: int = 60 * 60

    @property
    def email(self) -> EmailSettings | None:
        """The email settings, or None when mail is not configured.

        None rather than an error, because an API that cannot send mail still
        serves every screen. The caller that needs to send is the one that must
        refuse, and it can name `EMAIL_VARIABLES` when it does.

        **`console` is configured by naming it, and needs nothing else.** The
        all-or-none rule exists because a half-filled configuration fails at the
        first send with a provider error that names no variable. There is no
        provider to refuse here and no credential to be valid, so the rule has
        nothing to protect — and applying it anyway would mean a clone with no
        `.env` gets no mail at all, when reading the verification link out of the
        log is exactly what development needs.
        """
        if self.email_provider == "console":
            return EmailSettings(
                provider=self.email_provider,
                api_key="",
                sending_domain=self.email_sending_domain,
                from_address=self.email_from_address or CONSOLE_FROM_ADDRESS,
                reply_to=self.email_reply_to,
            )
        parts = (
            self.email_api_key,
            self.email_sending_domain,
            self.email_from_address,
            self.email_reply_to,
        )
        if not all(parts):
            return None
        return EmailSettings(
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
            environment=self.payments_domestic_env,
            # A trailing slash would make every path double-slashed, which some
            # gateways answer with a redirect and others with a 404.
            base_url=(
                self.payments_domestic_base_url
                or PROVIDER_API_ROOTS[provider][self.payments_domestic_env]
            ).rstrip("/"),
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
