"""A pasted cURL command, read as data (FR12).

Words are split the way bash splits them (`shlex`, POSIX mode) and matched
against an explicit table of options. An option outside the table is listed as
skipped with its name, never dropped silently (EC5); a local file cannot be read
from the user's machine, so it becomes a file to attach (AC8).
"""

from __future__ import annotations

import math
import re
import shlex
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Final
from urllib.parse import quote_plus, unquote, unquote_plus, urlsplit, urlunsplit

from nexovue_platform.checks.config import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_REQUEST_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    ApiCheckConfig,
    BasicAuth,
    BearerAuth,
    FormBody,
    HttpRequest,
    JsonBody,
    MultipartBody,
    MultipartField,
    NameValue,
    NoAuth,
    NoBody,
    RequestBody,
    TextBody,
    XmlBody,
)
from pydantic import ValidationError

from nexovue_api.checkimports import plan
from nexovue_api.checkimports.schemas import ImportPlan
from nexovue_api.errors.exceptions import InvalidRequestError

MAX_CURL_CHARACTERS: Final = 64 * 1024

CURL_PROGRAM: Final = "curl"
DEFAULT_SCHEME: Final = "http"  # what curl itself assumes for a URL without one
FORM_CONTENT_TYPE: Final = "application/x-www-form-urlencoded"
JSON_CONTENT_TYPE: Final = "application/json"
CONTENT_TYPE_HEADER: Final = "Content-Type"
ACCEPT_HEADER: Final = "Accept"
MULTIPART_CONTENT_TYPE: Final = "multipart/form-data"
LOCAL_FILE_PREFIX: Final = "@"
FILE_CONTENT_PREFIX: Final = "<"
END_OF_OPTIONS: Final = "--"

_LINE_CONTINUATION: Final = re.compile(r"\\\r?\n")
_WINDOWS_CONTINUATION: Final = re.compile(r"[\^`]\s*$", re.MULTILINE)
_FORM_PAIR: Final = re.compile(r"^[^=&\s]+=[^&]*$")

CANNOT_IMPORT: Final = "This cURL command cannot become a check. Each problem is named below."


class Action(Enum):
    METHOD = auto()
    HEADER = auto()
    DATA = auto()
    DATA_RAW = auto()
    DATA_URLENCODE = auto()
    JSON = auto()
    FORM = auto()
    FORM_STRING = auto()
    USER = auto()
    FOLLOW_REDIRECTS = auto()
    DATA_INTO_QUERY = auto()
    HEAD = auto()
    URL = auto()
    USER_AGENT = auto()
    COOKIE = auto()
    REFERER = auto()
    BEARER = auto()
    MAX_TIME = auto()
    UPLOAD_FILE = auto()
    # Changes what curl prints or saves, never the request a check sends.
    NO_EFFECT = auto()
    UNSUPPORTED = auto()


@dataclass(frozen=True, slots=True)
class CurlOption:
    names: tuple[str, ...]
    takes_value: bool
    action: Action
    reason: str = ""


_PROXY: Final = "a check is sent from nexovue's probe locations, never through a proxy"
_TLS: Final = "client certificates and custom TLS settings are not supported"
_NETWORK: Final = "a check resolves and connects from its probe location"
_HTTP_VERSION: Final = "the HTTP version is chosen by the probe"
_AUTH: Final = "only basic, bearer and API-key authentication are supported"
_RETRY: Final = "retries are set on the check itself"

# fmt: off
OPTIONS: Final[tuple[CurlOption, ...]] = (
    CurlOption(("-X", "--request"), True, Action.METHOD),
    CurlOption(("-H", "--header"), True, Action.HEADER),
    CurlOption(("-d", "--data", "--data-ascii", "--data-binary"), True, Action.DATA),
    CurlOption(("--data-raw",), True, Action.DATA_RAW),
    CurlOption(("--data-urlencode",), True, Action.DATA_URLENCODE),
    CurlOption(("--json",), True, Action.JSON),
    CurlOption(("-F", "--form"), True, Action.FORM),
    CurlOption(("--form-string",), True, Action.FORM_STRING),
    CurlOption(("-u", "--user"), True, Action.USER),
    CurlOption(("-L", "--location"), False, Action.FOLLOW_REDIRECTS),
    CurlOption(("-G", "--get"), False, Action.DATA_INTO_QUERY),
    CurlOption(("-I", "--head"), False, Action.HEAD),
    CurlOption(("--url",), True, Action.URL),
    CurlOption(("-A", "--user-agent"), True, Action.USER_AGENT),
    CurlOption(("-b", "--cookie"), True, Action.COOKIE),
    CurlOption(("-e", "--referer"), True, Action.REFERER),
    CurlOption(("--oauth2-bearer",), True, Action.BEARER),
    CurlOption(("-m", "--max-time"), True, Action.MAX_TIME),
    CurlOption(("-T", "--upload-file"), True, Action.UPLOAD_FILE),
    CurlOption(
        (
            "-s", "--silent", "-S", "--show-error", "-v", "--verbose", "-i", "--include",
            "-f", "--fail", "--fail-with-body", "-#", "--progress-bar", "--no-progress-meter",
            "-N", "--no-buffer", "-g", "--globoff", "--basic", "-O", "--remote-name",
            "-J", "--remote-header-name", "-R", "--remote-time", "--create-dirs",
            # The probe does not ask for compression, so the response is read either way.
            "--compressed",
        ),
        False,
        Action.NO_EFFECT,
    ),
    CurlOption(
        ("-o", "--output", "-w", "--write-out", "-D", "--dump-header", "--trace",
         "--trace-ascii", "--stderr", "-c", "--cookie-jar"),
        True,
        Action.NO_EFFECT,
    ),
    CurlOption(
        ("-k", "--insecure"),
        False,
        Action.UNSUPPORTED,
        "certificate verification cannot be turned off: a check always verifies the "
        "certificate, so a site with an invalid one is reported as failing",
    ),
    CurlOption(
        ("--digest", "--ntlm", "--negotiate", "--anyauth"), False, Action.UNSUPPORTED, _AUTH
    ),
    CurlOption(("--aws-sigv4",), True, Action.UNSUPPORTED, "request signing is not supported"),
    CurlOption(
        ("-x", "--proxy", "-U", "--proxy-user", "--noproxy", "--socks4", "--socks5",
         "--socks5-hostname", "--preproxy"),
        True,
        Action.UNSUPPORTED,
        _PROXY,
    ),
    CurlOption(("-p", "--proxytunnel", "--proxy-insecure"), False, Action.UNSUPPORTED, _PROXY),
    CurlOption(
        ("--cacert", "--capath", "-E", "--cert", "--key", "--cert-type", "--key-type",
         "--pinnedpubkey", "--ciphers", "--tls-max", "--pass", "--crlfile"),
        True,
        Action.UNSUPPORTED,
        _TLS,
    ),
    CurlOption(
        ("--tlsv1", "--tlsv1.0", "--tlsv1.1", "--tlsv1.2", "--tlsv1.3", "-2", "--sslv2",
         "-3", "--sslv3", "--ssl", "--ssl-reqd", "--cert-status", "--false-start"),
        False,
        Action.UNSUPPORTED,
        _TLS,
    ),
    CurlOption(
        ("--resolve", "--connect-to", "--interface", "--dns-servers", "--local-port",
         "--unix-socket", "--abstract-unix-socket"),
        True,
        Action.UNSUPPORTED,
        _NETWORK,
    ),
    CurlOption(("-4", "--ipv4", "-6", "--ipv6"), False, Action.UNSUPPORTED, _NETWORK),
    CurlOption(
        ("--http0.9", "--http1.0", "-0", "--http1.1", "--http2", "--http2-prior-knowledge",
         "--http3", "--http3-only"),
        False,
        Action.UNSUPPORTED,
        _HTTP_VERSION,
    ),
    CurlOption(("--retry", "--retry-delay", "--retry-max-time"), True, Action.UNSUPPORTED, _RETRY),
    CurlOption(
        ("--retry-connrefused", "--retry-all-errors"), False, Action.UNSUPPORTED, _RETRY
    ),
    CurlOption(
        ("--connect-timeout",),
        True,
        Action.UNSUPPORTED,
        "the check's one time limit covers connecting; set it on the check",
    ),
    CurlOption(
        ("--max-redirs",), True, Action.UNSUPPORTED, "redirects follow the probe's own limit"
    ),
    CurlOption(
        ("--location-trusted",),
        False,
        Action.UNSUPPORTED,
        "credentials are never sent on to a host a redirect leads to; use -L",
    ),
    CurlOption(
        ("--post301", "--post302", "--post303", "--path-as-is", "--raw", "--tr-encoding",
         "-j", "--junk-session-cookies", "--tcp-nodelay", "--tcp-fastopen", "-Z", "--parallel",
         "-n", "--netrc", "--netrc-optional", "-l", "--list-only"),
        False,
        Action.UNSUPPORTED,
        "not supported by a check",
    ),
    CurlOption(
        ("-K", "--config", "--netrc-file", "-r", "--range", "--limit-rate", "--max-filesize",
         "-z", "--time-cond", "-C", "--continue-at", "-Y", "--speed-limit", "-y",
         "--speed-time", "--expect100-timeout", "--variable", "-Q", "--quote"),
        True,
        Action.UNSUPPORTED,
        "not supported by a check",
    ),
)
# fmt: on

_BY_NAME: Final[dict[str, CurlOption]] = {
    name: option for option in OPTIONS for name in option.names
}


@dataclass
class _Command:
    """What the options said, before it is judged as a request."""

    method: str | None = None
    urls: list[str] = field(default_factory=list)
    headers: list[tuple[str, str]] = field(default_factory=list)
    data: list[str] = field(default_factory=list)
    json: list[str] = field(default_factory=list)
    form_fields: list[tuple[str, str]] = field(default_factory=list)
    user: str | None = None
    bearer: str | None = None
    follow_redirects: bool = False
    data_into_query: bool = False
    head: bool = False
    uploads_file: bool = False
    sends_form_file: bool = False
    sends_body_from_file: bool = False
    timeout_seconds: int | None = None
    needs_values: list[str] = field(default_factory=list)
    needs_files: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def preview(text: str) -> ImportPlan:
    """One check from one command, or `InvalidRequestError` saying why not."""
    command = _read(_words(text))
    url = _chosen_url(command)
    method = _method(command)
    builder = plan.PlanBuilder("curl", plan.check_name(f"{method} {_short_url(url)}"))
    for item, reason in command.skipped:
        builder.skip(item, reason)

    url = _without_url_credentials(url, command, builder)
    parts = plan.RequestParts(
        method=method,
        url=url,
        headers=list(command.headers),
        needs_values=list(command.needs_values),
        needs_files=list(command.needs_files),
    )
    _apply_credentials(command, parts, builder)
    plan.protect_credentials(parts, builder, found_in="the cURL command")
    query, body = _query_and_body(command, parts)

    try:
        config = ApiCheckConfig(
            request=HttpRequest(
                method=method,
                url=url,
                query=query,
                headers=plan.headers_of(parts, builder, item="cURL"),
                body=body,
                auth=parts.auth,
                assertions=plan.status_below_error(),
                follow_redirects=command.follow_redirects,
                timeout_seconds=command.timeout_seconds or DEFAULT_TIMEOUT_SECONDS,
            )
        )
    except ValidationError as refused:
        raise InvalidRequestError(CANNOT_IMPORT, errors=_problems(refused)) from refused

    builder.add(
        key=f"{method} {url}",
        name=builder.title,
        method=method,
        url=url,
        build_config=lambda: config,
        needs_values=parts.needs_values,
        needs_files=parts.needs_files,
    )
    return builder.build()


# --- words and options ------------------------------------------------------------


def _words(text: str) -> list[str]:
    if len(text) > MAX_CURL_CHARACTERS:
        raise InvalidRequestError(
            f"This cURL command is longer than {MAX_CURL_CHARACTERS // 1024} KB, which is the "
            "most an import reads. Move a large body into a file and attach it instead."
        )
    if _WINDOWS_CONTINUATION.search(text):
        raise InvalidRequestError(
            "This looks like a Windows command-prompt or PowerShell copy. Copy the command "
            "as cURL (bash) instead — in a browser, that is 'Copy as cURL (bash)'."
        )
    try:
        words = shlex.split(_LINE_CONTINUATION.sub(" ", text), posix=True)
    except ValueError as unbalanced:
        raise InvalidRequestError(
            f"This cURL command could not be read: {unbalanced}. Check its quotes."
        ) from unbalanced
    if not words or words[0] != CURL_PROGRAM:
        raise InvalidRequestError("A cURL import must be a command that starts with curl.")
    return words[1:]


def _options(words: list[str]) -> Iterator[tuple[str, CurlOption | None, str]]:
    """(name as written, the option or None when unknown, its value or "")."""
    position = 0
    while position < len(words):
        word = words[position]
        position += 1
        if word == END_OF_OPTIONS:
            for positional in words[position:]:
                yield positional, None, positional
            return
        if not word.startswith("-") or word == "-":
            yield word, None, word
        elif word.startswith("--"):
            option = _BY_NAME.get(word)
            value = ""
            if option is not None and option.takes_value:
                value, position = _value_after(words, position, word)
            yield word, option, value
        else:
            position = yield from _short_cluster(words, position, word)


def _short_cluster(
    words: list[str], position: int, word: str
) -> Generator[tuple[str, CurlOption | None, str], None, int]:
    """`-sSL` is three flags; `-XPOST` is one option and its value."""
    letters = word[1:]
    for index, letter in enumerate(letters):
        name = f"-{letter}"
        option = _BY_NAME.get(name)
        if option is None or not option.takes_value:
            yield name, option, ""
            continue
        attached = letters[index + 1 :]
        if attached:
            yield name, option, attached
        else:
            value, position = _value_after(words, position, name)
            yield name, option, value
        break
    return position


def _value_after(words: list[str], position: int, name: str) -> tuple[str, int]:
    if position >= len(words):
        raise InvalidRequestError(f"The cURL option {name} needs a value and has none.")
    return words[position], position + 1


def _read(words: list[str]) -> _Command:
    command = _Command()
    for name, option, value in _options(words):
        if option is None:
            if name.startswith("-") and name != "-":
                command.skipped.append((name, "not an option this import recognises"))
            else:
                command.urls.append(value)
            continue
        _apply(command, name, option, value)
    return command


def _apply(command: _Command, name: str, option: CurlOption, value: str) -> None:
    match option.action:
        case Action.METHOD:
            command.method = value
        case Action.HEADER:
            _add_header(command, name, value)
        case Action.DATA:
            _add_data(command, value, reads_files=True)
        case Action.DATA_RAW:
            command.data.append(value)
        case Action.DATA_URLENCODE:
            _add_urlencoded(command, value)
        case Action.JSON:
            if _is_local_file(value):
                _needs_body_file(command, value[1:])
            else:
                command.json.append(value)
        case Action.FORM:
            _add_form_part(command, name, value)
        case Action.FORM_STRING:
            field_name, separator, field_value = value.partition("=")
            if separator and field_name:
                command.form_fields.append((field_name, field_value))
            else:
                command.skipped.append((f"{name} {value}", "not a name=value form field"))
        case Action.USER:
            command.user = value
        case Action.FOLLOW_REDIRECTS:
            command.follow_redirects = True
        case Action.DATA_INTO_QUERY:
            command.data_into_query = True
        case Action.HEAD:
            command.head = True
        case Action.URL:
            command.urls.append(value)
        case Action.USER_AGENT:
            command.headers.append(("User-Agent", value))
        case Action.COOKIE:
            if "=" in value:
                command.headers.append(("Cookie", value))
            else:
                command.skipped.append((f"{name} {value}", "reads cookies from a local file"))
        case Action.REFERER:
            referer = value.removesuffix(";auto")
            if referer:
                command.headers.append(("Referer", referer))
        case Action.BEARER:
            command.bearer = value
        case Action.MAX_TIME:
            _set_timeout(command, name, value)
        case Action.UPLOAD_FILE:
            command.uploads_file = True
            _needs_body_file(command, value)
        case Action.NO_EFFECT:
            pass
        case Action.UNSUPPORTED:
            written = f"{name} {value}" if option.takes_value else name
            command.skipped.append((written, option.reason))


def _add_header(command: _Command, name: str, value: str) -> None:
    if _is_local_file(value):
        command.skipped.append((f"{name} {value}", "reads headers from a local file"))
        return
    header_name, separator, header_value = value.partition(":")
    header_name = header_name.strip()
    if separator and header_value.strip():
        command.headers.append((header_name, header_value.strip()))
    elif not separator and header_name.endswith(";"):
        command.headers.append((header_name.removesuffix(";"), ""))
    elif separator:
        command.skipped.append(
            (f"{name} {value}", "removes a header curl adds itself; a check does not add it")
        )
    else:
        command.skipped.append((f"{name} {value}", "not a 'Name: value' header"))


def _add_data(command: _Command, value: str, *, reads_files: bool) -> None:
    if reads_files and _is_local_file(value):
        _needs_body_file(command, value[1:])
    else:
        command.data.append(value)


def _add_urlencoded(command: _Command, value: str) -> None:
    """curl's five forms: `content`, `=content`, `name=content`, `@file`, `name@file`."""
    if "=" in value:
        name, _, content = value.partition("=")
        command.data.append(f"{name}={quote_plus(content)}" if name else quote_plus(content))
        return
    name, separator, path = value.partition(LOCAL_FILE_PREFIX)
    if separator:
        _needs_body_file(command, path)
    else:
        command.data.append(quote_plus(name))


def _add_form_part(command: _Command, name: str, value: str) -> None:
    field_name, separator, field_value = value.partition("=")
    if not separator or not field_name:
        command.skipped.append((f"{name} {value}", "not a name=value form field"))
    elif field_value.startswith((LOCAL_FILE_PREFIX, FILE_CONTENT_PREFIX)):
        path = field_value[1:].split(";", 1)[0]
        command.sends_form_file = True
        command.needs_files.append(
            f"form field '{field_name}' refers to a local file {path} — attach it"
        )
    else:
        command.form_fields.append((field_name, field_value))


def _needs_body_file(command: _Command, path: str) -> None:
    command.sends_body_from_file = True
    command.needs_files.append(f"the request body is the local file {path} — attach it")


def _set_timeout(command: _Command, name: str, value: str) -> None:
    try:
        seconds = math.ceil(float(value))
    except ValueError:
        command.skipped.append((f"{name} {value}", "not a number of seconds"))
        return
    bounded = min(max(seconds, MIN_TIMEOUT_SECONDS), MAX_REQUEST_TIMEOUT_SECONDS)
    command.timeout_seconds = bounded
    if bounded != seconds:
        command.skipped.append(
            (
                f"{name} {value}",
                f"a request may wait {MIN_TIMEOUT_SECONDS} to {MAX_REQUEST_TIMEOUT_SECONDS} "
                f"seconds; the check uses {bounded}",
            )
        )


def _is_local_file(value: str) -> bool:
    return value.startswith(LOCAL_FILE_PREFIX) and len(value) > 1


# --- the request ------------------------------------------------------------------


def _chosen_url(command: _Command) -> str:
    """The first word that looks like a URL. Anything else is listed, not guessed at."""
    if not command.urls:
        raise InvalidRequestError("This cURL command has no URL to check.")
    position = next((index for index, url in enumerate(command.urls) if "://" in url), 0)
    chosen = command.urls[position]
    for index, other in enumerate(command.urls):
        if index != position:
            command.skipped.append((other, "only one URL is imported from a command"))
    return chosen if "://" in chosen else f"{DEFAULT_SCHEME}://{chosen}"


def _method(command: _Command) -> str:
    if command.method:
        return command.method
    if command.head:
        return "HEAD"
    if command.data_into_query:
        return "GET"
    if command.uploads_file:
        return "PUT"
    if command.data or command.json or command.form_fields or command.sends_form_file:
        return "POST"
    if command.sends_body_from_file:
        return "POST"
    return "GET"


def _short_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.hostname or ''}{parts.path}" or url


def _without_url_credentials(url: str, command: _Command, builder: plan.PlanBuilder) -> str:
    """`https://user:pass@host/` carries a password in the URL itself (BR10)."""
    parts = urlsplit(url)
    if parts.username is None:
        return url
    host = parts.netloc.rpartition("@")[2]
    if command.user is None:
        credentials = parts.netloc.rpartition("@")[0]
        command.user = unquote(credentials)
    else:
        builder.skip("credentials in the URL", "-u already names the user; the URL's are unused")
    return urlunsplit(parts._replace(netloc=host))


def _apply_credentials(
    command: _Command, parts: plan.RequestParts, builder: plan.PlanBuilder
) -> None:
    host = plan.host_label(parts.url)
    if command.user is not None:
        username, has_password, password = command.user.partition(":")
        if not username:
            builder.skip("-u", "has no user name")
        else:
            found_in = f"the cURL command: password for user {username}"
            secret = builder.secret(plan.secret_name(host, "password"), password, found_in)
            parts.auth = BasicAuth(username=username, password_secret=secret)
            if not has_password:
                parts.needs_values.append(f"the password for user {username}")
    if command.bearer is not None:
        if isinstance(parts.auth, NoAuth):
            secret = builder.secret(
                plan.secret_name(host, "token"), command.bearer, "the cURL command: bearer token"
            )
            parts.auth = BearerAuth(token_secret=secret)
        else:
            builder.skip("--oauth2-bearer", "a request has one authentication; -u is used")


def _query_and_body(
    command: _Command, parts: plan.RequestParts
) -> tuple[list[NameValue], RequestBody]:
    if command.data_into_query:
        query = [
            NameValue(name=name, value=value) for name, value in _pairs("&".join(command.data))
        ]
        return query, NoBody()
    kinds = sum(bool(kind) for kind in (command.data, command.json, command.form_fields))
    if kinds > 1:
        raise InvalidRequestError(
            "This cURL command mixes -d, --json and -F, which curl itself does not allow."
        )
    if command.json:
        if not _header(parts, ACCEPT_HEADER):
            parts.headers.append((ACCEPT_HEADER, JSON_CONTENT_TYPE))
        return [], JsonBody(content="".join(command.json))
    if command.form_fields or command.sends_form_file:
        _drop_multipart_content_type(parts)
        fields = [MultipartField(name=name, value=value) for name, value in command.form_fields]
        return [], MultipartBody(parts=fields)
    if command.data:
        return [], _data_body("&".join(command.data), _header(parts, CONTENT_TYPE_HEADER))
    return [], NoBody()


def _data_body(content: str, content_type: str | None) -> RequestBody:
    lowered = (content_type or "").lower()
    stripped = content.lstrip()
    if "json" in lowered or (not content_type and stripped.startswith(("{", "["))):
        return JsonBody(content=content)
    if "xml" in lowered:
        return XmlBody(content=content)
    if (not content_type or lowered.startswith(FORM_CONTENT_TYPE)) and _is_form(content):
        return FormBody(
            fields=[NameValue(name=name, value=value) for name, value in _pairs(content)]
        )
    return TextBody(content=content, content_type=content_type or FORM_CONTENT_TYPE)


def _is_form(content: str) -> bool:
    return all(_FORM_PAIR.match(pair) for pair in content.split("&"))


def _pairs(content: str) -> list[tuple[str, str]]:
    pairs = []
    for piece in content.split("&"):
        if not piece:
            continue
        name, _, value = piece.partition("=")
        pairs.append((unquote_plus(name), unquote_plus(value)))
    return pairs


def _header(parts: plan.RequestParts, name: str) -> str | None:
    return next((value for key, value in parts.headers if key.lower() == name.lower()), None)


def _drop_multipart_content_type(parts: plan.RequestParts) -> None:
    """A typed `multipart/form-data` header has no boundary; the probe writes its own."""
    parts.headers = [
        (name, value)
        for name, value in parts.headers
        if not (
            name.lower() == CONTENT_TYPE_HEADER.lower()
            and value.lower().startswith(MULTIPART_CONTENT_TYPE)
        )
    ]


def _problems(error: ValidationError) -> list[dict[str, str]]:
    return [
        {"field": ".".join(str(part) for part in detail["loc"]), "reason": detail["msg"]}
        for detail in error.errors()
    ]
