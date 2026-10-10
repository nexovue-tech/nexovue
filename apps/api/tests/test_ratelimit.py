"""Which tier a path falls into, and what the RateLimit headers say."""

from __future__ import annotations

import pytest

from nexovue_api.apitokens.tokens import API_TOKEN_PREFIX
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
    TOKEN_TIER,
    Quota,
    tier_for,
    tier_for_path,
)
from nexovue_api.settings import API_PREFIX


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"{API_PREFIX}/auth/login", AUTH_TIER),
        (f"{API_PREFIX}/signup", AUTH_TIER),
        (f"{API_PREFIX}/auth/password-reset", AUTH_TIER),
        (f"{API_PREFIX}/auth/password-reset/confirm", AUTH_TIER),
        (f"{API_PREFIX}/auth/verify-email", AUTH_TIER),
        # Every route that checks a credential belongs on the strict tier, and
        # this one does not look like it does: changing a password verifies the
        # current one, so on the default tier it is 300 guesses a minute for
        # whoever holds a stolen session.
        (f"{API_PREFIX}/profile/password", AUTH_TIER),
        # …and the rest of the profile is an ordinary authenticated write.
        (f"{API_PREFIX}/profile", DEFAULT_TIER),
        (f"{API_PREFIX}/profile/sign-out-everywhere", DEFAULT_TIER),
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


A_TOKEN = f"{API_TOKEN_PREFIX}CIreadsCIreadsCIreadsCIreadsCIre"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # A token pays its own allowance wherever it is pointed (F36, FR19): the
        # path says what is read, the credential says whose budget pays.
        (f"{API_PREFIX}/traces", TOKEN_TIER),
        (f"{API_PREFIX}/checks", TOKEN_TIER),
        (f"{API_PREFIX}/alerts/incidents", TOKEN_TIER),
        # An orchestrator's probe carries no credential, and a token pointed at
        # one learns nothing — so exempt stays exempt.
        ("/healthz", None),
    ],
)
def test_a_token_is_counted_as_a_token_whatever_it_reads(path: str, expected: object) -> None:
    assert tier_for(path, presented=A_TOKEN) is expected


@pytest.mark.parametrize(
    ("presented", "expected"),
    [
        (None, TELEMETRY_TIER),
        ("a-session-token", TELEMETRY_TIER),
        ("nvk_live_devlocaldevlocaldevloc", TELEMETRY_TIER),
    ],
    ids=["signed out", "session", "ingest key"],
)
def test_every_other_credential_falls_on_the_path_tier(
    presented: str | None, expected: object
) -> None:
    """Only an API token changes the tier. An ingest key is refused elsewhere,
    and must not buy a machine's allowance on its way to that refusal."""
    assert tier_for(f"{API_PREFIX}/traces", presented=presented) is expected


def test_a_token_reads_more_than_a_person_and_still_caps_concurrency() -> None:
    """FR19: a nightly pull is not six screens, but a scan is still a scan."""
    assert TOKEN_TIER.quota.limit > TELEMETRY_TIER.quota.limit
    assert TOKEN_TIER.max_concurrent == TELEMETRY_TIER.max_concurrent
    assert TOKEN_TIER.org_quota is None
    # An organization's tokens together are held to the telemetry tier's ceiling.
    assert TOKEN_TIER.org_max_concurrent == TELEMETRY_TIER.org_max_concurrent


def test_the_auth_tier_is_the_strictest() -> None:
    assert AUTH_TIER.quota.limit < TELEMETRY_TIER.quota.limit < DEFAULT_TIER.quota.limit


def test_only_the_auth_tier_is_counted_exactly() -> None:
    """The one tier where a guess let through costs an account (ADR-020)."""
    assert AUTH_TIER.quota.exact
    assert not any(t.quota.exact for t in (TELEMETRY_TIER, DEFAULT_TIER, TOKEN_TIER))


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
