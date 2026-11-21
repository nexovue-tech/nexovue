"""Charging the scans a request really runs, in the buckets the middleware counted it in.

The limiter counts HTTP requests (`middleware/ratelimit.py`), which was true to cost
only while one request meant about one scan. A route that runs many scans in one
request, the dashboard batch (F20), would otherwise walk around every limit. So the
middleware leaves two things in `scope["state"]`: the buckets it counted the request
in, and when the request arrived. A route then charges each further scan to the same
buckets, by the same keys, through `ScanCharge` (design section 6, ADR-031).

**`counted` absent means limits are off.** The off switch is the middleware's own
`enabled`, and the lifespan builds a limiter regardless, so without this a batch would
refuse panels on an install that turned limits off (AC60).

**No slots for later scans** (D43). The request keeps the middleware's one slot; what
runs at once is held by the batch's permits and the process caps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from starlette.types import Scope

from nexovue_api.ratelimit.limiter import RateLimiter
from nexovue_api.ratelimit.policy import Quota

# Keys in `scope["state"]`, beside the correlation id (`middleware/correlation.py`).
COUNTED_STATE: Final = "rate_limit_counted"
ARRIVED_AT_STATE: Final = "arrived_at_monotonic"

Identity = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Counted:
    """One bucket a request is counted in: whose, how many, and how many at once.

    No quota is a bucket that only caps what is in flight.
    """

    identity: Identity
    quota: Quota | None
    max_concurrent: int | None
    # The tier the bucket belongs to. In-flight slots are counted per tier, so an
    # organization's tokens and its people do not share one ceiling (group 9, D5).
    pool: str


@dataclass(frozen=True, slots=True)
class Refused:
    """A scan the quota would not pay for, and how long until it would."""

    policy: str
    retry_after_seconds: int


class ScanCharge:
    """Pays for one request's scans: the first rides on the request's own place."""

    def __init__(self, limiter: RateLimiter | None, counted: list[Counted] | None) -> None:
        self._limiter = limiter
        self._counted = counted
        self._rider_spent = False

    async def claim(self) -> Refused | None:
        """Pay for one scan, or say why not. A refusal stops only that scan."""
        if self._counted is None or self._limiter is None:
            return None
        if not self._rider_spent:
            self._rider_spent = True
            return None
        # Person first, then organization, as the middleware's own spend runs.
        for bucket in self._counted:
            if bucket.quota is None:
                continue
            scope, identity = bucket.identity
            decision = await self._limiter.consume(
                scope=scope, identity=identity, quota=bucket.quota
            )
            if not decision.allowed:
                return Refused(
                    policy=decision.quota.name, retry_after_seconds=decision.retry_after_seconds
                )
        return None


def record_arrival(scope: Scope, arrived_at: float) -> None:
    scope.setdefault("state", {})[ARRIVED_AT_STATE] = arrived_at


def record_counted(scope: Scope, counted: list[Counted]) -> None:
    scope.setdefault("state", {})[COUNTED_STATE] = counted


def arrival_of(scope: Scope) -> float | None:
    """When the request reached the API, on the monotonic clock, before any wait."""
    arrived: float | None = scope.get("state", {}).get(ARRIVED_AT_STATE)
    return arrived


def scan_charge_for(scope: Scope, limiter: RateLimiter | None) -> ScanCharge:
    """The charge for a request, keyed exactly as the middleware keyed it."""
    counted: list[Counted] | None = scope.get("state", {}).get(COUNTED_STATE)
    return ScanCharge(limiter, counted)
