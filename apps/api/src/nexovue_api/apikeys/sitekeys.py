"""Minting a public site key.

**There is no hash here, and that is the decision, not an omission.** A site key
is printed in the page that carries it, so hashing it at rest would protect a
value anyone can already read while costing three things that matter: the
exact-match index the browser door's lookup needs, the ability to show the key
again to a customer redeploying their site, and the plain statement — visible in
the schema — that this table holds nothing secret.

The value is still high-entropy random. Not secret is not the same as guessable:
twenty-two base62 characters is what stops somebody enumerating projects and
posting junk telemetry into one they never visited.

The scheme differs from `nvk_live_` on purpose. The receiver's server door
rejects anything that is not `nvk_live_<22>` before it looks anything up, so a
site key pasted into a server exporter fails on format rather than reaching a
query.
"""

from __future__ import annotations

import secrets
import string
from typing import Final

# `nvp` for nexovue public — one letter from `nvk`, and the letter says which.
SITE_KEY_SCHEME: Final = "nvp_live_"
SITE_KEY_RANDOM_LENGTH: Final = 22
SITE_KEY_ALPHABET: Final = string.ascii_letters + string.digits


def generate_site_key() -> str:
    """A fresh public key. Stored whole, shown as often as the customer asks."""
    random_part = "".join(secrets.choice(SITE_KEY_ALPHABET) for _ in range(SITE_KEY_RANDOM_LENGTH))
    return f"{SITE_KEY_SCHEME}{random_part}"
