"""The secret patterns: a file holding one never enters a pack (FR13), and an added
line holding one is refused (K9).

**A net, not a boundary** (spec D23, D63): a secret in a format not listed here is sent.
A small pattern list, kept rather than a scanning dependency (D63): private key headers,
well-known token prefixes, and long opaque values assigned to names such as `password`.

The prefixes follow gitleaks' default rules (`config/gitleaks.toml`, read 2026-11-11),
loosened from exact lengths to "at least", because a net should err toward catching.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Final

# (name, pattern). The name is what a refusal or a left-out file says.
TOKEN_PATTERNS: Final = (
    ("private_key", re.compile(r"-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----")),
    ("aws_access_key", re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}")),
    ("github_fine_grained_token", re.compile(r"\bgithub_pat_\w{40,}")),
    ("gitlab_token", re.compile(r"\bglpat-[\w-]{20,}")),
    ("slack_token", re.compile(r"\bxox[abposre]-[0-9A-Za-z-]{10,}")),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_(?:test|live|prod)_[0-9A-Za-z]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[\w-]{35}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[\w-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj|svcacct|admin)-[\w-]{20,}")),
    ("npm_token", re.compile(r"\bnpm_[0-9A-Za-z]{36,}")),
    ("sendgrid_key", re.compile(r"\bSG\.[\w-]{16,}\.[\w-]{16,}")),
    ("json_web_token", re.compile(r"\beyJ[\w-]{10,}\.eyJ[\w-]{10,}\.[\w-]{10,}")),
)

# A value at least this long, with at least this much randomness, assigned to a name
# that says it is secret (guesses).
ASSIGNED_VALUE_MIN_LENGTH: Final = 12
ASSIGNED_VALUE_MIN_BITS_PER_CHARACTER: Final = 3.0
_SECRET_NAME: Final = (
    r"[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[\w.-]*"
)
ASSIGNED_SECRET: Final = re.compile(
    rf"(?i)\b{_SECRET_NAME}[\"']?\s*(?::=|=>|=|:)\s*[\"']"
    rf"([^\s'\"$<>{{}}]{{{ASSIGNED_VALUE_MIN_LENGTH},}})[\"']"
)
ASSIGNED_SECRET_NAME: Final = "assigned_secret"


def shannon_bits_per_character(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def secret_in(text: str) -> str | None:
    """The name of the first secret pattern `text` holds, or None. Never the value."""
    for name, pattern in TOKEN_PATTERNS:
        if pattern.search(text):
            return name
    for match in ASSIGNED_SECRET.finditer(text):
        if shannon_bits_per_character(match.group(1)) >= ASSIGNED_VALUE_MIN_BITS_PER_CHARACTER:
            return ASSIGNED_SECRET_NAME
    return None
