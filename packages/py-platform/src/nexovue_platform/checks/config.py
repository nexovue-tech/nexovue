"""The configuration of every kind of check, validated before it is stored.

`extra="forbid"` throughout: a field this build does not know is refused rather
than stored and silently ignored, which is how a check comes to look configured
and never do what its author wrote.

What is decided here is the *shape*. Rules that need the project — whether a
secret exists, whether a file belongs to it, whether a target is public — are the
API's, because only the API can answer them.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from typing import Annotated, Final, Literal, Self

import re2
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from nexovue_platform.checks.jsonpath import compile_json_path
from nexovue_platform.checks.templating import (
    SECRET_NAME_PATTERN,
    VARIABLE_NAME_PATTERN,
    references_in,
)

CheckType = Literal["api", "multistep", "heartbeat", "tcp", "dns"]
CHECK_TYPES: Final[tuple[CheckType, ...]] = ("api", "multistep", "heartbeat", "tcp", "dns")

MAX_URL_LENGTH: Final = 2048
MAX_NAME_VALUE_PAIRS: Final = 50
MAX_FIELD_NAME_LENGTH: Final = 256
MAX_FIELD_VALUE_LENGTH: Final = 8192
# A body typed or pasted into the editor. Files are how larger payloads travel.
MAX_INLINE_BODY_CHARACTERS: Final = 256 * 1024
MAX_ASSERTIONS: Final = 20
MAX_EXTRACTIONS: Final = 10
MAX_STEPS: Final = 10
MAX_STEP_NAME_LENGTH: Final = 120
MAX_FILES_PER_REQUEST: Final = 3
MAX_MULTIPART_PARTS: Final = 50
MAX_METHOD_LENGTH: Final = 32
MAX_EXPRESSION_LENGTH: Final = 512
MAX_HOST_LENGTH: Final = 253
MAX_DNS_ASSERTIONS: Final = 10

MIN_TIMEOUT_SECONDS: Final = 1
MAX_REQUEST_TIMEOUT_SECONDS: Final = 30
DEFAULT_TIMEOUT_SECONDS: Final = 10
# A whole multistep run must finish inside the probe task's time limit.
MAX_RUN_SECONDS: Final = 60

MIN_GRACE_SECONDS: Final = 0
MAX_GRACE_SECONDS: Final = 86_400
DEFAULT_GRACE_SECONDS: Final = 300

# RFC 9110 `token`: the grammar of a method and of a header name. It excludes
# spaces and control characters, so neither can split a request line.
_TOKEN: Final = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_LINE_BREAKS: Final = re.compile(r"[\r\n\x00]")
_WHITESPACE: Final = re.compile(r"\s")
_HOSTNAME: Final = re.compile(r"^[A-Za-z0-9._:\-\[\]]+$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _refuse_line_breaks(value: str) -> str:
    if _LINE_BREAKS.search(value):
        raise ValueError("must not contain line breaks")
    return value


SecretName = Annotated[str, Field(pattern=SECRET_NAME_PATTERN.pattern)]
VariableName = Annotated[str, Field(pattern=VARIABLE_NAME_PATTERN.pattern)]


class NameValue(_Model):
    """A query parameter, header or form field. `enabled` lets a row be kept and
    switched off, as Postman does, without deleting what was typed."""

    name: Annotated[str, Field(min_length=1, max_length=MAX_FIELD_NAME_LENGTH)]
    value: Annotated[str, Field(max_length=MAX_FIELD_VALUE_LENGTH)] = ""
    enabled: bool = True

    _no_breaks = field_validator("name", "value")(_refuse_line_breaks)


class Header(NameValue):
    @field_validator("name")
    @classmethod
    def _is_a_field_name(cls, name: str) -> str:
        if not _TOKEN.match(name):
            raise ValueError("a header name may contain only letters, digits and !#$%&'*+-.^_`|~")
        return name

    @field_validator("value")
    @classmethod
    def _is_ascii(cls, value: str) -> str:
        # libcurl sends header bytes as given; a value outside ASCII has no one
        # encoding every server agrees on, so it is refused rather than guessed.
        if not value.isascii():
            raise ValueError("a header value may contain only ASCII characters")
        return value


# --- bodies -------------------------------------------------------------------


class NoBody(_Model):
    kind: Literal["none"] = "none"


class TextBody(_Model):
    kind: Literal["text"] = "text"
    content: Annotated[str, Field(max_length=MAX_INLINE_BODY_CHARACTERS)] = ""
    content_type: Annotated[str, Field(min_length=1, max_length=MAX_FIELD_NAME_LENGTH)] = (
        "text/plain"
    )

    _no_breaks = field_validator("content_type")(_refuse_line_breaks)


class JsonBody(_Model):
    """Kept as text: `{{order_id}}` inside it is not JSON until it is substituted."""

    kind: Literal["json"] = "json"
    content: Annotated[str, Field(max_length=MAX_INLINE_BODY_CHARACTERS)] = ""


class XmlBody(_Model):
    kind: Literal["xml"] = "xml"
    content: Annotated[str, Field(max_length=MAX_INLINE_BODY_CHARACTERS)] = ""


class FormBody(_Model):
    kind: Literal["form"] = "form"
    fields: Annotated[list[NameValue], Field(max_length=MAX_NAME_VALUE_PAIRS)] = []


class MultipartField(_Model):
    kind: Literal["field"] = "field"
    name: Annotated[str, Field(min_length=1, max_length=MAX_FIELD_NAME_LENGTH)]
    value: Annotated[str, Field(max_length=MAX_FIELD_VALUE_LENGTH)] = ""

    _no_breaks = field_validator("name")(_refuse_line_breaks)


class MultipartFile(_Model):
    kind: Literal["file"] = "file"
    name: Annotated[str, Field(min_length=1, max_length=MAX_FIELD_NAME_LENGTH)]
    file_id: uuid.UUID

    _no_breaks = field_validator("name")(_refuse_line_breaks)


MultipartPart = Annotated[MultipartField | MultipartFile, Field(discriminator="kind")]


class MultipartBody(_Model):
    kind: Literal["multipart"] = "multipart"
    parts: Annotated[list[MultipartPart], Field(max_length=MAX_MULTIPART_PARTS)] = []

    @model_validator(mode="after")
    def _files_within_limit(self) -> Self:
        files = sum(1 for part in self.parts if isinstance(part, MultipartFile))
        if files > MAX_FILES_PER_REQUEST:
            raise ValueError(f"a request may send at most {MAX_FILES_PER_REQUEST} files")
        return self


class BinaryBody(_Model):
    kind: Literal["binary"] = "binary"
    file_id: uuid.UUID
    content_type: Annotated[str, Field(min_length=1, max_length=MAX_FIELD_NAME_LENGTH)] = (
        "application/octet-stream"
    )

    _no_breaks = field_validator("content_type")(_refuse_line_breaks)


RequestBody = Annotated[
    NoBody | TextBody | JsonBody | XmlBody | FormBody | MultipartBody | BinaryBody,
    Field(discriminator="kind"),
]


# --- authentication -----------------------------------------------------------


class NoAuth(_Model):
    kind: Literal["none"] = "none"


class BasicAuth(_Model):
    kind: Literal["basic"] = "basic"
    username: Annotated[str, Field(min_length=1, max_length=MAX_FIELD_NAME_LENGTH)]
    password_secret: SecretName

    _no_breaks = field_validator("username")(_refuse_line_breaks)


class BearerAuth(_Model):
    kind: Literal["bearer"] = "bearer"
    token_secret: SecretName


class ApiKeyAuth(_Model):
    kind: Literal["api_key"] = "api_key"
    name: Annotated[str, Field(min_length=1, max_length=MAX_FIELD_NAME_LENGTH)]
    value_secret: SecretName
    location: Literal["header", "query"] = "header"

    @model_validator(mode="after")
    def _header_name_is_a_token(self) -> Self:
        if self.location == "header" and not _TOKEN.match(self.name):
            raise ValueError("a header name may contain only letters, digits and !#$%&'*+-.^_`|~")
        _refuse_line_breaks(self.name)
        return self


RequestAuth = Annotated[NoAuth | BasicAuth | BearerAuth | ApiKeyAuth, Field(discriminator="kind")]


# --- assertions and extraction ------------------------------------------------

AssertionSource = Literal[
    "status_code", "header", "body", "json_path", "response_time", "tls_days_remaining"
]
Comparison = Literal[
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "less_than",
    "greater_than",
    "matches",
    "is_empty",
    "is_not_empty",
]

NUMERIC_SOURCES: Final = frozenset({"status_code", "response_time", "tls_days_remaining"})
NUMERIC_COMPARISONS: Final = frozenset({"equals", "not_equals", "less_than", "greater_than"})
TEXT_COMPARISONS: Final = frozenset(
    {"equals", "not_equals", "contains", "not_contains", "matches", "is_empty", "is_not_empty"}
)
ORDERING_COMPARISONS: Final = frozenset({"less_than", "greater_than"})
TARGETLESS_COMPARISONS: Final = frozenset({"is_empty", "is_not_empty"})
SOURCES_WITH_PROPERTY: Final = frozenset({"header", "json_path"})


def validate_json_path(expression: str) -> str:
    compile_json_path(expression)
    return expression


def validate_pattern(expression: str) -> str:
    """RE2, not `re`: its matching is linear in the input, so a pattern a customer
    typed cannot hold a probe worker with catastrophic backtracking."""
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError(f"a pattern may be at most {MAX_EXPRESSION_LENGTH} characters")
    try:
        re2.compile(expression)
    except re2.error as unparseable:
        raise ValueError(f"not a valid regular expression: {unparseable}") from unparseable
    return expression


class Assertion(_Model):
    """What a healthy response looks like, one fact at a time. Never code."""

    source: AssertionSource
    # The header name or the JSON path; empty for sources that need neither.
    property: Annotated[str, Field(max_length=MAX_EXPRESSION_LENGTH)] = ""
    comparison: Comparison
    target: Annotated[str, Field(max_length=MAX_FIELD_VALUE_LENGTH)] = ""

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.source in SOURCES_WITH_PROPERTY and not self.property:
            raise ValueError(f"a {self.source} assertion needs a {_property_label(self.source)}")
        if self.source not in SOURCES_WITH_PROPERTY and self.property:
            raise ValueError(f"a {self.source} assertion takes no property")
        if self.source == "json_path":
            validate_json_path(self.property)

        if self.source in NUMERIC_SOURCES and self.comparison not in NUMERIC_COMPARISONS:
            raise ValueError(f"{self.source} can only be compared as a number")
        if self.source in ("header", "body") and self.comparison not in TEXT_COMPARISONS:
            raise ValueError(f"{self.source} can only be compared as text")

        if self.comparison in TARGETLESS_COMPARISONS:
            if self.target:
                raise ValueError(f"'{self.comparison}' takes no value")
        elif self.target == "" and self.comparison not in ("equals", "not_equals"):
            raise ValueError("this comparison needs a value")

        if self.comparison in ORDERING_COMPARISONS or self.source in NUMERIC_SOURCES:
            try:
                float(self.target)
            except ValueError as not_a_number:
                raise ValueError("this comparison needs a number") from not_a_number
        if self.comparison == "matches":
            validate_pattern(self.target)
        return self


def _property_label(source: str) -> str:
    return "header name" if source == "header" else "JSON path"


class Extraction(_Model):
    """A value taken from a response into a variable the next request can use."""

    name: VariableName
    source: Literal["json_path", "header", "regex"]
    expression: Annotated[str, Field(min_length=1, max_length=MAX_EXPRESSION_LENGTH)]

    @model_validator(mode="after")
    def _expression_parses(self) -> Self:
        if self.source == "json_path":
            validate_json_path(self.expression)
        elif self.source == "regex":
            validate_pattern(self.expression)
        return self


# --- requests -----------------------------------------------------------------


class HttpRequest(_Model):
    """One request, as the editor's tabs describe it."""

    name: Annotated[str, Field(max_length=MAX_STEP_NAME_LENGTH)] = ""
    method: Annotated[str, Field(min_length=1, max_length=MAX_METHOD_LENGTH)] = "GET"
    url: Annotated[str, Field(min_length=1, max_length=MAX_URL_LENGTH)]
    query: Annotated[list[NameValue], Field(max_length=MAX_NAME_VALUE_PAIRS)] = []
    headers: Annotated[list[Header], Field(max_length=MAX_NAME_VALUE_PAIRS)] = []
    body: RequestBody = NoBody()
    auth: RequestAuth = NoAuth()
    assertions: Annotated[list[Assertion], Field(min_length=1, max_length=MAX_ASSERTIONS)]
    extractions: Annotated[list[Extraction], Field(max_length=MAX_EXTRACTIONS)] = []
    follow_redirects: bool = True
    timeout_seconds: Annotated[
        int, Field(ge=MIN_TIMEOUT_SECONDS, le=MAX_REQUEST_TIMEOUT_SECONDS)
    ] = DEFAULT_TIMEOUT_SECONDS

    @field_validator("method")
    @classmethod
    def _method_is_a_token(cls, method: str) -> str:
        if not _TOKEN.match(method):
            raise ValueError("a method may contain only letters, digits and !#$%&'*+-.^_`|~")
        return method

    @field_validator("url")
    @classmethod
    def _url_is_one_line(cls, url: str) -> str:
        if _WHITESPACE.search(url):
            raise ValueError("a URL may not contain spaces or line breaks")
        return url

    def file_ids(self) -> list[uuid.UUID]:
        if isinstance(self.body, BinaryBody):
            return [self.body.file_id]
        if isinstance(self.body, MultipartBody):
            return [part.file_id for part in self.body.parts if isinstance(part, MultipartFile)]
        return []

    def texts(self) -> Iterator[str]:
        """Every string a reference may appear in."""
        yield self.url
        for pair in (*self.query, *self.headers):
            yield pair.name
            yield pair.value
        body = self.body
        if isinstance(body, TextBody | JsonBody | XmlBody):
            yield body.content
        elif isinstance(body, FormBody):
            for field in body.fields:
                yield field.name
                yield field.value
        elif isinstance(body, MultipartBody):
            for part in body.parts:
                if isinstance(part, MultipartField):
                    yield part.value
        if isinstance(self.auth, BasicAuth):
            yield self.auth.username
        for assertion in self.assertions:
            yield assertion.target

    def secret_names(self) -> frozenset[str]:
        names = {name for text in self.texts() for name in references_in(text).secrets}
        auth = self.auth
        if isinstance(auth, BasicAuth):
            names.add(auth.password_secret)
        elif isinstance(auth, BearerAuth):
            names.add(auth.token_secret)
        elif isinstance(auth, ApiKeyAuth):
            names.add(auth.value_secret)
        return frozenset(names)

    def variable_names(self) -> frozenset[str]:
        return frozenset(name for text in self.texts() for name in references_in(text).variables)


# --- the kinds ----------------------------------------------------------------


class ApiCheckConfig(_Model):
    type: Literal["api"] = "api"
    request: HttpRequest

    @model_validator(mode="after")
    def _nothing_to_extract_into(self) -> Self:
        if self.request.extractions:
            raise ValueError("values are extracted only in a multistep check")
        return self


class MultistepCheckConfig(_Model):
    type: Literal["multistep"] = "multistep"
    steps: Annotated[list[HttpRequest], Field(min_length=1, max_length=MAX_STEPS)]

    @model_validator(mode="after")
    def _fits_one_run(self) -> Self:
        if sum(step.timeout_seconds for step in self.steps) > MAX_RUN_SECONDS:
            raise ValueError(f"the steps' time limits may add up to at most {MAX_RUN_SECONDS}s")
        extracted = [extraction.name for step in self.steps for extraction in step.extractions]
        if len(extracted) != len(set(extracted)):
            raise ValueError("each extracted value needs its own name")
        return self


class HeartbeatCheckConfig(_Model):
    """The expected period is the check's interval; this is how late a call may be."""

    type: Literal["heartbeat"] = "heartbeat"
    grace_seconds: Annotated[int, Field(ge=MIN_GRACE_SECONDS, le=MAX_GRACE_SECONDS)] = (
        DEFAULT_GRACE_SECONDS
    )


def _plain_host(host: str) -> str:
    if not _HOSTNAME.match(host):
        raise ValueError("a host is a name or an address, without a scheme, path or port")
    return host


class TcpCheckConfig(_Model):
    type: Literal["tcp"] = "tcp"
    host: Annotated[str, Field(min_length=1, max_length=MAX_HOST_LENGTH)]
    port: Annotated[int, Field(ge=1, le=65_535)]
    timeout_seconds: Annotated[
        int, Field(ge=MIN_TIMEOUT_SECONDS, le=MAX_REQUEST_TIMEOUT_SECONDS)
    ] = DEFAULT_TIMEOUT_SECONDS

    _host = field_validator("host")(_plain_host)


DnsRecordType = Literal["A", "AAAA", "CNAME", "MX", "NS", "TXT"]


class DnsAssertion(_Model):
    """Held against every answer: `contains` passes when one answer equals the target."""

    comparison: Literal["contains", "not_contains", "matches"]
    target: Annotated[str, Field(min_length=1, max_length=MAX_FIELD_VALUE_LENGTH)]

    @model_validator(mode="after")
    def _pattern_parses(self) -> Self:
        if self.comparison == "matches":
            validate_pattern(self.target)
        return self


class DnsCheckConfig(_Model):
    """A name must resolve. The assertions say to what."""

    type: Literal["dns"] = "dns"
    name: Annotated[str, Field(min_length=1, max_length=MAX_HOST_LENGTH)]
    record_type: DnsRecordType = "A"
    # An address, never a name: resolving the resolver would ask the question twice.
    resolver: Annotated[str, Field(max_length=64)] | None = None
    assertions: Annotated[list[DnsAssertion], Field(max_length=MAX_DNS_ASSERTIONS)] = []
    timeout_seconds: Annotated[
        int, Field(ge=MIN_TIMEOUT_SECONDS, le=MAX_REQUEST_TIMEOUT_SECONDS)
    ] = DEFAULT_TIMEOUT_SECONDS

    _name = field_validator("name")(_plain_host)


CheckConfig = Annotated[
    ApiCheckConfig | MultistepCheckConfig | HeartbeatCheckConfig | TcpCheckConfig | DnsCheckConfig,
    Field(discriminator="type"),
]

CHECK_CONFIG: Final = TypeAdapter[CheckConfig](CheckConfig)


def parse_config(document: object) -> CheckConfig:
    """A stored or submitted configuration, or `pydantic.ValidationError`."""
    return CHECK_CONFIG.validate_python(document)


def requests_of(config: CheckConfig) -> list[HttpRequest]:
    if isinstance(config, ApiCheckConfig):
        return [config.request]
    if isinstance(config, MultistepCheckConfig):
        return list(config.steps)
    return []


def secret_names_of(config: CheckConfig) -> frozenset[str]:
    return frozenset(name for request in requests_of(config) for name in request.secret_names())


def file_ids_of(config: CheckConfig) -> list[uuid.UUID]:
    return [file_id for request in requests_of(config) for file_id in request.file_ids()]


def run_seconds_of(config: CheckConfig) -> int:
    """The longest one run of this check may take, for the task's time limit."""
    if isinstance(config, TcpCheckConfig | DnsCheckConfig):
        return config.timeout_seconds
    return sum(request.timeout_seconds for request in requests_of(config))


def undefined_variables(config: CheckConfig, defined: frozenset[str]) -> list[tuple[int, str]]:
    """(request position, name) for every variable nothing defines at that point.

    A multistep request may use what an *earlier* request extracted — never a
    later one, because it has not happened yet when this request is sent.
    """
    available = set(defined)
    missing: list[tuple[int, str]] = []
    for position, request in enumerate(requests_of(config)):
        missing.extend((position, name) for name in sorted(request.variable_names() - available))
        available.update(extraction.name for extraction in request.extractions)
    return missing
