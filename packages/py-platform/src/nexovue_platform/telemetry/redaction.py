"""Keeping credentials out of the logs.

Three rules: an event key that names a secret never has its value logged; a
connection string loses its password wherever it appears — including inside the
free text of a driver error, which is how DSNs most often escape into a log line;
and a heartbeat URL loses its token, because the token is the whole credential and
every request log line, access log line and refusal names the path it arrived on.
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

# `/api/v1/heartbeat/<token>` — the path segment after `heartbeat/`, whatever follows it.
_HEARTBEAT_TOKEN_IN_PATH: Final = re.compile(r"(/heartbeat/)[^\s/?#\"']+")

# `/support/view-as?ticket=<ticket>` — a one-time credential for a support view,
# in the query string the access line records.
_TICKET_IN_QUERY: Final = re.compile(r"([?&]ticket=)[^\s&#\"']+")


def is_secret_key(key: str) -> bool:
    """Whether a value logged under this key must never be written out."""
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_KEY_MARKERS)


def redact_credentials(text: str) -> str:
    """Remove connection-string passwords, heartbeat tokens and support tickets."""
    without_passwords = _CREDENTIALS_IN_URL.sub(rf"\1:{PLACEHOLDER}@", text)
    without_tokens = _HEARTBEAT_TOKEN_IN_PATH.sub(rf"\1{PLACEHOLDER}", without_passwords)
    return _TICKET_IN_QUERY.sub(rf"\1{PLACEHOLDER}", without_tokens)


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
