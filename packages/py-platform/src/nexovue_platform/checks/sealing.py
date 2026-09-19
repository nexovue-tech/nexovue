"""AES-256-GCM sealing, bound to what a value belongs to.

Shared because two processes hold the check-secret key: the API seals a value
when a customer saves it, the scheduler opens it immediately before a request
uses it. Nothing else crosses the queue but ciphertext (ADR-010).

**Associated data carries a purpose and an owner**, so a sealed value copied into
another row — another project, another name, another kind of credential — fails
to open rather than decrypting as somebody else's secret.
"""

from __future__ import annotations

import base64
import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12

# Stored ahead of the nonce so a future construction can be told apart from this
# one without guessing at lengths.
FORMAT_VERSION: Final = 1


class SealError(ValueError):
    """A sealed value could not be opened: wrong key, wrong owner, or tampered."""


class SealedBox:
    """Seals and opens values for one purpose under one key."""

    def __init__(self, key: bytes, *, purpose: str) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"the key must be {KEY_BYTES} bytes")
        if not purpose:
            raise ValueError("a purpose is required")
        self._cipher = AESGCM(key)
        self._purpose = purpose

    def seal(self, plaintext: str, *, owner: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._cipher.encrypt(nonce, plaintext.encode(), self._associated_data(owner))
        return bytes([FORMAT_VERSION]) + nonce + sealed

    def open(self, sealed: bytes, *, owner: str) -> str:
        if len(sealed) <= 1 + NONCE_BYTES or sealed[0] != FORMAT_VERSION:
            raise SealError("not a sealed value this build can read")
        nonce = sealed[1 : 1 + NONCE_BYTES]
        try:
            opened = self._cipher.decrypt(
                nonce, sealed[1 + NONCE_BYTES :], self._associated_data(owner)
            )
        except InvalidTag as tampered:
            raise SealError("the sealed value could not be opened") from tampered
        return opened.decode()

    def _associated_data(self, owner: str) -> bytes:
        return f"{self._purpose}:{owner}".encode()


def decode_key(configured: str) -> bytes:
    """A key as the environment carries it: URL-safe base64 of 32 bytes."""
    try:
        key = base64.urlsafe_b64decode(configured)
    except (ValueError, TypeError) as unusable:
        raise ValueError("the key is not base64") from unusable
    if len(key) != KEY_BYTES:
        raise ValueError(f"the key must decode to {KEY_BYTES} bytes")
    return key


def generate_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode()


# The purpose check secrets are sealed under, and the owner each is bound to.
CHECK_SECRET_PURPOSE: Final = "check-secret"


def check_secret_owner(project_id: str, name: str) -> str:
    return f"{project_id}:{name}"


def encode_sealed(sealed: bytes) -> str:
    """Ciphertext as text, for a task argument. Still ciphertext."""
    return base64.b64encode(sealed).decode()


def decode_sealed(encoded: str) -> bytes:
    return base64.b64decode(encoded)


# F21, ADR-010 amended: notification channel secrets — a webhook URL that is itself
# a credential, a PagerDuty routing key, a webhook's signing secret. The same key
# as check secrets, their own purpose, bound to the organization and the channel.
NOTIFICATION_CHANNEL_PURPOSE: Final = "notification-channel"


def channel_secret_owner(org_id: str, channel_id: str) -> str:
    return f"{org_id}:{channel_id}"
