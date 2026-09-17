"""What the limits are, and which requests they apply to.

Three tiers, because three kinds of request cost wildly different things:

* **auth** — login and signup are the credential-stuffing surface. Counted per
  IP *and* per email, so neither rotating addresses nor rotating accounts gets
  a free run at one target.
* **telemetry** — every ClickHouse-backed screen. A low request rate plus a hard
  cap on how many can be in flight at once: a columnar scan costs orders of
  magnitude more than a Postgres row read, and ten concurrent ones will hurt the
  cluster long before a per-minute counter notices.
* **default** — everything else on the control plane.

These are product policy, not deployment configuration, so they live in code.
A plan may raise them; see :class:`PlanLimits`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

from nexovue_api.settings import API_PREFIX


@dataclass(frozen=True, slots=True)
class Quota:
    """One counted allowance: ``limit`` requests per ``window_seconds``."""

    name: str
    limit: int
    window_seconds: int

    def scaled(self, multiplier: float) -> Quota:
        """The same quota widened for a paying plan."""
        return Quota(
            name=self.name,
            limit=max(1, int(self.limit * multiplier)),
            window_seconds=self.window_seconds,
        )


@dataclass(frozen=True, slots=True)
class Tier:
    """The quota a class of request is held to, and its concurrency cap."""

    quota: Quota
    max_concurrent: int | None = None


AUTH_TIER: Final = Tier(quota=Quota(name="auth", limit=10, window_seconds=60))

# The most telemetry reads one screen asks for at once. Service detail wants the
# service, its trend, its endpoints, its dependencies, its errors and its duration
# heatmap; Overview wanted five before it was made to wait for itself.
#
# This is not a limit. It is the measurement the limit below has to clear, and it
# is stated here so the next screen that grows a read fails a test rather than a
# customer.
LARGEST_SCREEN_FANOUT: Final = 6

TELEMETRY_TIER: Final = Tier(
    quota=Quota(name="telemetry", limit=60, window_seconds=60),
    # **Above one screen's fan-out, and still below the number this tier exists
    # to prevent.** At 4 it sat *under* legitimate use: opening one service
    # refused one of its five reads, and the screen rendered with its errors
    # panel missing. A cap below what the product's own screens need is not
    # protection — it is a defect that looks like protection, and it had already
    # been worked around in one screen by making its reads wait for each other.
    #
    # The reasoning above says ten concurrent scans hurt the cluster. Eight keeps
    # that ceiling, leaves a screen room to load at once, and leaves the 60/minute
    # quota to bound sustained load, which is the thing a counter is good at.
    #
    # **Refusing is still the wrong answer for a legitimate burst**; a bounded
    # wait would make a sixth read slow rather than absent. That is a larger
    # change than a number and is recorded rather than assumed.
    max_concurrent=8,
)
DEFAULT_TIER: Final = Tier(quota=Quota(name="default", limit=300, window_seconds=60))

# The one unauthenticated write in the control plane. It shared the default tier,
# which meant anything on the internet could consume the quota signed-in users
# need. Its own, tighter allowance: a deploy is an occasional event, and no CI
# system pushes sixty a minute.
WEBHOOK_PREFIX: Final = "/api/v1/deploys/webhook/"
WEBHOOK_TIER: Final = Tier(quota=Quota(name="deploy-webhook", limit=60, window_seconds=60))

# F18. Each of these makes our infrastructure do work somebody else chose, so each
# has its own allowance rather than a share of the one signed-in screens need.
#
# Run now is an outbound request to an address the caller picked; the result read
# shares the prefix and waits server-side, so one run costs one or two requests.
RUN_NOW_PREFIX: Final = "/api/v1/check-runs"
RUN_NOW_TIER: Final = Tier(quota=Quota(name="check-run-now", limit=30, window_seconds=60))
# Parsing an OpenAPI description or a Postman collection is CPU the caller chose.
CHECK_IMPORT_PREFIX: Final = "/api/v1/check-imports"
CHECK_IMPORT_TIER: Final = Tier(quota=Quota(name="check-import", limit=20, window_seconds=60))
# Up to three megabytes into object storage per request.
CHECK_FILE_PREFIX: Final = "/api/v1/check-files"
CHECK_FILE_TIER: Final = Tier(quota=Quota(name="check-file", limit=30, window_seconds=60))
# Unauthenticated, like the deploy webhook: anything on the internet can reach it,
# and a job that calls more than once a second is not a heartbeat.
HEARTBEAT_PREFIX: Final = "/api/v1/heartbeat/"
HEARTBEAT_TIER: Final = Tier(quota=Quota(name="heartbeat", limit=120, window_seconds=60))

_OWN_TIERS: Final = (
    (RUN_NOW_PREFIX, RUN_NOW_TIER),
    (CHECK_IMPORT_PREFIX, CHECK_IMPORT_TIER),
    (CHECK_FILE_PREFIX, CHECK_FILE_TIER),
    (HEARTBEAT_PREFIX, HEARTBEAT_TIER),
)

# Ordered longest-prefix-first so a more specific rule cannot be shadowed.
AUTH_PATHS: Final = (
    f"{API_PREFIX}/auth/login",
    # Signup is a sibling of /auth, not a child: it creates the organization the
    # session then belongs to. It is still credential-stuffing surface.
    f"{API_PREFIX}/signup",
    # Account recovery, and it is listed path by path rather than as an
    # `/auth/` prefix because `/auth/me` is polled by every signed-in screen and
    # would be throttled at ten a minute. The reset request is the one that
    # matters most: unthrottled it is a way to have us mail anybody repeatedly,
    # and the per-email scope is what bounds that rather than the per-IP one.
    f"{API_PREFIX}/auth/password-reset",
    f"{API_PREFIX}/auth/verify-email",
    # Changing a password verifies the CURRENT one, which makes it a credential
    # check wearing a profile route's clothes. On the default tier that is 300
    # guesses a minute against exactly the attacker the endpoint exists to stop —
    # somebody holding a stolen session and trying to take the account. Only the
    # per-IP scope applies here, because the body carries no email for
    # `_email_from` to find.
    f"{API_PREFIX}/profile/password",
    # Same reasoning, and the stakes are higher: asking to move an account to a
    # new address verifies the CURRENT password, and whoever ends up holding the
    # address can reset the password afterwards. On the default tier that is 300
    # guesses a minute at the gate protecting an account takeover. It is also
    # the one authenticated endpoint that mails an address the caller chose, so
    # an unthrottled one is a way to send from our verified domain in bulk.
    f"{API_PREFIX}/profile/email",
    # Spends the link that address change mailed. Token guessing, throttled the
    # way `/auth/verify-email` is.
    f"{API_PREFIX}/auth/email-change",
    # The second half of a sign-in (T64). A TOTP code is a six-digit credential
    # — a million wide, which sounds large and is not: on the default tier, 300
    # guesses a minute walks a meaningful fraction of that space in a day
    # against an account whose password is already known. The challenge's own
    # five-attempt budget bounds one password submission; this bounds the rate
    # at which submissions can be made. Only the per-IP scope applies, because
    # the body carries no email for `_email_from` to find.
    f"{API_PREFIX}/auth/login/second-factor",
    # Arming and disarming the second factor both verify the CURRENT password,
    # which makes them credential checks wearing a profile route's clothes —
    # the same reading `/profile/password` gets, and the reasoning above it
    # applies here unchanged. `/profile/two-factor/confirm` is on the prefix
    # too, and belongs there for its own reason: it checks a code against a
    # secret nobody has confirmed yet, which is a credential check as well.
    #
    # The prefix also catches `GET /profile/two-factor`, which reads a state and
    # checks nothing. That is accepted rather than worked around: it is read once
    # when the profile screen opens, not polled the way `/auth/me` is — which is
    # the reason `/auth/` is not listed here as a prefix. If a screen ever starts
    # polling it, it needs a path of its own rather than a wider rule.
    f"{API_PREFIX}/profile/two-factor",
)
# **These must be the routers' real prefixes, and two of them were not.**
# `/errors` and `/database` matched nothing: the routers are mounted at
# `/error-groups` and `/dbstats`, so both read ClickHouse on the default tier —
# 300 a minute with no concurrency cap at all — while the entries meant to
# protect them sat here looking correct. Nothing failed, because a rule that
# matches no path raises no error.
#
# `test_ratelimit_covers_every_clickhouse_router` compares this list against the
# routers themselves, so the next rename is caught rather than discovered.
TELEMETRY_PATHS: Final = (
    f"{API_PREFIX}/traces",
    f"{API_PREFIX}/logs",
    f"{API_PREFIX}/metrics",
    f"{API_PREFIX}/error-groups",
    f"{API_PREFIX}/services",
    f"{API_PREFIX}/service-map",
    f"{API_PREFIX}/infrastructure",
    f"{API_PREFIX}/synthetics",
    f"{API_PREFIX}/dbstats",
    f"{API_PREFIX}/rum",
    f"{API_PREFIX}/usage",
)

# Probes must answer while the API is being hammered; that is the whole point
# of them. They are also what an orchestrator uses to decide to restart us.
EXEMPT_PATHS: Final = ("/healthz", "/readyz")


def tier_for_path(path: str) -> Tier | None:
    """The tier a request path falls into, or None when it is exempt."""
    if path.startswith(WEBHOOK_PREFIX):
        return WEBHOOK_TIER
    for prefix, tier in _OWN_TIERS:
        if path.startswith(prefix):
            return tier
    if path in EXEMPT_PATHS:
        return None
    if path.startswith(AUTH_PATHS):
        return AUTH_TIER
    if path.startswith(TELEMETRY_PATHS):
        return TELEMETRY_TIER
    return DEFAULT_TIER


class PlanLimits(Protocol):
    """How much a paying plan widens the base quotas.

    This is the seam the off-the-shelf limiters do not have: the multiplier
    comes from the organization's plan in Postgres, not from a decorator
    argument fixed at import time.
    """

    async def multiplier_for(self, org_id: str | None) -> float:
        """The factor applied to a tier's base quota for this organization."""


class FixedPlanLimits:
    """Every organization on the base quota.

    The Postgres-backed implementation needs the ``organizations.plan`` column
    from P1-59; this is what the API runs on until that lands.
    """

    async def multiplier_for(self, org_id: str | None) -> float:
        return 1.0
