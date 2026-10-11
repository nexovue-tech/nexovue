"""AES-256-GCM sealing, bound to what a value belongs to, under a keyring.

Shared because two processes hold the check-secret key: the API seals a value
when a customer saves it, the scheduler opens it immediately before a request
uses it. Nothing else crosses the queue but ciphertext (ADR-010).

**Associated data carries a purpose and an owner**, so a sealed value copied into
another row — another project, another name, another kind of credential — fails
to open rather than decrypting as somebody else's secret.

**Every value names the key that sealed it** (readiness blocker 8). A keyring
holds one current key, which seals, and previous keys, which only open. Rotating
is: make a new key current, keep the old one previous, re-seal everything
(`python -m nexovue_api.keys reseal`), then drop the old key. The key id is
derived from the key, so nobody manages ids, and it is in the associated data,
so a value cannot be pointed at another key.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12
KEY_ID_BYTES: Final = 8

# Stored first so a future construction can be told apart from this one. Version
# 1 carried no key id and is not read (pre-deployment; founder decision).
FORMAT_VERSION: Final = 2
_HEADER_BYTES: Final = 1 + KEY_ID_BYTES + NONCE_BYTES


class SealError(ValueError):
    """A sealed value could not be opened: wrong key, wrong owner, or tampered."""


def key_id(key: bytes) -> bytes:
    """Which key this is, without revealing it: the first bytes of its SHA-256."""
    return hashlib.sha256(key).digest()[:KEY_ID_BYTES]


@dataclass(frozen=True, slots=True)
class Keyring:
    """The key that seals, and the keys that still open what older ones sealed."""

    current: bytes
    previous: tuple[bytes, ...] = ()

    def __post_init__(self) -> None:
        keys = (self.current, *self.previous)
        if any(len(key) != KEY_BYTES for key in keys):
            raise ValueError(f"every key must be {KEY_BYTES} bytes")
        if len({key_id(key) for key in keys}) != len(keys):
            raise ValueError("a key appears twice in the keyring")

    def by_id(self) -> dict[bytes, bytes]:
        return {key_id(key): key for key in (self.current, *self.previous)}


@dataclass(frozen=True, slots=True)
class ConfiguredKeys:
    """A keyring as the environment carries it, not yet decoded: the current
    key, and previous keys comma-separated. Empty `current` means unconfigured."""

    current: str
    previous: str = ""

    def __repr__(self) -> str:
        return "ConfiguredKeys(<redacted>)"


def keyring_from(current: str, previous: str = "") -> Keyring:
    """Decode a keyring. Raises ValueError naming nothing secret."""
    return Keyring(
        current=decode_key(current),
        previous=tuple(decode_key(entry.strip()) for entry in previous.split(",") if entry.strip()),
    )


def sealed_key_id(sealed: bytes) -> bytes | None:
    """The id of the key that sealed this value, or None for a value this build
    cannot read at all."""
    if len(sealed) <= _HEADER_BYTES or sealed[0] != FORMAT_VERSION:
        return None
    return sealed[1 : 1 + KEY_ID_BYTES]


class SealedBox:
    """Seals and opens values for one purpose under one keyring."""

    def __init__(self, keyring: Keyring, *, purpose: str) -> None:
        if not purpose:
            raise ValueError("a purpose is required")
        self._purpose = purpose
        self._current_id = key_id(keyring.current)
        self._ciphers = {kid: AESGCM(key) for kid, key in keyring.by_id().items()}

    @property
    def current_key_id(self) -> bytes:
        return self._current_id

    def holds(self, kid: bytes) -> bool:
        """Whether this box can open a value sealed under the key with this id."""
        return kid in self._ciphers

    def seal(self, plaintext: str, *, owner: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._ciphers[self._current_id].encrypt(
            nonce, plaintext.encode(), self._associated_data(self._current_id, owner)
        )
        return bytes([FORMAT_VERSION]) + self._current_id + nonce + sealed

    def open(self, sealed: bytes, *, owner: str) -> str:
        kid = sealed_key_id(sealed)
        if kid is None:
            raise SealError("not a sealed value this build can read")
        cipher = self._ciphers.get(kid)
        if cipher is None:
            raise SealError("sealed under a key this keyring does not hold")
        nonce = sealed[1 + KEY_ID_BYTES : _HEADER_BYTES]
        try:
            opened = cipher.decrypt(
                nonce, sealed[_HEADER_BYTES:], self._associated_data(kid, owner)
            )
        except InvalidTag as tampered:
            raise SealError("the sealed value could not be opened") from tampered
        return opened.decode()

    def _associated_data(self, kid: bytes, owner: str) -> bytes:
        return f"{self._purpose}:{owner}:".encode() + kid


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
