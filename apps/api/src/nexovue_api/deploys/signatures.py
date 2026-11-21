"""Proving a deploy notification came from the project it names.

The endpoint is public: anything on the internet can POST to it, and the only
thing separating a real deploy from a forged one is an HMAC over the exact bytes
that arrived. So everything here works on raw bytes and compares in constant
time.

GitHub's scheme is the one implemented, because it is the one every CI already
knows how to produce: ``X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw
body>``, hex digest, secret as the key. The legacy ``X-Hub-Signature`` header is
SHA-1 and is deliberately not accepted — GitHub keeps it only for old
integrations, and accepting it would mean accepting the weaker of the two.

Secrets live in `project_webhook_secrets`, one row each, so that a rotation
can overlap and revoking an ingest key does not silently stop deploys. They are
sealed at rest (F28 D7) and opened for each request, so this module only ever
sees them as plain values handed in by the service.

The replay defence is inside the signed body: each item carries its own
`delivery_id` and `deployed_at` (D6). A header id, such as GitHub's
``X-GitHub-Delivery``, is not signed, so it is not read.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Sequence
from typing import Final

SIGNATURE_HEADER: Final = "X-Hub-Signature-256"
SIGNATURE_ALGORITHM: Final = "sha256"

# Scheme prefix, so a leaked secret is recognisable as ours in a log or a paste.
SECRET_PREFIX: Final = "nvdw_"

# How many of a secret's last characters are kept in the clear, to tell one from
# another (`project_webhook_secrets.secret_hint`).
HINT_LENGTH: Final = 4

_HEX_DIGEST_LENGTH: Final = 64
_IS_HEX: Final = re.compile(r"[0-9a-fA-F]+")


def new_secret() -> str:
    """A fresh webhook secret.

    `token_urlsafe(32)` is 256 bits from the OS entropy source, which is what
    `secrets` exists to provide — an HMAC key is only as unguessable as this.
    The prefix makes one recognisable in a CI settings page, the way an ingest
    key is.
    """
    return f"{SECRET_PREFIX}{secrets.token_urlsafe(32)}"


def secret_hint(secret: str) -> str:
    """The last characters of a secret, the only part ever shown again."""
    return secret[-HINT_LENGTH:]


def matching_secret(*, body: bytes, header_value: str | None, secrets: Sequence[str]) -> int | None:
    """The position of the secret that signed these exact bytes, or None.

    Every secret is tried and none short-circuits: during a rotation two are live,
    a delivery signed with either must be accepted, and which one matched is not
    worth leaking in timing. The caller needs the position only to record which
    secret was used, after the answer is already decided.

    An absent header is a None, not an exception — an unsigned request and a
    wrongly signed one are refused by the caller in exactly the same way.
    """
    presented = _presented_digest(header_value)
    if presented is None:
        return None

    matched: int | None = None
    for position, secret in enumerate(secrets):
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        # compare_digest, never ==: an early-exiting comparison leaks the digest
        # one byte at a time to anyone willing to time the responses.
        if hmac.compare_digest(expected, presented) and matched is None:
            matched = position
    return matched


def _presented_digest(header_value: str | None) -> str | None:
    """The hex half of ``sha256=<hex>``, or None when the header is not that.

    A malformed header is rejected here rather than compared: it can never match
    a real digest, and letting it through only widens what is accepted.
    """
    if header_value is None:
        return None

    algorithm, separator, digest = header_value.strip().partition("=")
    if separator != "=" or algorithm.lower() != SIGNATURE_ALGORITHM:
        return None
    if len(digest) != _HEX_DIGEST_LENGTH or not _IS_HEX.fullmatch(digest):
        return None
    return digest.lower()
