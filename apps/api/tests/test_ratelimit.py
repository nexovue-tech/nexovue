"""Which tier a path falls into, and what the RateLimit headers say."""

from __future__ import annotations

import pytest

from nexovue_api.ratelimit.headers import (
    POLICY_HEADER,
    RETRY_AFTER_HEADER,
    STATE_HEADER,
    limit_headers,
    rejection_headers,
)
from nexovue_api.ratelimit.limiter import Decision
from nexovue_api.ratelimit.policy import (
    AUTH_TIER,
    DEFAULT_TIER,
    TELEMETRY_TIER,
    Quota,
    tier_for_path,
)
from nexovue_api.settings import API_PREFIX


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"{API_PREFIX}/auth/login", AUTH_TIER),
        (f"{API_PREFIX}/auth/signup", AUTH_TIER),
        (f"{API_PREFIX}/traces", TELEMETRY_TIER),
        (f"{API_PREFIX}/traces/abc123", TELEMETRY_TIER),
        (f"{API_PREFIX}/logs", TELEMETRY_TIER),
        (f"{API_PREFIX}/projects", DEFAULT_TIER),
        ("/healthz", None),
        ("/readyz", None),
    ],
)
def test_tier_for_path(path: str, expected: object) -> None:
    assert tier_for_path(path) is expected


def test_the_auth_tier_is_the_strictest() -> None:
    assert AUTH_TIER.quota.limit < TELEMETRY_TIER.quota.limit < DEFAULT_TIER.quota.limit


def test_only_the_clickhouse_tier_caps_concurrency() -> None:
    assert TELEMETRY_TIER.max_concurrent is not None
    assert AUTH_TIER.max_concurrent is None
    assert DEFAULT_TIER.max_concurrent is None


def test_a_plan_multiplier_widens_the_quota() -> None:
    widened = Quota(name="default", limit=300, window_seconds=60).scaled(2.0)

    assert widened.limit == 600
    assert widened.window_seconds == 60, "a plan raises the allowance, not the window"


def test_a_multiplier_can_never_produce_a_zero_allowance() -> None:
    assert Quota(name="auth", limit=10, window_seconds=60).scaled(0.0).limit >= 1


def test_headers_follow_the_two_field_draft() -> None:
    decision = Decision(
        allowed=True,
        quota=Quota(name="default", limit=300, window_seconds=60),
        remaining=299,
        reset_seconds=60,
    )

    headers = limit_headers(decision)

    assert headers[POLICY_HEADER] == '"default";q=300;w=60'
    assert headers[STATE_HEADER] == '"default";r=299;t=60'


def test_a_rejection_always_gives_a_retry_after_of_at_least_one_second() -> None:
    decision = Decision(
        allowed=False,
        quota=Quota(name="auth", limit=10, window_seconds=60),
        remaining=0,
        reset_seconds=0,
    )

    assert rejection_headers(decision)[RETRY_AFTER_HEADER] == "1"
