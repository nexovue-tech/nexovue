"""An organization's AI connection: what may be saved, and how it is fingerprinted (FR23, FR26).

Both sides run the same rules: the API when an admin saves (`PUT /ai/connection`), and the AI
role at call time, because the row could have been changed by anyone holding the install
account and a check on save alone is a check once (AC34).

**Plain `http` only to a private address written as that address.** On save the API cannot
know what a name will resolve to, so a name over `http` is refused; at call time the transport
lets `http` reach only an address the operator listed in `AI_PRIVATE_ENDPOINTS` (FR32).
Loopback is refused everywhere, with words that say what to do instead.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import unicodedata
from typing import Final, Literal
from urllib.parse import urlsplit

from nexovue_platform.ai_records import WIRE_FORMATS

# The purpose a provider key is sealed under, and the owner it is bound to: the address is
# in the owner, so a key sealed for one address cannot open for another (AC34).
AI_CONNECTION_PURPOSE: Final = "ai-connection"

MODEL_PATTERN: Final = re.compile(r"[A-Za-z0-9._:/-]{1,200}")
MAX_BASE_URL_LENGTH: Final = 500
MAX_REGION_LENGTH: Final = 64
MAX_KEY_LENGTH: Final = 4096
_LOCAL_NAMES: Final = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

Refusal = Literal[
    "base_url_scheme",
    "base_url_credentials",
    "base_url_query",
    "base_url_fragment",
    "base_url_host",
    "base_url_http_public",
    "base_url_loopback",
    "base_url_length",
    "model",
    "region",
    "key",
    "wire_format",
]

# What each refusal says to the person saving (spec, *Error cases*).
REFUSAL_WORDS: Final[dict[str, str]] = {
    "base_url_scheme": "The address must start with https://.",
    "base_url_credentials": "The address must not hold a user name or password.",
    "base_url_query": "The address must not hold a query (the part after ?).",
    "base_url_fragment": "The address must not hold a fragment (the part after #).",
    "base_url_host": "The address has no host.",
    "base_url_http_public": (
        "Plain http is allowed only to a private address written as that address, such as "
        "http://192.168.1.20:11434, which the operator has listed in AI_PRIVATE_ENDPOINTS. "
        "Use https for anything else."
    ),
    "base_url_loopback": (
        "Loopback cannot be reached from a container. Bind Ollama to a private address, and "
        "list it in AI_PRIVATE_ENDPOINTS."
    ),
    "base_url_length": f"The address must be at most {MAX_BASE_URL_LENGTH} characters.",
    "model": (
        "A model name may hold letters, digits and . _ : / - only, at most 200 of them, "
        "and no '..'."
    ),
    "region": f"The region must be at most {MAX_REGION_LENGTH} characters, on one line.",
    "key": f"The key must be one line, at most {MAX_KEY_LENGTH} characters.",
    "wire_format": "That wire format is not one nexovue speaks.",
}


class ConnectionRefused(ValueError):  # noqa: N818 — a refusal, named for the reader
    """A value FR23 does not allow; `reason` is a closed word, never the value itself."""

    def __init__(self, reason: Refusal) -> None:
        super().__init__(REFUSAL_WORDS[reason])
        self.reason: Refusal = reason


def _has_control(value: str) -> bool:
    return any(unicodedata.category(character) in ("Cc", "Cf", "Zl", "Zp") for character in value)


def _is_loopback(host: str) -> bool:
    if host.lower() in _LOCAL_NAMES or host.lower().endswith(".localhost"):
        return True
    address = _address(host)
    return address is not None and address.is_loopback


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def check_base_url(base_url: str) -> None:
    """Refuse a base URL FR23 does not allow."""
    if len(base_url) > MAX_BASE_URL_LENGTH:
        raise ConnectionRefused("base_url_length")
    if _has_control(base_url) or " " in base_url:
        raise ConnectionRefused("base_url_host")
    try:
        parts = urlsplit(base_url)
        host = parts.hostname
        parts.port  # noqa: B018 — reading it raises on a port that is not a number
    except ValueError as unreadable:
        raise ConnectionRefused("base_url_host") from unreadable
    if parts.scheme not in ("https", "http"):
        raise ConnectionRefused("base_url_scheme")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ConnectionRefused("base_url_credentials")
    if parts.query or base_url.rstrip().endswith("?"):
        raise ConnectionRefused("base_url_query")
    if parts.fragment or "#" in base_url:
        raise ConnectionRefused("base_url_fragment")
    if not host:
        raise ConnectionRefused("base_url_host")
    if _is_loopback(host):
        raise ConnectionRefused("base_url_loopback")
    if parts.scheme == "http":
        address = _address(host)
        if address is None or not address.is_private or address.is_link_local:
            raise ConnectionRefused("base_url_http_public")


def check_model(model: str) -> None:
    if MODEL_PATTERN.fullmatch(model) is None or ".." in model:
        raise ConnectionRefused("model")


def check_region(region: str | None) -> None:
    if region is not None and (len(region) > MAX_REGION_LENGTH or _has_control(region)):
        raise ConnectionRefused("region")


def check_key(key: str) -> None:
    """A key holding a control character could end a header and start another (AC34)."""
    if not key or len(key) > MAX_KEY_LENGTH or _has_control(key) or key != key.strip():
        raise ConnectionRefused("key")


def check_wire_format(wire_format: str) -> None:
    if wire_format not in WIRE_FORMATS:
        raise ConnectionRefused("wire_format")


def check_connection(*, wire_format: str, base_url: str, model: str, region: str | None) -> None:
    """Every rule FR23 sets on the stored values, in one call."""
    check_wire_format(wire_format)
    check_base_url(base_url)
    check_model(model)
    check_region(region)


def provider_host(base_url: str) -> str:
    """Scheme and host, with any port, and never a path, which may hold a secret (AC72)."""
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    shown = f"[{host}]" if ":" in host else host
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{shown}{port}"


def is_private_address(base_url: str) -> bool:
    """Only for the screen's where-data-goes sentence (foundation FD3): no code path asks
    "is this local?" for any other reason."""
    address = _address(urlsplit(base_url).hostname or "")
    return address is not None and address.is_private


def _sha256(values: list[str | None]) -> str:
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def check_fingerprint(
    *, wire_format: str, base_url: str, model: str, region: str | None, key_id: str | None
) -> str:
    """What a passing test covers (design, *The two fingerprints*). Never reads the key."""
    return _sha256([wire_format, base_url, model, region, key_id])


def consent_fingerprint(
    *, wire_format: str, base_url: str, region: str | None, key_id: str | None
) -> str:
    """What an owner's consent covers: the check's, without the model (FR26)."""
    return _sha256([wire_format, base_url, region, key_id])


def key_owner(org_id: str, connection_id: str, base_url: str) -> str:
    """The owner a provider key is sealed for (design, *Keys*)."""
    return f"{org_id}:{connection_id}:{hashlib.sha256(base_url.encode()).hexdigest()}"
