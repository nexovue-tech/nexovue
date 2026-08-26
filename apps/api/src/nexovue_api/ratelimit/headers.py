"""The RateLimit header fields, as defined by draft-ietf-httpapi-ratelimit-headers-11.

The draft that reached working-group last call collapses the three widely
deployed ``RateLimit-Limit`` / ``-Remaining`` / ``-Reset`` fields into two
structured fields: ``RateLimit-Policy`` advertises the quota, ``RateLimit``
reports what is left of it. We emit the current shape; it is still an
Internet-Draft, not an RFC, so this module is the one place to change if it
moves again.
"""

from __future__ import annotations

from typing import Final

from nexovue_api.ratelimit.limiter import Decision

POLICY_HEADER: Final = "RateLimit-Policy"
STATE_HEADER: Final = "RateLimit"
RETRY_AFTER_HEADER: Final = "Retry-After"


def limit_headers(decision: Decision) -> dict[str, str]:
    """Advertise the quota and what remains of it."""
    quota = decision.quota
    return {
        POLICY_HEADER: f'"{quota.name}";q={quota.limit};w={quota.window_seconds}',
        STATE_HEADER: f'"{quota.name}";r={decision.remaining};t={decision.reset_seconds}',
    }


def rejection_headers(decision: Decision) -> dict[str, str]:
    """Headers for a 429. Retry-After takes precedence over the effective window."""
    headers = limit_headers(decision)
    headers[RETRY_AFTER_HEADER] = str(decision.retry_after_seconds)
    return headers
