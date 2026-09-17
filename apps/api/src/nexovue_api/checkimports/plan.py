"""What every import shares: the plan being assembled, names, credentials, assertions.

Parsers describe requests; this turns them into validated check configurations
and keeps the three promises every source makes — each check can fail (BR1),
each credential becomes a secret (BR10), and nothing is dropped silently (EC5).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

from nexovue_platform.checks.config import (
    Assertion,
    BasicAuth,
    BearerAuth,
    CheckConfig,
    Header,
    NoAuth,
    RequestAuth,
    requests_of,
)
from nexovue_platform.checks.templating import (
    SECRET_NAMESPACE,
    VARIABLE_NAME_PATTERN,
    has_references,
)
from pydantic import ValidationError

from nexovue_api.checkimports.schemas import (
    MAX_IMPORT_CHARACTERS,
    MAX_IMPORTED_CHECKS,
    DetectedSecret,
    DetectedVariable,
    ImportItem,
    ImportPlan,
    ImportSource,
    SkippedItem,
)
from nexovue_api.checks.schemas import MAX_NAME_LENGTH
from nexovue_api.errors.exceptions import InvalidRequestError

HTTP_ERROR_STATUS_FLOOR: Final = 400
TRUNCATION_MARK: Final = "…"

MAX_SECRET_NAME_LENGTH: Final = 64
# Leaves room for a `_NN` suffix when two different credentials share a name.
MAX_SECRET_NAME_BASE_LENGTH: Final = 60
MAX_VARIABLE_NAME_LENGTH: Final = 64
FALLBACK_SECRET_NAME: Final = "IMPORTED"
MAX_REPORTED_PROBLEMS: Final = 5

AUTHORIZATION_HEADER: Final = "authorization"
BEARER_SCHEME: Final = "bearer"
BASIC_SCHEME: Final = "basic"
# Header names whose value is a credential by convention. A cookie pasted from a
# browser is a signed-in session, so it is one too.
CREDENTIAL_HEADERS: Final = frozenset({"x-api-key", "api-key", "apikey", "x-auth-token", "cookie"})

_NOT_NAME_CHARACTERS: Final = re.compile(r"[^A-Za-z0-9]+")
_NOT_VARIABLE_CHARACTERS: Final = re.compile(r"[^A-Za-z0-9_.\-]+")
_HEADER_NAME: Final = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_CAMEL_BOUNDARY: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def read_json(text: str, *, what: str) -> object:
    """A JSON document, or `InvalidRequestError` naming where it stopped parsing (FR15)."""
    if len(text) > MAX_IMPORT_CHARACTERS:
        raise InvalidRequestError(
            f"This {what} is larger than {MAX_IMPORT_CHARACTERS // (1024 * 1024)} MB, "
            "which is the most an import may read."
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as unparseable:
        raise InvalidRequestError(
            f"This {what} could not be read as JSON at line {unparseable.lineno}, "
            f"column {unparseable.colno}: {unparseable.msg}."
        ) from unparseable
    except RecursionError as too_deep:
        raise InvalidRequestError(f"This {what} is nested too deeply to read.") from too_deep


def status_below_error() -> list[Assertion]:
    """The assertion a cURL command or Postman request gets: it did not fail."""
    return [
        Assertion(source="status_code", comparison="less_than", target=str(HTTP_ERROR_STATUS_FLOOR))
    ]


def check_name(text: str) -> str:
    """A check name within the limit a save enforces."""
    cleaned = " ".join(text.split()) or "Imported check"
    if len(cleaned) <= MAX_NAME_LENGTH:
        return cleaned
    return cleaned[: MAX_NAME_LENGTH - len(TRUNCATION_MARK)] + TRUNCATION_MARK


def secret_name(*parts: str) -> str:
    """`api.example.com` + `token` → `API_EXAMPLE_COM_TOKEN`, valid as a secret name."""
    words = [_CAMEL_BOUNDARY.sub("_", part) for part in parts]
    joined = _NOT_NAME_CHARACTERS.sub("_", "_".join(words)).strip("_").upper()
    if not joined:
        joined = FALLBACK_SECRET_NAME
    if joined[0].isdigit():
        joined = f"{FALLBACK_SECRET_NAME}_{joined}"
    return joined[:MAX_SECRET_NAME_BASE_LENGTH].rstrip("_")


def variable_name(raw: str) -> str:
    """A name valid as `{{name}}`, keeping the original where it already is."""
    if VARIABLE_NAME_PATTERN.match(raw):
        return raw
    cleaned = _NOT_VARIABLE_CHARACTERS.sub("_", raw).strip("_") or "value"
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"_{cleaned}"
    return cleaned[:MAX_VARIABLE_NAME_LENGTH]


def secret_reference(name: str) -> str:
    return f"{{{{{SECRET_NAMESPACE}.{name}}}}}"


def variable_reference(name: str) -> str:
    return f"{{{{{name}}}}}"


def host_label(url: str) -> str:
    """The host a credential belongs to, for naming its secret."""
    candidate = url if "://" in url else f"http://{url}"
    try:
        host = urlsplit(candidate).hostname or ""
    except ValueError:
        host = ""
    return host or url.split("/", 1)[0]


def is_header_name(name: str) -> bool:
    return bool(_HEADER_NAME.match(name))


def validation_summary(error: ValidationError) -> str:
    """The first few problems pydantic found, as one readable line."""
    problems = []
    for detail in error.errors()[:MAX_REPORTED_PROBLEMS]:
        location = ".".join(str(part) for part in detail["loc"])
        problems.append(f"{location}: {detail['msg']}" if location else detail["msg"])
    return "; ".join(problems)


@dataclass
class ItemDraft:
    """An item before the plan's variables are known to be complete."""

    key: str
    name: str
    method: str
    url: str
    config: CheckConfig
    needs_values: list[str] = field(default_factory=list)
    needs_files: list[str] = field(default_factory=list)


@dataclass
class RequestParts:
    """A request's pieces as a parser read them, before a model judges them."""

    method: str
    url: str
    headers: list[tuple[str, str]] = field(default_factory=list)
    auth: RequestAuth = field(default_factory=NoAuth)
    needs_values: list[str] = field(default_factory=list)
    needs_files: list[str] = field(default_factory=list)


class PlanBuilder:
    """Collects items, secrets, variables and skips, deduplicating each."""

    def __init__(self, source: ImportSource, title: str) -> None:
        self.source: ImportSource = source
        self.title = title
        self.servers: list[str] = []
        self._drafts: list[ItemDraft] = []
        self._keys: set[str] = set()
        self._secrets: dict[str, DetectedSecret] = {}
        self._variables: dict[str, DetectedVariable] = {}
        self._skipped: dict[tuple[str, str], SkippedItem] = {}

    def secret(self, base_name: str, value: str, found_in: str) -> str:
        """The secret's name: reused for the same value, suffixed for a different one."""
        candidate = base_name
        suffix = 2
        while candidate in self._secrets and self._secrets[candidate].value != value:
            candidate = f"{base_name}_{suffix}"
            suffix += 1
        if candidate not in self._secrets:
            self._secrets[candidate] = DetectedSecret(
                name=candidate, value=value, found_in=found_in
            )
        return candidate

    def variable(self, name: str, value: str, *, replace: bool = False) -> None:
        if replace or name not in self._variables:
            self._variables[name] = DetectedVariable(name=name, value=value)

    def forget_variable(self, name: str) -> None:
        self._variables.pop(name, None)

    def skip(self, item: str, reason: str) -> None:
        self._skipped.setdefault((item, reason), SkippedItem(item=item, reason=reason))

    def add(
        self,
        *,
        key: str,
        name: str,
        method: str,
        url: str,
        build_config: Callable[[], CheckConfig],
        needs_values: Iterable[str] = (),
        needs_files: Iterable[str] = (),
    ) -> bool:
        """Add one check, or list it as skipped when its configuration is refused."""
        if len(self._drafts) >= MAX_IMPORTED_CHECKS:
            raise InvalidRequestError(
                f"This import would create more than {MAX_IMPORTED_CHECKS} checks, which is "
                "the most one import may hold. Split it and import each part."
            )
        try:
            config = build_config()
        except ValidationError as refused:
            self.skip(name, f"cannot become a check: {validation_summary(refused)}")
            return False
        self._drafts.append(
            ItemDraft(
                key=self._unique_key(key),
                name=check_name(name),
                method=method,
                url=url,
                config=config,
                needs_values=list(dict.fromkeys(needs_values)),
                needs_files=list(dict.fromkeys(needs_files)),
            )
        )
        return True

    def build(self) -> ImportPlan:
        for draft in self._drafts:
            self._ask_for_undefined_variables(draft)
        return ImportPlan(
            source=self.source,
            title=self.title,
            servers=self.servers,
            items=[
                ImportItem(
                    key=draft.key,
                    name=draft.name,
                    method=draft.method,
                    url=draft.url,
                    config=draft.config,
                    needs_values=draft.needs_values,
                    needs_files=draft.needs_files,
                )
                for draft in self._drafts
            ],
            secrets=list(self._secrets.values()),
            variables=list(self._variables.values()),
            skipped=list(self._skipped.values()),
        )

    def _ask_for_undefined_variables(self, draft: ItemDraft) -> None:
        referenced = sorted(
            {name for request in requests_of(draft.config) for name in request.variable_names()}
        )
        for name in referenced:
            if name in self._variables:
                continue
            if VARIABLE_NAME_PATTERN.match(name):
                self.variable(name, "")
                message = f"variable {name} has no value"
            else:
                message = (
                    f"{variable_reference(name)} is not a name nexovue can define — "
                    "replace it with a value or a variable"
                )
            if message not in draft.needs_values:
                draft.needs_values.append(message)

    def _unique_key(self, key: str) -> str:
        candidate = key
        suffix = 2
        while candidate in self._keys:
            candidate = f"{key} ({suffix})"
            suffix += 1
        self._keys.add(candidate)
        return candidate


def protect_credentials(parts: RequestParts, builder: PlanBuilder, *, found_in: str) -> None:
    """Move every literal credential in the headers into a secret (BR10).

    A value that already refers to a variable is left alone: the credential is
    wherever that variable is defined, and the caller decides what that is.
    """
    host = host_label(parts.url)
    kept: list[tuple[str, str]] = []
    for name, value in parts.headers:
        lowered = name.lower()
        if has_references(value) or not value:
            kept.append((name, value))
        elif lowered == AUTHORIZATION_HEADER:
            header_value = _authorization(parts, builder, host=host, value=value, found_in=found_in)
            if header_value is not None:
                kept.append((name, header_value))
        elif lowered in CREDENTIAL_HEADERS:
            secret = builder.secret(secret_name(host, name), value, f"{found_in}: {name} header")
            kept.append((name, secret_reference(secret)))
        else:
            kept.append((name, value))
    parts.headers = kept


def _authorization(
    parts: RequestParts, builder: PlanBuilder, *, host: str, value: str, found_in: str
) -> str | None:
    """The header value to keep, or None when the credential became the request's auth."""
    scheme, _, credential = value.strip().partition(" ")
    credential = credential.strip()
    auth_is_free = isinstance(parts.auth, NoAuth)
    if auth_is_free and scheme.lower() == BEARER_SCHEME and credential:
        token = builder.secret(
            secret_name(host, "token"), credential, f"{found_in}: Authorization bearer token"
        )
        parts.auth = BearerAuth(token_secret=token)
        return None
    if auth_is_free and scheme.lower() == BASIC_SCHEME:
        decoded = decode_basic_credentials(credential)
        if decoded is not None:
            username, password = decoded
            secret = builder.secret(
                secret_name(host, "password"), password, f"{found_in}: Authorization basic password"
            )
            parts.auth = BasicAuth(username=username, password_secret=secret)
            return None
    secret = builder.secret(
        secret_name(host, "authorization"), value, f"{found_in}: Authorization header"
    )
    return secret_reference(secret)


def decode_basic_credentials(encoded: str) -> tuple[str, str] | None:
    """`user:password` from a Basic header, or None when it is not one."""
    try:
        decoded = base64.b64decode(encoded, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator or not username or "\n" in username or "\r" in username:
        return None
    return username, password


def headers_of(parts: RequestParts, builder: PlanBuilder, *, item: str) -> list[Header]:
    """The headers a model will accept; each one it would refuse is listed as skipped."""
    headers = []
    for name, value in parts.headers:
        if not is_header_name(name):
            builder.skip(f"{item}: header {name!r}", "not a valid header name")
            continue
        headers.append(Header(name=name, value=value))
    return headers
