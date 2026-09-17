"""From a request as the customer wrote it to one ready to send.

Substitution happens here and nowhere else, immediately before sending, with the
secrets already opened. Everything a substituted value could break is checked
*after* substitution — a header value from a variable is refused if it carries a
line break, exactly as a typed one is at save.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from nexovue_platform.checks.config import (
    ApiKeyAuth,
    BasicAuth,
    BearerAuth,
    BinaryBody,
    FormBody,
    HttpRequest,
    JsonBody,
    MultipartBody,
    MultipartFile,
    TextBody,
    XmlBody,
)
from nexovue_platform.checks.evidence import (
    MAX_CAPTURED_BODY_BYTES,
    CapturedBody,
    CapturedHeader,
    SentRequest,
)
from nexovue_platform.checks.task import FileReference
from nexovue_platform.checks.templating import UnresolvedReferenceError, substitute

from nexovue_scheduler.probes.failures import MisconfiguredError
from nexovue_scheduler.probes.http_check import PreparedRequest
from nexovue_scheduler.probes.urls import reportable_url

CONTENT_TYPE: Final = "Content-Type"
JSON_CONTENT_TYPE: Final = "application/json"
XML_CONTENT_TYPE: Final = "application/xml"
FORM_CONTENT_TYPE: Final = "application/x-www-form-urlencoded"

# Shown as the header's name with its value withheld. The customer typed these,
# but evidence is kept and shown to everyone who can read the project, and a
# session cookie is somebody's session.
SENSITIVE_HEADERS: Final = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"}
)
WITHHELD: Final = "[withheld]"

_SCHEMES: Final = ("http", "https")
_PATH_SAFE: Final = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE: Final = "/?%:@!$&'()*+,;=-._~"

Redact = Callable[[str], str]


def _unchanged(text: str) -> str:
    return text


_LINE_BREAKS: Final = re.compile(r"[\r\n\x00]")


@dataclass(frozen=True, slots=True)
class MultipartPart:
    name: str
    value: bytes
    # Set for a file part; a plain field has neither.
    filename: str | None = None
    content_type: str | None = None


class FileSource(Protocol):
    """Where a file's bytes come from. The worker reads object storage; a test
    hands them over directly."""

    def read(self, org_id: str, file_id: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class BuiltRequest:
    prepared: PreparedRequest
    # What is kept as evidence of what was sent. Redacted later, again, by value.
    sent: SentRequest


def build(
    request: HttpRequest,
    *,
    org_id: str,
    variables: Mapping[str, str],
    secrets: Mapping[str, str],
    files: FileSource,
    file_references: Mapping[str, FileReference],
    redact: Redact = _unchanged,
) -> BuiltRequest:
    """The request, substituted and checked, or `MisconfiguredError` saying why not."""

    def fill(text: str) -> str:
        try:
            return substitute(text, variables=variables, secrets=secrets)
        except UnresolvedReferenceError as undefined:
            raise MisconfiguredError(
                f"{{{{{undefined.reference}}}}} is not defined, so the request was not sent"
            ) from undefined

    headers = [(fill(h.name), fill(h.value)) for h in request.headers if h.enabled]
    query = [(fill(q.name), fill(q.value)) for q in request.query if q.enabled]
    _apply_auth(request, headers, query, secrets)

    body: bytes | None = None
    content_type: str | None = None
    captured_body: CapturedBody | None = None

    match request.body:
        case TextBody(content=content, content_type=declared):
            body, content_type = fill(content).encode(), declared
        case JsonBody(content=content):
            body, content_type = fill(content).encode(), JSON_CONTENT_TYPE
        case XmlBody(content=content):
            body, content_type = fill(content).encode(), XML_CONTENT_TYPE
        case FormBody(fields=fields):
            pairs = [(fill(f.name), fill(f.value)) for f in fields if f.enabled]
            body, content_type = urlencode(pairs).encode(), FORM_CONTENT_TYPE
        case MultipartBody(parts=parts):
            multipart: list[MultipartPart] = []
            for part in parts:
                if isinstance(part, MultipartFile):
                    reference = file_references.get(str(part.file_id))
                    multipart.append(
                        MultipartPart(
                            name=part.name,
                            value=files.read(org_id, str(part.file_id)),
                            filename=reference.name if reference else "file",
                            content_type=(
                                reference.content_type if reference else "application/octet-stream"
                            ),
                        )
                    )
                else:
                    multipart.append(MultipartPart(name=part.name, value=fill(part.value).encode()))
            body, content_type = encode_multipart(multipart)
            captured_body = _multipart_summary(multipart, redact)
        case BinaryBody(file_id=file_id, content_type=declared):
            body, content_type = files.read(org_id, str(file_id)), declared

    if content_type is not None and not _has_header(headers, CONTENT_TYPE):
        headers.append((CONTENT_TYPE, content_type))
    for name, value in headers:
        if _LINE_BREAKS.search(name) or _LINE_BREAKS.search(value):
            raise MisconfiguredError("a header contains a line break after substitution")
        if not (name.isascii() and value.isascii()):
            raise MisconfiguredError(
                "a header contains characters outside ASCII after substitution"
            )

    url = _ascii_url(_with_query(_checked_url(fill(request.url)), query))
    prepared = PreparedRequest(
        method=request.method,
        url=url,
        headers=headers,
        body=body,
        follow_redirects=request.follow_redirects,
        timeout_seconds=request.timeout_seconds,
    )
    sent = SentRequest(
        method=request.method,
        url=reportable_url(url) or url,
        headers=[CapturedHeader(name=name, value=_shown(name, value)) for name, value in headers],
        body=captured_body if captured_body is not None else capture_body(body, redact),
    )
    return BuiltRequest(prepared=prepared, sent=sent)


def capture_body(body: bytes | None, redact: Redact = _unchanged) -> CapturedBody | None:
    """The first bytes of a body, as text, for evidence (EC13).

    Redacted *before* it is cut: a secret straddling the cut would otherwise leave
    its first half in the evidence, where no whole-value replacement finds it.
    """
    if body is None:
        return None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as undecodable:
        # The probe's own cap cutting through a multi-byte character is still text.
        if undecodable.start < len(body) - 3:
            return CapturedBody(
                text=f"[{len(body)} bytes that are not text]",
                size_bytes=len(body),
                truncated=False,
                binary=True,
            )
        text = body[: undecodable.start].decode("utf-8")
    encoded = redact(text).encode("utf-8")
    return CapturedBody(
        text=encoded[:MAX_CAPTURED_BODY_BYTES].decode("utf-8", "ignore"),
        size_bytes=len(body),
        truncated=len(encoded) > MAX_CAPTURED_BODY_BYTES,
    )


def encode_multipart(parts: list[MultipartPart]) -> tuple[bytes, str]:
    """An RFC 7578 body, built here rather than by libcurl's deprecated form API,
    so the bytes sent are the bytes this function returns."""
    boundary = f"nexovue-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for part in parts:
        disposition = f'form-data; name="{_quoted(part.name)}"'
        if part.filename is not None:
            disposition += f'; filename="{_quoted(part.filename)}"'
        head = f"--{boundary}\r\nContent-Disposition: {disposition}\r\n"
        if part.content_type:
            head += f"Content-Type: {part.content_type}\r\n"
        chunks += [head.encode(), b"\r\n", part.value, b"\r\n"]
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _quoted(value: str) -> str:
    """The HTML form encoding of a quote and a line break inside a quoted name."""
    return value.replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")


def captured_headers(headers: list[tuple[str, str]]) -> list[CapturedHeader]:
    return [CapturedHeader(name=name, value=_shown(name, value)) for name, value in headers]


def _apply_auth(
    request: HttpRequest,
    headers: list[tuple[str, str]],
    query: list[tuple[str, str]],
    secrets: Mapping[str, str],
) -> None:
    auth = request.auth
    if isinstance(auth, BasicAuth):
        password = _secret(secrets, auth.password_secret)
        pair = base64.b64encode(f"{auth.username}:{password}".encode()).decode()
        headers.append(("Authorization", f"Basic {pair}"))
    elif isinstance(auth, BearerAuth):
        headers.append(("Authorization", f"Bearer {_secret(secrets, auth.token_secret)}"))
    elif isinstance(auth, ApiKeyAuth):
        value = _secret(secrets, auth.value_secret)
        (headers if auth.location == "header" else query).append((auth.name, value))


def _secret(secrets: Mapping[str, str], name: str) -> str:
    if name not in secrets:
        raise MisconfiguredError(f"the secret {name} no longer exists, so the request was not sent")
    return secrets[name]


def _checked_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme.lower() not in _SCHEMES or not parts.hostname:
        raise MisconfiguredError("the URL is not an http:// or https:// address after substitution")
    if _LINE_BREAKS.search(url) or " " in url:
        raise MisconfiguredError("the URL contains spaces or line breaks after substitution")
    return url


def _ascii_url(url: str) -> str:
    """The URL libcurl can send: an internationalised host in its `xn--` form, and
    any other non-ASCII character percent-encoded. Existing escapes are kept."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        ascii_host = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError as unencodable:
        raise MisconfiguredError("the URL's host is not a valid domain name") from unencodable
    netloc = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    if parts.username is not None:
        userinfo = quote(parts.username, safe="%")
        if parts.password is not None:
            userinfo = f"{userinfo}:{quote(parts.password, safe='%')}"
        netloc = f"{userinfo}@{netloc}"
    return urlunsplit(
        (
            parts.scheme,
            netloc,
            quote(parts.path, safe=_PATH_SAFE),
            quote(parts.query, safe=_QUERY_SAFE),
            quote(parts.fragment, safe=_QUERY_SAFE),
        )
    )


def _with_query(url: str, extra: list[tuple[str, str]]) -> str:
    if not extra:
        return url
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True) + extra
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def _has_header(headers: list[tuple[str, str]], name: str) -> bool:
    return any(existing.lower() == name.lower() for existing, _ in headers)


def _shown(name: str, value: str) -> str:
    return WITHHELD if name.lower() in SENSITIVE_HEADERS else value


def _multipart_summary(parts: list[MultipartPart], redact: Redact) -> CapturedBody:
    """A multipart body is described, not reproduced: file bytes stay out of logs."""
    described = [
        {"name": part.name, "file": part.filename, "bytes": len(part.value)}
        if part.filename is not None
        else {"name": part.name, "value": part.value.decode("utf-8", "replace")}
        for part in parts
    ]
    text = redact(json.dumps(described))
    return CapturedBody(
        text=text[:MAX_CAPTURED_BODY_BYTES],
        size_bytes=sum(len(part.value) for part in parts),
        truncated=len(text) > MAX_CAPTURED_BODY_BYTES,
    )
