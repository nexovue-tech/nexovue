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
TELEMETRY_TIER: Final = Tier(
    quota=Quota(name="telemetry", limit=60, window_seconds=60),
    max_concurrent=4,
)
DEFAULT_TIER: Final = Tier(quota=Quota(name="default", limit=300, window_seconds=60))

# Ordered longest-prefix-first so a more specific rule cannot be shadowed.
AUTH_PATHS: Final = (
    f"{API_PREFIX}/auth/login",
    # Signup is a sibling of /auth, not a child: it creates the organization the
    # session then belongs to. It is still credential-stuffing surface.
    f"{API_PREFIX}/signup",
    f"{API_PREFIX}/auth/password-reset",
)
TELEMETRY_PATHS: Final = (
    f"{API_PREFIX}/traces",
    f"{API_PREFIX}/logs",
    f"{API_PREFIX}/metrics",
    f"{API_PREFIX}/errors",
    f"{API_PREFIX}/services",
    f"{API_PREFIX}/service-map",
    f"{API_PREFIX}/infra",
    f"{API_PREFIX}/synthetics",
    f"{API_PREFIX}/database",
    f"{API_PREFIX}/rum",
    f"{API_PREFIX}/usage",
)

# Probes must answer while the API is being hammered; that is the whole point
# of them. They are also what an orchestrator uses to decide to restart us.
EXEMPT_PATHS: Final = ("/healthz", "/readyz")


def tier_for_path(path: str) -> Tier | None:
    """The tier a request path falls into, or None when it is exempt."""
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
