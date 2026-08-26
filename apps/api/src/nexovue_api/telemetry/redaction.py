"""Keeping credentials out of the logs.

Two rules, matching the Go services exactly: an event key that names a secret
never has its value logged, and a connection string loses its password wherever
it appears — including inside the free text of a driver error, which is how
DSNs most often escape into a log line.
"""

from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any, Final

PLACEHOLDER: Final = "[redacted]"

# Matched against the event key, not the value: cheap, predictable, and it does
# not mangle legitimate data the way value sniffing does.
SECRET_KEY_MARKERS: Final = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "session_id",
)

_CREDENTIALS_IN_URL: Final = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+):[^\s/@]*@")


def is_secret_key(key: str) -> bool:
    """Whether a value logged under this key must never be written out."""
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_KEY_MARKERS)


def redact_credentials(text: str) -> str:
    """Remove connection-string passwords, keeping the scheme, user and host."""
    return _CREDENTIALS_IN_URL.sub(rf"\1:{PLACEHOLDER}@", text)


def redact_event(
    _logger: Any,
    _method_name: str,
    event: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """structlog processor applying both rules to every field of every line."""
    for key, value in event.items():
        if is_secret_key(key):
            event[key] = PLACEHOLDER
        elif isinstance(value, str):
            event[key] = redact_credentials(value)
    return event
