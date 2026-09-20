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
from typing import Final

from nexovue_platform.checks.sealing import KEY_BYTES, SealedBox, SealError
from nexovue_platform.checks.sealing import generate_key as _generate_key

from nexovue_api.errors.exceptions import DependencyUnavailableError, InternalError

# The environment variable a deployment sets.
CREDENTIAL_ENCRYPTION_VARIABLE: Final = "CREDENTIAL_ENCRYPTION_KEY"

# Bound into every ciphertext, so a TOTP secret cannot be presented as the same
# owner's credential of another kind. Check secrets have their own key and purpose.
TOTP_PURPOSE: Final = "totp"

_KEY_MISCONFIGURED: Final = (
    "Two-factor authentication is not available on this deployment, so this could not be "
    "completed. Nothing has changed on the account."
)


class CredentialBox:
    """Seals and opens one kind of value, bound to the row that owns it."""

    def __init__(self, key: bytes, *, purpose: str = TOTP_PURPOSE) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"{CREDENTIAL_ENCRYPTION_VARIABLE} must decode to {KEY_BYTES} bytes")
        self._box = SealedBox(key, purpose=purpose)

    def seal(self, plaintext: str, *, owner_id: str) -> bytes:
        """Encrypt, authenticating the owner so the ciphertext cannot be moved."""
        return self._box.seal(plaintext, owner=owner_id)

    def open(self, sealed: bytes, *, owner_id: str) -> str:
        """Decrypt, or refuse.

        A failure here is never a caller's mistake — the bytes came from our own
        column — so it is an internal error rather than a 4xx. It means the key
        changed, the row was tampered with, or the ciphertext belongs to
        somebody else, and none of those should read as "wrong code".
        """
        try:
            return self._box.open(sealed, owner=owner_id)
        except SealError as unreadable:
            raise InternalError("A stored credential could not be decrypted.") from unreadable


def credential_box(configured_key: str, *, purpose: str = TOTP_PURPOSE) -> CredentialBox:
    """The box for this deployment, or a 503 naming nothing about the key.

    The variable name goes to the operator through the log the error handler
    already writes; it is deliberately absent from the message, because the
    endpoints that reach this are reachable by anybody with an account.
    """
    if not configured_key:
        raise DependencyUnavailableError(_KEY_MISCONFIGURED)
    try:
        return CredentialBox(base64.urlsafe_b64decode(configured_key), purpose=purpose)
    except (ValueError, TypeError) as unusable:
        raise DependencyUnavailableError(_KEY_MISCONFIGURED) from unusable


def generate_key() -> str:
    """A key in the form the environment variable takes. For operators, not for
    requests — nothing in the request path calls this."""
    return _generate_key()
