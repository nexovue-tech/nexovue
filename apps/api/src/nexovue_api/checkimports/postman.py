"""A Postman collection v2.1, with an optional environment, read as data (FR14).

Pre-request and test scripts are JavaScript that Postman runs in its own
sandbox. They are listed as skipped and never evaluated, translated or parsed:
nothing in this path can run code (BR5, D16, AC11).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import quote

from nexovue_platform.checks.config import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RUN_SECONDS,
    MAX_STEPS,
    ApiCheckConfig,
    ApiKeyAuth,
    BasicAuth,
    BearerAuth,
    CheckConfig,
    FormBody,
    HttpRequest,
    JsonBody,
    MultipartBody,
    MultipartField,
    MultistepCheckConfig,
    NameValue,
    NoBody,
    RequestBody,
    TextBody,
    XmlBody,
)
from nexovue_platform.checks.templating import (
    SECRET_NAME_PATTERN,
    VARIABLE_NAME_PATTERN,
    has_references,
)

from nexovue_api.checkimports import plan
from nexovue_api.checkimports.schemas import ImportPlan, PostmanFolderMode
from nexovue_api.errors.exceptions import InvalidRequestError

_VERSION_2_1: Final = re.compile(r"/collection/v2\.1\.\d+/collection\.json$")
_VERSION_2_0: Final = re.compile(r"/collection/v2\.0\.\d+/collection\.json$")
REEXPORT_STEPS: Final = (
    "In Postman, open the collection's ⋯ menu → Export, choose Collection v2.1, "
    "and import the exported file."
)

SCRIPT_EVENTS: Final = {"prerequest": "pre-request script", "test": "test script"}
SCRIPT_NOT_RUN: Final = "script not run — nexovue never runs code from an import"
SECRET_VARIABLE_TYPE: Final = "secret"
NO_AUTH: Final = "noauth"
INHERIT_AUTH: Final = "inherit"
MAX_FOLDER_DEPTH: Final = 32
UNTITLED: Final = "Untitled"
DEFAULT_SCHEME: Final = "http"  # what Postman assumes for a URL without one
CONTENT_TYPE_HEADER: Final = "content-type"
MULTIPART_CONTENT_TYPE: Final = "multipart/form-data"
TEXT_CONTENT_TYPES: Final = {
    "text": "text/plain",
    "html": "text/html",
    "javascript": "application/javascript",
}

_REFERENCE: Final = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")
_WHOLE_REFERENCE: Final = re.compile(r"^\{\{\s*([^{}\s]+)\s*\}\}$")
_PATH_VARIABLE: Final = re.compile(r"(?<=/):([A-Za-z_][A-Za-z0-9_.\-]*)")

Json = Any


@dataclass
class _Step:
    """One request, built from a collection item and ready to become a check."""

    label: str
    name: str
    parts: plan.RequestParts
    query: list[NameValue] = field(default_factory=list)
    body: RequestBody = field(default_factory=NoBody)
    follow_redirects: bool = True

    def request(self, builder: plan.PlanBuilder, *, timeout_seconds: int) -> HttpRequest:
        return HttpRequest(
            name=plan.check_name(self.name),
            method=self.parts.method,
            url=self.parts.url,
            query=self.query,
            headers=plan.headers_of(self.parts, builder, item=self.label),
            body=self.body,
            auth=self.parts.auth,
            assertions=plan.status_below_error(),
            follow_redirects=self.follow_redirects,
            timeout_seconds=timeout_seconds,
        )


def preview(text: str, *, environment: str | None, folders: PostmanFolderMode) -> ImportPlan:
    collection = _collection(text)
    title = _text(_mapping(collection.get("info")).get("name")) or "Postman collection"
    reader = _CollectionReader(plan.PlanBuilder("postman", title), folders)
    reader.read(collection, environment)
    return reader.builder.build()


def _collection(text: str) -> dict[str, Json]:
    document = plan.read_json(text, what="Postman collection")
    if not isinstance(document, dict):
        raise InvalidRequestError("A Postman collection must hold an object at its top level.")
    info = document.get("info")
    if not isinstance(info, dict):
        if isinstance(document.get("requests"), list):
            raise InvalidRequestError(
                f"This is a Postman collection v1, which cannot be imported. {REEXPORT_STEPS}"
            )
        raise InvalidRequestError("This file is not a Postman collection.")
    schema = _text(info.get("schema"))
    if _VERSION_2_0.search(schema):
        raise InvalidRequestError(
            f"This is a Postman collection v2.0, which cannot be imported. {REEXPORT_STEPS}"
        )
    if not _VERSION_2_1.search(schema):
        raise InvalidRequestError(
            f"This collection does not declare the Postman v2.1 format. {REEXPORT_STEPS}"
        )
    return document


class _CollectionReader:
    """Walks one collection. Secrets are decided before any request is built, so a
    reference is written as `{{secret.NAME}}` everywhere it appears."""

    def __init__(self, builder: plan.PlanBuilder, folders: PostmanFolderMode) -> None:
        self.builder = builder
        self.folders = folders
        self._values: dict[str, str] = {}
        # A Postman variable name → the secret it became.
        self._secrets: dict[str, str] = {}

    def read(self, collection: dict[str, Json], environment: str | None) -> None:
        self._define_variables(collection.get("variable"))
        self._define_folder_variables(collection.get("item"), depth=0)
        if environment is not None:
            self._read_environment(environment)
        self._promote_credential_variables(collection, depth=0)

        self._skip_scripts(collection, "collection")
        self._walk(collection.get("item"), path=(), inherited_auth=collection.get("auth"), depth=0)

    # --- variables and secrets ------------------------------------------------------

    def _define_variables(self, variables: Json, *, replace: bool = False) -> None:
        for raw in _sequence(variables):
            variable = _mapping(raw)
            name = _text(variable.get("key"))
            if not name or variable.get("disabled") is True:
                continue
            if not VARIABLE_NAME_PATTERN.match(name):
                self.builder.skip(f"variable {name}", "not a name a check variable can have")
                continue
            value = _as_text(variable.get("value"))
            if replace or name not in self._values:
                self._values[name] = value
            self.builder.variable(name, value, replace=replace)

    def _define_folder_variables(self, items: Json, *, depth: int) -> None:
        if depth > MAX_FOLDER_DEPTH:
            return
        for raw in _sequence(items):
            item = _mapping(raw)
            self._define_variables(item.get("variable"))
            self._define_folder_variables(item.get("item"), depth=depth + 1)

    def _read_environment(self, text: str) -> None:
        document = plan.read_json(text, what="Postman environment")
        if not isinstance(document, dict) or not isinstance(document.get("values"), list):
            raise InvalidRequestError("The environment file is not a Postman environment export.")
        for raw in document["values"]:
            value = _mapping(raw)
            name = _text(value.get("key"))
            if not name or value.get("enabled") is False:
                continue
            if value.get("type") == SECRET_VARIABLE_TYPE:
                self._become_secret(
                    name, _as_text(value.get("value")), f"environment secret {name}"
                )
            else:
                self._define_variables([{"key": name, "value": value.get("value")}], replace=True)

    def _become_secret(self, variable: str, value: str, found_in: str) -> str:
        """A variable whose value is a credential is stored as a secret instead (BR10)."""
        if variable in self._secrets:
            return self._secrets[variable]
        base = variable if SECRET_NAME_PATTERN.match(variable) else plan.secret_name(variable)
        name = self.builder.secret(base, value, found_in)
        self._secrets[variable] = name
        self._values.pop(variable, None)
        self.builder.forget_variable(variable)
        return name

    def _promote_credential_variables(self, node: dict[str, Json], *, depth: int) -> None:
        """A variable an auth block uses as a password, token or key is a credential."""
        for credential in _credential_values(_mapping(node.get("auth"))):
            match = _WHOLE_REFERENCE.match(credential.strip())
            if match and match.group(1) not in self._secrets:
                variable = match.group(1)
                value = self._values.get(variable, "")
                self._become_secret(variable, value, f"variable {variable}, used as a credential")
        request = node.get("request")
        if isinstance(request, dict):
            self._promote_credential_variables(request, depth=depth)
        if depth <= MAX_FOLDER_DEPTH:
            for child in _sequence(node.get("item")):
                self._promote_credential_variables(_mapping(child), depth=depth + 1)

    def _secretise(self, text: str) -> str:
        """`{{token}}` → `{{secret.token}}` for every variable that became a secret."""

        def replace(match: re.Match[str]) -> str:
            secret = self._secrets.get(match.group(1))
            return plan.secret_reference(secret) if secret is not None else match.group(0)

        return _REFERENCE.sub(replace, text)

    # --- the tree -------------------------------------------------------------------

    def _walk(
        self, items: Json, *, path: tuple[str, ...], inherited_auth: Json, depth: int
    ) -> None:
        folder_steps: list[_Step] = []
        for raw in _sequence(items):
            item = _mapping(raw)
            label = " / ".join((*path, _text(item.get("name")) or UNTITLED))
            self._skip_scripts(item, label)
            if isinstance(item.get("item"), list):
                if depth >= MAX_FOLDER_DEPTH:
                    self.builder.skip(label, f"folders nested more than {MAX_FOLDER_DEPTH} deep")
                    continue
                auth = _effective_auth(item.get("auth"), inherited_auth)
                self._walk(
                    item["item"],
                    path=(*path, _text(item.get("name")) or UNTITLED),
                    inherited_auth=auth,
                    depth=depth + 1,
                )
            elif "request" in item:
                step = self._step(item, label, inherited_auth)
                if step is None:
                    continue
                if self.folders == "multistep" and path:
                    folder_steps.append(step)
                else:
                    self._add_single(step)
            else:
                self.builder.skip(label, "neither a request nor a folder")
        if folder_steps:
            self._add_multistep(" / ".join(path), folder_steps)

    def _skip_scripts(self, node: dict[str, Json], label: str) -> None:
        for raw in _sequence(node.get("event")):
            event = _mapping(raw)
            kind = SCRIPT_EVENTS.get(_text(event.get("listen")))
            if kind is not None and _has_script(event.get("script")):
                self.builder.skip(f"{label}: {kind}", SCRIPT_NOT_RUN)

    def _add_single(self, step: _Step) -> None:
        def build_config() -> CheckConfig:
            return ApiCheckConfig(
                request=step.request(self.builder, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)
            )

        self.builder.add(
            key=step.label,
            name=step.label,
            method=step.parts.method,
            url=step.parts.url,
            build_config=build_config,
            needs_values=step.parts.needs_values,
            needs_files=step.parts.needs_files,
        )

    def _add_multistep(self, folder: str, steps: list[_Step]) -> None:
        """One check per folder (AC12). A folder longer than a multistep check may be
        is split into consecutive parts, so no request is lost."""
        chunks = [steps[start : start + MAX_STEPS] for start in range(0, len(steps), MAX_STEPS)]
        for number, chunk in enumerate(chunks, start=1):
            name = folder if len(chunks) == 1 else f"{folder} (part {number} of {len(chunks)})"
            # The steps' limits must add up to one run's.
            timeout = min(DEFAULT_TIMEOUT_SECONDS, MAX_RUN_SECONDS // len(chunk))

            def build_config(chunk: list[_Step] = chunk, timeout: int = timeout) -> CheckConfig:
                return MultistepCheckConfig(
                    steps=[step.request(self.builder, timeout_seconds=timeout) for step in chunk]
                )

            self.builder.add(
                key=name,
                name=name,
                method=chunk[0].parts.method,
                url=chunk[0].parts.url,
                build_config=build_config,
                needs_values=[f"{s.name}: {need}" for s in chunk for need in s.parts.needs_values],
                needs_files=[f"{s.name}: {need}" for s in chunk for need in s.parts.needs_files],
            )

    # --- one request ----------------------------------------------------------------

    def _step(self, item: dict[str, Json], label: str, inherited_auth: Json) -> _Step | None:
        raw_request = item["request"]
        request = {"url": raw_request} if isinstance(raw_request, str) else _mapping(raw_request)
        method = (_text(request.get("method")) or "GET").upper()
        step = _Step(
            label=label,
            name=_text(item.get("name")) or UNTITLED,
            parts=plan.RequestParts(method=method, url=""),
        )
        if not self._read_url(request.get("url"), step):
            self.builder.skip(label, "the request has no URL")
            return None
        step.parts.headers = [
            (self._secretise(name), self._secretise(value))
            for name, value in _enabled_pairs(request.get("header"))
        ]
        self._read_auth(_effective_auth(request.get("auth"), inherited_auth), step)
        plan.protect_credentials(step.parts, self.builder, found_in=label)
        step.body = self._body(_mapping(request.get("body")), step)
        behaviour = _mapping(item.get("protocolProfileBehavior"))
        if behaviour.get("followRedirects") is False:
            step.follow_redirects = False
        return step

    def _read_url(self, node: Json, step: _Step) -> bool:
        if isinstance(node, str):
            raw, variables, query = node, [], None
        else:
            url = _mapping(node)
            raw = _text(url.get("raw")) or _assembled_url(url)
            variables = _sequence(url.get("variable"))
            query = url.get("query") if "query" in url else None
        raw = raw.strip()
        if not raw:
            return False
        base = raw.partition("?")[0]
        if query is None:
            step.parts.url = self._secretise(raw)
        else:
            step.parts.url = self._secretise(base)
            step.query = [
                NameValue(name=self._secretise(name), value=self._secretise(value))
                for name, value in _enabled_pairs(query)
            ]
        step.parts.url = self._filled_path_variables(step.parts.url, variables, step)
        if "://" not in step.parts.url and not step.parts.url.startswith("{{"):
            step.parts.url = f"{DEFAULT_SCHEME}://{step.parts.url}"
        return True

    def _filled_path_variables(self, url: str, variables: list[Json], step: _Step) -> str:
        """`/orders/:id` takes the value the collection gave `id`."""
        values = {
            _text(_mapping(v).get("key")): _as_text(_mapping(v).get("value")) for v in variables
        }

        def fill(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in values:
                return match.group(0)
            if values[name]:
                value = values[name]
                return self._secretise(value) if has_references(value) else quote(value, safe="")
            variable = plan.variable_name(name)
            self.builder.variable(variable, "")
            step.parts.needs_values.append(f"path variable {name} has no value")
            return plan.variable_reference(variable)

        return _PATH_VARIABLE.sub(fill, url)

    def _read_auth(self, auth: dict[str, Json], step: _Step) -> None:
        kind = _text(auth.get("type"))
        if not kind or kind == NO_AUTH:
            return
        fields = _auth_fields(auth, kind)
        host = plan.host_label(step.parts.url)
        where = f"{step.label}: {kind} authentication"
        if kind == "bearer":
            secret = self._credential(
                fields.get("token", ""), plan.secret_name(host, "token"), where
            )
            if secret is not None:
                step.parts.auth = BearerAuth(token_secret=secret)
                return
        elif kind == "basic":
            username = self._secretise(fields.get("username", ""))
            secret = self._credential(
                fields.get("password", ""), plan.secret_name(host, "password"), where
            )
            if username and secret is not None:
                step.parts.auth = BasicAuth(username=username, password_secret=secret)
                return
        elif kind == "apikey":
            key = fields.get("key", "")
            secret = self._credential(fields.get("value", ""), plan.secret_name(host, key), where)
            if key and secret is not None:
                location = "query" if fields.get("in") == "query" else "header"
                step.parts.auth = ApiKeyAuth(name=key, value_secret=secret, location=location)
                return
        else:
            self.builder.skip(where, "not supported; the check is created without it")
            return
        self.builder.skip(where, "incomplete, or mixes text with variables; created without it")

    def _credential(self, value: str, base_name: str, found_in: str) -> str | None:
        """The secret a credential is stored in, or None when it cannot be one."""
        match = _WHOLE_REFERENCE.match(value.strip())
        if match:
            variable = match.group(1)
            return self._become_secret(variable, self._values.get(variable, ""), found_in)
        if has_references(value):
            return None
        return self.builder.secret(base_name, value, found_in)

    def _body(self, body: dict[str, Json], step: _Step) -> RequestBody:
        mode = _text(body.get("mode"))
        if not mode or body.get("disabled") is True:
            return NoBody()
        if mode == "raw":
            return self._raw_body(body, step)
        if mode == "urlencoded":
            return FormBody(
                fields=[
                    NameValue(name=self._secretise(name), value=self._secretise(value))
                    for name, value in _enabled_pairs(body.get("urlencoded"))
                ]
            )
        if mode == "formdata":
            return self._multipart_body(body, step)
        if mode == "file":
            source = _as_text(_mapping(body.get("file")).get("src")) or "(no file chosen)"
            step.parts.needs_files.append(
                f"the request body is the local file {source} — attach it"
            )
            return NoBody()
        if mode == "graphql":
            graphql = _mapping(body.get("graphql"))
            query = json.dumps(self._secretise(_as_text(graphql.get("query"))))
            variables = self._secretise(_as_text(graphql.get("variables"))).strip() or "{}"
            return JsonBody(content=f'{{"query": {query}, "variables": {variables}}}')
        self.builder.skip(f"{step.label}: {mode} body", "this body type is not supported")
        return NoBody()

    def _raw_body(self, body: dict[str, Json], step: _Step) -> RequestBody:
        content = self._secretise(_as_text(body.get("raw")))
        language = _text(_mapping(_mapping(body.get("options")).get("raw")).get("language"))
        declared = next(
            (value for name, value in step.parts.headers if name.lower() == CONTENT_TYPE_HEADER),
            "",
        )
        if language == "json" or "json" in declared.lower():
            return JsonBody(content=content)
        if language == "xml" or "xml" in declared.lower():
            return XmlBody(content=content)
        content_type = declared or TEXT_CONTENT_TYPES.get(language, TEXT_CONTENT_TYPES["text"])
        return TextBody(content=content, content_type=content_type)

    def _multipart_body(self, body: dict[str, Json], step: _Step) -> RequestBody:
        parts: list[MultipartField] = []
        for raw in _sequence(body.get("formdata")):
            part = _mapping(raw)
            name = _text(part.get("key"))
            if not name or part.get("disabled") is True:
                continue
            if part.get("type") == "file":
                source = part.get("src")
                sources = source if isinstance(source, list) else [source]
                for path in sources:
                    shown = _as_text(path) or "(no file chosen)"
                    step.parts.needs_files.append(
                        f"form field '{name}' refers to a local file {shown} — attach it"
                    )
                continue
            parts.append(
                MultipartField(
                    name=self._secretise(name), value=self._secretise(_as_text(part.get("value")))
                )
            )
        # A typed multipart header has no boundary; the probe writes its own.
        step.parts.headers = [
            (name, value)
            for name, value in step.parts.headers
            if not (
                name.lower() == CONTENT_TYPE_HEADER
                and value.lower().startswith(MULTIPART_CONTENT_TYPE)
            )
        ]
        return MultipartBody(parts=parts)


# --- helpers ------------------------------------------------------------------------


def _effective_auth(own: Json, inherited: Json) -> dict[str, Json]:
    """A request or folder without its own auth uses its parent's (FR14)."""
    own_auth = _mapping(own)
    if own_auth and _text(own_auth.get("type")) != INHERIT_AUTH:
        return own_auth
    return _mapping(inherited)


def _auth_fields(auth: dict[str, Json], kind: str) -> dict[str, str]:
    """v2.1 lists `{key, value}` pairs; some exporters write a plain object."""
    attributes = auth.get(kind)
    if isinstance(attributes, dict):
        return {str(key): _as_text(value) for key, value in attributes.items()}
    return {
        _text(_mapping(pair).get("key")): _as_text(_mapping(pair).get("value"))
        for pair in _sequence(attributes)
    }


def _credential_values(auth: dict[str, Json]) -> list[str]:
    kind = _text(auth.get("type"))
    fields = _auth_fields(auth, kind) if kind else {}
    credential_keys = {"bearer": ("token",), "basic": ("password",), "apikey": ("value",)}
    return [fields.get(key, "") for key in credential_keys.get(kind, ())]


def _assembled_url(url: dict[str, Json]) -> str:
    host = url.get("host")
    path = url.get("path")
    host_text = ".".join(_as_text(p) for p in host) if isinstance(host, list) else _as_text(host)
    path_text = "/".join(_as_text(p) for p in path) if isinstance(path, list) else _as_text(path)
    if not host_text:
        return ""
    protocol = _text(url.get("protocol"))
    port = _as_text(url.get("port"))
    prefix = f"{protocol}://" if protocol else ""
    suffix = f":{port}" if port else ""
    return f"{prefix}{host_text}{suffix}/{path_text.lstrip('/')}".rstrip("/")


def _enabled_pairs(pairs: Json) -> list[tuple[str, str]]:
    enabled = []
    for raw in _sequence(pairs):
        pair = _mapping(raw)
        name = _text(pair.get("key"))
        if name and pair.get("disabled") is not True:
            enabled.append((name, _as_text(pair.get("value"))))
    return enabled


def _has_script(script: Json) -> bool:
    lines = _mapping(script).get("exec")
    text = (
        "\n".join(_as_text(line) for line in lines) if isinstance(lines, list) else _as_text(lines)
    )
    return bool(text.strip())


def _mapping(node: Json) -> dict[str, Json]:
    return node if isinstance(node, dict) else {}


def _sequence(node: Json) -> list[Json]:
    return node if isinstance(node, list) else []


def _text(node: Json) -> str:
    return node.strip() if isinstance(node, str) else ""


def _as_text(value: Json) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value)
