"""F4: chat's one way into the API (design, *The internal client*; plan T2.8).

The guarded client refuses private addresses, and that flag must not open for model-driven
calls, so this is its own small pycurl GET:

- the base URL comes only from `INTERNAL_API_URL` (default `http://api:8000`); a model never
  names an address, and the path is the catalog's own;
- redirects are never followed, and only `http` and `https` are spoken;
- every argument is checked by `check_arguments` before a URL exists: a path slot (none in
  Step 2's tools) refuses `/`, `\\`, `%`, control characters, `.` and `..`, since uvicorn
  decodes the path before routing, so percent-encoding alone does not close path tricks;
- each value is then encoded with `quote(value, safe="")`;
- the ticket goes in the `Authorization` header, never the URL, so no log line holds it.

The answer is capped, and a failure is a closed word, never curl's text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Final, Literal, Protocol
from urllib.parse import quote, urlsplit

import pycurl
from nexovue_platform.assistant_tools import ChatTool

USER_AGENT: Final = "nexovue-assistant/0.1"
# A tool result is cut to 8 KiB before the model sees it; the API's page is larger.
MAX_BODY_BYTES: Final = 2 * 1024 * 1024
READ_TIMEOUT_SECONDS: Final = 30

ReadFailure = Literal["unreachable", "timed_out", "too_large", "not_allowed"]


@dataclass(frozen=True, slots=True)
class ApiAnswer:
    status: int | None
    body: bytes
    failure: ReadFailure | None = None


class ApiClient(Protocol):
    """What the loop reads through: one catalog GET, as the asking person."""

    def get(self, tool: ChatTool, query: Mapping[str, str], *, ticket: str) -> ApiAnswer: ...


class PathRefused(ValueError):  # noqa: N818 — a refusal, named for the reader
    pass


_PATH_FORBIDDEN: Final = frozenset("/\\%?#")


def check_path_slot(value: str) -> None:
    """A value bound for a path segment: nothing that could move the route."""
    if (
        not value
        or value in (".", "..")
        or any(character in _PATH_FORBIDDEN for character in value)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise PathRefused("a path value may not hold / \\ % ? # . .. or control characters")


def build_url(base_url: str, tool: ChatTool, query: Mapping[str, str]) -> str:
    """The catalog's path under the configured base, each query value encoded whole."""
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.query:
        raise PathRefused("INTERNAL_API_URL must be a plain http or https base")
    pairs = "&".join(
        f"{quote(name, safe='')}={quote(value, safe='')}" for name, value in query.items()
    )
    url = f"{base_url.rstrip('/')}{tool.route}"
    return f"{url}?{pairs}" if pairs else url


@dataclass(frozen=True, slots=True)
class CurlApiClient:
    base_url: str
    timeout_seconds: int = READ_TIMEOUT_SECONDS

    def get(self, tool: ChatTool, query: Mapping[str, str], *, ticket: str) -> ApiAnswer:
        try:
            url = build_url(self.base_url, tool, query)
        except PathRefused:
            return ApiAnswer(None, b"", "not_allowed")
        if any(ord(character) < 0x21 for character in ticket):
            return ApiAnswer(None, b"", "not_allowed")
        body = BytesIO()
        too_large = False

        def keep(chunk: bytes) -> int | None:
            nonlocal too_large
            if body.tell() + len(chunk) > MAX_BODY_BYTES:
                too_large = True
                return 0  # declining the chunk ends the transfer
            body.write(chunk)
            return None

        curl = pycurl.Curl()
        try:
            curl.setopt(pycurl.URL, url)
            curl.setopt(pycurl.PROTOCOLS, pycurl.PROTO_HTTP | pycurl.PROTO_HTTPS)
            curl.setopt(pycurl.FOLLOWLOCATION, False)
            curl.setopt(pycurl.TIMEOUT, self.timeout_seconds)
            curl.setopt(pycurl.USERAGENT, USER_AGENT)
            curl.setopt(
                pycurl.HTTPHEADER, [f"Authorization: Bearer {ticket}", "Accept: application/json"]
            )
            curl.setopt(pycurl.WRITEFUNCTION, keep)
            curl.perform()
            status = int(curl.getinfo(pycurl.RESPONSE_CODE))
        except pycurl.error as error:
            if too_large:
                return ApiAnswer(None, b"", "too_large")
            code = error.args[0] if error.args else None
            return ApiAnswer(
                None, b"", "timed_out" if code == pycurl.E_OPERATION_TIMEDOUT else "unreachable"
            )
        finally:
            curl.close()
        return ApiAnswer(status, body.getvalue())
