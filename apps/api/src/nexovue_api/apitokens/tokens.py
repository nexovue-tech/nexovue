"""Minting an API token.

SHA-256 and not argon2, for the reason `signup/apikeys.py` gives about ingest
keys: this is verified on every request, and a 32-character random secret has
~190 bits of entropy, so there is nothing for stretching to protect.

The prefix is its own thing. `nvt_` is not `nvk_` (ingest) or `nvp_` (browser),
so a credential says what it is before anything looks it up — which is what lets
the API refuse an ingest key in one sentence (BR8), and what a secret scanner
matches on if a token is ever pushed to a repository.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from typing import Final

API_TOKEN_PREFIX: Final = "nvt_live_"
API_TOKEN_SECRET_LENGTH: Final = 32
API_TOKEN_ALPHABET: Final = string.ascii_letters + string.digits


def generate_api_token() -> str:
    """A fresh plaintext token. Shown once and never stored in the clear."""
    secret = "".join(secrets.choice(API_TOKEN_ALPHABET) for _ in range(API_TOKEN_SECRET_LENGTH))
    return f"{API_TOKEN_PREFIX}{secret}"


def hash_api_token(token: str) -> str:
    """SHA-256, never argon2 — this is verified on every request."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# What a caller is told when they present the wrong kind of credential (BR8).
# Here, beside the schemes, because both the session dependency and the reader
# dependency answer with them and neither may import the other.
TOKENS_ARE_READ_ONLY: Final = "API tokens are read-only and reach only the documented read API."
INGEST_KEYS_CANNOT_READ: Final = (
    "That is an ingest key. Ingest keys write telemetry and read nothing; "
    "create an API token in Settings → API tokens to read."
)
# `nvk_` is the server-side ingest key, `nvp_` the browser one.
INGEST_KEY_SCHEMES: Final = ("nvk_", "nvp_")


def looks_like_api_token(candidate: str) -> bool:
    """Whether a bearer value is claiming to be an API token.

    A claim, not proof: it decides which lookup runs, so a session token is never
    hashed against `api_tokens` and a token is never looked up in Redis.
    """
    return candidate.startswith(API_TOKEN_PREFIX)
