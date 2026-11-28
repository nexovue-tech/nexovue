"""The secret patterns (F5 plan T2.4; spec FR13, K9, D23).

**Every value here is a test-only fake, made up for this file.** Each is assembled from
a prefix and a made-up body at run time, so the source holds no secret-shaped string
for a scanner to report; if one is reported anyway, its fingerprint is for review
under `.gitleaksignore`, as `status.md` lists.
"""

from __future__ import annotations

import pytest
from nexovue_platform.fixes.secrets import ASSIGNED_SECRET_NAME, secret_in

# A made-up body, mixed enough to read as random, never a real credential.
BODY = "Qz7Wn2Lx9Pv4Rk8Ts3Ym6Hb1Jc5Dg0Fa"


def fake(prefix: str, length: int, alphabet: str = BODY) -> str:
    return prefix + (alphabet * 4)[:length]


FAKES = [
    ("private_key", "-----BEGIN " + "OPENSSH PRIVATE KEY-----"),
    ("private_key", "-----BEGIN " + "PRIVATE KEY-----"),
    ("aws_access_key", fake("AK" + "IA", 16, "QZ2T3B4C5D6E7F6A")),
    ("github_token", fake("gh" + "p_", 36)),
    ("github_token", fake("gh" + "s_", 36)),
    ("github_fine_grained_token", fake("github" + "_pat_", 82)),
    ("gitlab_token", fake("gl" + "pat-", 20)),
    ("slack_token", fake("xo" + "xb-", 40)),
    ("stripe_key", fake("sk" + "_live_", 24)),
    ("google_api_key", fake("AI" + "za", 35)),
    ("anthropic_key", fake("sk-" + "ant-api03-", 40)),
    ("openai_key", fake("sk-" + "proj-", 48)),
    ("npm_token", fake("np" + "m_", 36)),
    ("sendgrid_key", "SG" + "." + fake("", 22) + "." + fake("", 43)),
    ("json_web_token", "ey" + "J" + fake("", 20) + ".ey" + "J" + fake("", 20) + "." + fake("", 20)),
]


@pytest.mark.parametrize(("name", "value"), FAKES, ids=[name for name, _ in FAKES])
def test_each_pattern_finds_its_fake(name: str, value: str) -> None:
    assert secret_in(f'TOKEN_VALUE = "{value}"') == name


@pytest.mark.parametrize(
    "line",
    [
        'db_password = "' + fake("", 20) + '"',
        "GITHUB_TOKEN: '" + fake("", 24) + "'",
        '"apiKey": "' + fake("", 16) + '"',
        'client_secret := "' + fake("", 30) + '"',
    ],
)
def test_a_long_opaque_value_assigned_to_a_secret_name(line: str) -> None:
    assert secret_in(line) == ASSIGNED_SECRET_NAME


@pytest.mark.parametrize(
    "line",
    [
        'password = os.environ["DB_PASSWORD"]',
        'token = "changeme"',
        'password = "${DB_PASSWORD}"',
        'secret_name = "aaaaaaaaaaaaaaaaaaaa"',
        'AWS_REGION = "eu-west-1"',
        "skip_tests = True",
        'label = "sk-learn is a library"',
        "-----BEGIN CERTIFICATE-----",
        "AKIAEXAMPLE",
    ],
)
def test_near_misses(line: str) -> None:
    assert secret_in(line) is None


def test_a_secret_is_named_never_echoed() -> None:
    value = fake("gh" + "p_", 36)

    assert value not in str(secret_in(value))
