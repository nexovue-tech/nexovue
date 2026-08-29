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

The secret itself is *derived*, never stored: see :func:`derive_secret`.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from collections.abc import Sequence
from typing import Final

SIGNATURE_HEADER: Final = "X-Hub-Signature-256"
SIGNATURE_ALGORITHM: Final = "sha256"

# Names this derivation, so a future scheme can be introduced beside it rather
# than silently changing every customer's secret under them.
DERIVATION_LABEL: Final = "nexovue.deploy.webhook.v1"

# Scheme prefix, so a leaked secret is recognisable as ours in a log or a paste.
SECRET_PREFIX: Final = "nvdw_"

_HEX_DIGEST_LENGTH: Final = 64
_IS_HEX: Final = re.compile(r"[0-9a-fA-F]+")


def derive_secret(*, key_hash: str, project_id: uuid.UUID) -> str:
    """The secret a customer configures in GitHub, for one project.

    Derived rather than stored because no column holds one yet. Keying the
    derivation on an ingest key's hash gives the two properties that matter: a
    project's secret cannot be computed from another project's, and it cannot be
    computed at all without a row from ``api_keys``.
    """
    digest = hmac.new(
        key_hash.encode("utf-8"),
        f"{DERIVATION_LABEL}:{project_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{SECRET_PREFIX}{digest}"


def signature_matches(*, body: bytes, header_value: str | None, secrets: Sequence[str]) -> bool:
    """Whether these exact bytes were signed by one of these secrets.

    Every secret is tried and none short-circuits: a project may hold several
    live ingest keys, so rotating one must not break a webhook still signed with
    the other's secret, and which one matched is not worth leaking in timing.

    An absent header is a false, not an exception — an unsigned request and a
    wrongly signed one are refused by the caller in exactly the same way.
    """
    presented = _presented_digest(header_value)
    if presented is None:
        return False

    matched = False
    for secret in secrets:
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        # compare_digest, never ==: an early-exiting comparison leaks the digest
        # one byte at a time to anyone willing to time the responses.
        matched |= hmac.compare_digest(expected, presented)
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
