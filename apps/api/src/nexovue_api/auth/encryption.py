"""Encrypting the one credential this system cannot hash.

**Everything else here is hashed, and that is the first thing to say.** Passwords
take argon2 (`credentials.py`), API keys and session tokens take SHA-256
(`models.py`, `sessions.py`), because nothing ever needs the original back — a
comparison is enough. A TOTP secret is the exception: verifying a six-digit code
means recomputing an HMAC with the *same* secret the authenticator holds, so the
secret has to survive in a recoverable form or the feature cannot exist.

That leaves encryption as the only protection available, and this module is the
whole of it. **It is not the envelope-encryption-with-a-KMS scheme the scheduler
declined to invent** (`pollers/postgres_connect.py` says so in as many words):
one key, held in the API's environment, applied to one kind of value. What it
buys is precise and worth stating so nobody expects more:

* A Postgres dump, a backup file, a replica, or a read-only SQL injection yields
  ciphertext and nothing else. That is the realistic leak for a control plane.
* An attacker who owns the API process reads the key and therefore the secrets.
  This does not pretend otherwise. At that point the session store is theirs too.

**AES-256-GCM rather than a CBC-plus-HMAC construction.** GCM authenticates as it
decrypts, so a tampered ciphertext fails rather than decrypting to rubbish the
caller then treats as a secret.

**The user id is authenticated as associated data, and that is the load-bearing
part.** Without it, somebody with write access to Postgres could copy their own
`totp_secret` into a colleague's row and then hold that colleague's second
factor. Binding the ciphertext to the row it belongs to makes such a copy fail to
decrypt, and it costs one argument.

**A missing key is refused, never worked around.** An unconfigured deployment
gets 503 from the endpoints that need one, exactly as an unconfigured mail
provider does in `deps.get_email_sender`. Storing a secret in the clear because
the key was absent is the one outcome this module exists to prevent.
"""

from __future__ import annotations

import base64
import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from nexovue_api.errors.exceptions import DependencyUnavailableError, InternalError

# The environment variable a deployment sets. Named for what it protects rather
# than for TOTP: the next credential that cannot be hashed should share this key
# rather than introduce a second one nobody rotates.
CREDENTIAL_ENCRYPTION_VARIABLE: Final = "CREDENTIAL_ENCRYPTION_KEY"

KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12

# Stored ahead of the nonce so a future scheme can be told apart from this one
# without guessing at lengths. Rotation is a change of key; this is a change of
# construction, and the two are different problems.
FORMAT_VERSION: Final = 1

_KEY_MISCONFIGURED: Final = (
    "Two-factor authentication is not available on this deployment, so this could not be "
    "completed. Nothing has changed on the account."
)


class CredentialBox:
    """Seals and opens one kind of value, bound to the row that owns it."""

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"{CREDENTIAL_ENCRYPTION_VARIABLE} must decode to {KEY_BYTES} bytes")
        self._cipher = AESGCM(key)

    def seal(self, plaintext: str, *, owner_id: str) -> bytes:
        """Encrypt, authenticating the owner so the ciphertext cannot be moved."""
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._cipher.encrypt(nonce, plaintext.encode(), _associated_data(owner_id))
        return bytes([FORMAT_VERSION]) + nonce + sealed

    def open(self, sealed: bytes, *, owner_id: str) -> str:
        """Decrypt, or refuse.

        A failure here is never a caller's mistake — the bytes came from our own
        column — so it is an internal error rather than a 4xx. It means the key
        changed, the row was tampered with, or the ciphertext belongs to
        somebody else, and none of those should read as "wrong code".
        """
        if len(sealed) <= 1 + NONCE_BYTES or sealed[0] != FORMAT_VERSION:
            raise InternalError("A stored credential is not in a form this build can read.")
        nonce = sealed[1 : 1 + NONCE_BYTES]
        try:
            opened = self._cipher.decrypt(
                nonce, sealed[1 + NONCE_BYTES :], _associated_data(owner_id)
            )
        except InvalidTag as tampered:
            raise InternalError("A stored credential could not be decrypted.") from tampered
        return opened.decode()


def credential_box(configured_key: str) -> CredentialBox:
    """The box for this deployment, or a 503 naming nothing about the key.

    The variable name goes to the operator through the log the error handler
    already writes; it is deliberately absent from the message, because the
    endpoints that reach this are reachable by anybody with an account.
    """
    if not configured_key:
        raise DependencyUnavailableError(_KEY_MISCONFIGURED)
    try:
        return CredentialBox(base64.urlsafe_b64decode(configured_key))
    except (ValueError, TypeError) as unusable:
        raise DependencyUnavailableError(_KEY_MISCONFIGURED) from unusable


def generate_key() -> str:
    """A key in the form the environment variable takes. For operators, not for
    requests — nothing in the request path calls this."""
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode()


def _associated_data(owner_id: str) -> bytes:
    """What the ciphertext is bound to. Prefixed so one owner's TOTP secret could
    not be presented as the same owner's future second sealed credential."""
    return f"totp:{owner_id}".encode()
