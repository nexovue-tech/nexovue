"""Minting an ingest key.

SHA-256 and not argon2, deliberately: the receiver verifies this on every
request, and a stretched hash there would cost more than the ingest it guards.
A 22-character random secret has ~131 bits of entropy, so there is nothing for
stretching to protect — the key is generated, not chosen by a person.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from typing import Final

# `nvk` for nexovue key, `live` because a `test` tier will want the same shape.
API_KEY_PREFIX: Final = "nvk_live_"
API_KEY_SECRET_LENGTH: Final = 22
API_KEY_ALPHABET: Final = string.ascii_letters + string.digits


def generate_api_key() -> str:
    """A fresh plaintext key. Shown once and never stored in the clear."""
    secret = "".join(secrets.choice(API_KEY_ALPHABET) for _ in range(API_KEY_SECRET_LENGTH))
    return f"{API_KEY_PREFIX}{secret}"


def hash_api_key(key: str) -> str:
    """SHA-256, never argon2 — the receiver verifies this on every request."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
