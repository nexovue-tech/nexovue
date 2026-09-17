"""An OpenAPI description — Swagger 2.0 or OpenAPI 3.0, 3.1, 3.2 — read as data (FR13).

The order is the protection. Every `$ref` is checked to point inside the
document *before* the validator sees it, because no documented option stops the
validator following a remote one (AC10, D14). Local references are then resolved
here by JSON pointer, never by a resolver that knows how to fetch.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final
from urllib.parse import quote, unquote

from nexovue_platform.checks.config import (
    ApiCheckConfig,
    ApiKeyAuth,
    Assertion,
    BasicAuth,
    BearerAuth,
    CheckConfig,
    FormBody,
    HttpRequest,
    JsonBody,
    MultipartBody,
    MultipartField,
    NameValue,
    NoAuth,
    NoBody,
    RequestAuth,
    RequestBody,
)
from openapi_spec_validator import validate
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

from nexovue_api.checkimports import plan, yaml_safe
from nexovue_api.checkimports.schemas import MAX_IMPORTED_CHECKS, ImportPlan
from nexovue_api.errors.exceptions import InvalidRequestError

SWAGGER_VERSION: Final = "2.0"
_OPENAPI_VERSION: Final = re.compile(r"^3\.[0-2]\.\d+$")
_OPENAPI_3_2: Final = re.compile(r"^3\.2\.")

LOCAL_REFERENCE_PREFIX: Final = "#/"
REFERENCE_KEYWORDS: Final = ("$ref", "$dynamicRef")
MAX_DOCUMENT_DEPTH: Final = 256
MAX_REFERENCE_HOPS: Final = 32
MAX_VALIDATION_MESSAGE: Final = 300

HTTP_METHODS: Final = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
QUERY_METHOD: Final = "query"  # OpenAPI 3.2 only

SUCCESS_FLOOR: Final = 200
SUCCESS_CEILING: Final = 299
BASE_URL_VARIABLE: Final = "base_url"
NO_SERVER: Final = f"the description names no server URL — set {BASE_URL_VARIABLE}"
BASIC_USERNAME_PLACEHOLDER: Final = "user"
DEFAULT_SWAGGER_SCHEME: Final = "https"
JSON_INDENT: Final = 2
MULTIPART_CONTENT_TYPE: Final = "multipart/form-data"
# Header parameters OpenAPI itself says are ignored: the body and auth set them.
IGNORED_HEADER_PARAMETERS: Final = frozenset({"accept", "content-type", "authorization"})

_PATH_TEMPLATE: Final = re.compile(r"\{([^{}/]+)\}")
_SERVER_VARIABLE: Final = re.compile(r"\{([^{}]+)\}")

Json = Any


@dataclass(frozen=True, slots=True)
class Example:
    """A documented value. Wrapped so that a documented `null` is still a value."""

    value: Json


@dataclass
class _Request:
    """One operation's request as it is assembled."""

    parts: plan.RequestParts
    query: list[tuple[str, str]] = field(default_factory=list)
    body: RequestBody = field(default_factory=NoBody)


@dataclass(frozen=True, slots=True)
class _Operation:
    path: str
    method: str
    definition: dict[str, Json]
    path_item: dict[str, Json]


def preview(text: str, *, server_url: str | None) -> ImportPlan:
    document = _parse(text)
    _refuse_outside_references(document)
    swagger = _version(document)
    _validate(document)

    operations = list(_operations(document, swagger))
    if len(operations) > MAX_IMPORTED_CHECKS:
        raise InvalidRequestError(
            f"This description has {len(operations)} operations. An import may hold at most "
            f"{MAX_IMPORTED_CHECKS}; split the description and import each part."
        )

    info = _mapping(document.get("info"))
    title = info.get("title")
    builder = plan.PlanBuilder("openapi", title if isinstance(title, str) else "OpenAPI import")
    root_bases = _root_bases(document, swagger)
    builder.servers = [base for base in root_bases if "://" in base]
    for operation in operations:
        _add_operation(
            builder, document, operation, swagger=swagger, bases=root_bases, chosen=server_url
        )
    return builder.build()


# --- reading and refusing -----------------------------------------------------------


def _parse(text: str) -> dict[str, Json]:
    what = "OpenAPI file"
    document = (
        plan.read_json(text, what=what)
        if text.lstrip().startswith("{")
        else yaml_safe.load(text, what=what)
    )
    if not isinstance(document, dict):
        raise InvalidRequestError("An OpenAPI file must hold an object at its top level.")
    return {str(key): _as_json(value, depth=1) for key, value in document.items()}


def _as_json(node: Json, *, depth: int) -> Json:
    """What JSON would have held. YAML reads `200:` as a number and a date as a date;
    OpenAPI means the text."""
    if depth > MAX_DOCUMENT_DEPTH:
        raise InvalidRequestError(
            f"This OpenAPI file is nested more than {MAX_DOCUMENT_DEPTH} levels deep."
        )
    if isinstance(node, dict):
        return {str(key): _as_json(value, depth=depth + 1) for key, value in node.items()}
    if isinstance(node, list):
        return [_as_json(value, depth=depth + 1) for value in node]
    if isinstance(node, date):
        return node.isoformat()
    return node


def _refuse_outside_references(document: dict[str, Json]) -> None:
    """AC10: a reference to another file or URL is refused, and nothing is fetched."""
    for keyword, reference in _references(document):
        if keyword == "$ref" and not reference.startswith(LOCAL_REFERENCE_PREFIX):
            raise InvalidRequestError(
                f"This description refers to {reference!r}, outside itself. An import must be "
                "self-contained: bundle the referenced files into this one and import it again."
            )
        if keyword == "$dynamicRef" and not reference.startswith("#"):
            raise InvalidRequestError(
                f"This description refers to {reference!r}, outside itself. An import must be "
                "self-contained."
            )
        if keyword == "$ref":
            _pointer(document, reference)


def _references(document: dict[str, Json]) -> Iterator[tuple[str, str]]:
    pending: list[Json] = [document]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            for keyword in REFERENCE_KEYWORDS:
                value = node.get(keyword)
                if isinstance(value, str):
                    yield keyword, value
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)


def _version(document: dict[str, Json]) -> bool:
    """True for Swagger 2.0; refuses every version this import does not read."""
    swagger = document.get("swagger")
    openapi = document.get("openapi")
    if swagger == SWAGGER_VERSION:
        return True
    if isinstance(openapi, str) and _OPENAPI_VERSION.match(openapi):
        return False
    declared = openapi if openapi is not None else swagger
    named = f"version {declared!r}" if declared is not None else "no OpenAPI version"
    raise InvalidRequestError(
        f"This file declares {named}. Supported: Swagger 2.0 and OpenAPI 3.0, 3.1 and 3.2."
    )


def _validate(document: dict[str, Json]) -> None:
    try:
        validate(document)
    except OpenAPIValidationError as invalid:
        where = _pointer_text(invalid.absolute_path)
        raise InvalidRequestError(
            f"This OpenAPI description is not valid at {where}: {_short(invalid.message)}"
        ) from invalid
    except Exception as invalid:
        # The validator raises its libraries' own errors on malformed input, and
        # the input is untrusted: any failure means the description is not valid.
        raise InvalidRequestError(
            f"This OpenAPI description is not valid: {_short(str(invalid))}"
        ) from invalid


def _pointer_text(path: Iterable[object]) -> str:
    tokens = [str(token).replace("~", "~0").replace("/", "~1") for token in path]
    return "#/" + "/".join(tokens)


def _short(message: str) -> str:
    first_line = message.strip().splitlines()[0] if message.strip() else "unknown problem"
    if len(first_line) <= MAX_VALIDATION_MESSAGE:
        return first_line
    return first_line[: MAX_VALIDATION_MESSAGE - 1] + plan.TRUNCATION_MARK


# --- references ---------------------------------------------------------------------


def _pointer(document: dict[str, Json], reference: str) -> Json:
    target: Json = document
    for raw_token in reference[len(LOCAL_REFERENCE_PREFIX) :].split("/"):
        token = unquote(raw_token).replace("~1", "/").replace("~0", "~")
        if isinstance(target, dict) and token in target:
            target = target[token]
        elif isinstance(target, list) and token.isdigit() and int(token) < len(target):
            target = target[int(token)]
        else:
            raise InvalidRequestError(
                f"The reference {reference!r} points to nothing in this file."
            )
    return target


def _resolved(document: dict[str, Json], node: Json) -> Json:
    for _ in range(MAX_REFERENCE_HOPS):
        reference = node.get("$ref") if isinstance(node, dict) else None
        if not isinstance(reference, str):
            return node
        node = _pointer(document, reference)
    raise InvalidRequestError(
        f"A reference in this file leads through more than {MAX_REFERENCE_HOPS} others, "
        "or back to itself."
    )


def _mapping(node: Json) -> dict[str, Json]:
    return node if isinstance(node, dict) else {}


def _sequence(node: Json) -> list[Json]:
    return node if isinstance(node, list) else []


# --- operations ---------------------------------------------------------------------


def _operations(document: dict[str, Json], swagger: bool) -> Iterator[_Operation]:
    version = document.get("openapi")
    has_query_method = isinstance(version, str) and bool(_OPENAPI_3_2.match(version))
    for path, raw_item in _mapping(document.get("paths")).items():
        path_item = _mapping(_resolved(document, raw_item))
        methods = (
            [*HTTP_METHODS, QUERY_METHOD] if has_query_method and not swagger else HTTP_METHODS
        )
        for method in methods:
            if method in path_item:
                definition = _mapping(_resolved(document, path_item[method]))
                yield _Operation(path, method.upper(), definition, path_item)
        for method, raw in _mapping(path_item.get("additionalOperations")).items():
            yield _Operation(path, method.upper(), _mapping(_resolved(document, raw)), path_item)


def _add_operation(
    builder: plan.PlanBuilder,
    document: dict[str, Json],
    operation: _Operation,
    *,
    swagger: bool,
    bases: list[str],
    chosen: str | None,
) -> None:
    key = f"{operation.method} {operation.path}"
    base = _base_for(builder, operation, swagger=swagger, root_bases=bases, chosen=chosen)
    parameters = _parameters(document, operation)
    request = _Request(parts=plan.RequestParts(method=operation.method, url=""))
    if base.startswith(plan.variable_reference(BASE_URL_VARIABLE)):
        request.parts.needs_values.append(NO_SERVER)
    request.parts.url = base + _filled_path(builder, document, operation.path, parameters, request)

    _add_query_header_and_cookie_parameters(builder, document, parameters, request)
    if swagger:
        _add_swagger_body(builder, document, operation, parameters, request)
    else:
        _add_request_body(document, operation.definition, request)
    _add_security(builder, document, operation, request, swagger=swagger)

    responses = _mapping(operation.definition.get("responses"))
    name = _text(operation.definition.get("operationId")) or _text(
        operation.definition.get("summary")
    )

    def build_config() -> CheckConfig:
        return ApiCheckConfig(
            request=HttpRequest(
                method=operation.method,
                url=request.parts.url,
                query=[NameValue(name=n, value=v) for n, v in request.query],
                headers=plan.headers_of(request.parts, builder, item=key),
                body=request.body,
                auth=request.parts.auth,
                assertions=_success_assertions(responses),
            )
        )

    builder.add(
        key=key,
        name=name or key,
        method=operation.method,
        url=request.parts.url,
        build_config=build_config,
        needs_values=request.parts.needs_values,
        needs_files=request.parts.needs_files,
    )


def _text(node: Json) -> str:
    return node.strip() if isinstance(node, str) else ""


# --- servers ------------------------------------------------------------------------


def _root_bases(document: dict[str, Json], swagger: bool) -> list[str]:
    if swagger:
        host = _text(document.get("host"))
        base_path = _text(document.get("basePath")).rstrip("/")
        if not host:
            return [base_path]
        schemes = [
            scheme for scheme in _sequence(document.get("schemes")) if isinstance(scheme, str)
        ]
        scheme = schemes[0] if schemes else DEFAULT_SWAGGER_SCHEME
        return [f"{scheme}://{host}{base_path}"]
    return _server_urls(document.get("servers"))


def _server_urls(servers: Json) -> list[str]:
    urls = []
    for server in _sequence(servers):
        server_map = _mapping(server)
        url = _text(server_map.get("url"))
        variables = _mapping(server_map.get("variables"))

        def default_of(match: re.Match[str], variables: dict[str, Json] = variables) -> str:
            default = _mapping(variables.get(match.group(1))).get("default")
            return default if isinstance(default, str) else match.group(0)

        urls.append(_SERVER_VARIABLE.sub(default_of, url).rstrip("/"))
    return urls


def _base_for(
    builder: plan.PlanBuilder,
    operation: _Operation,
    *,
    swagger: bool,
    root_bases: list[str],
    chosen: str | None,
) -> str:
    """The server this operation is called on; `{{base_url}}` when the file names none."""
    bases = root_bases
    if not swagger:
        override = operation.definition.get("servers") or operation.path_item.get("servers")
        if override:
            bases = _server_urls(override)
    absolute = [base for base in bases if "://" in base]
    if chosen is not None and chosen.rstrip("/") in absolute:
        return chosen.rstrip("/")
    if absolute:
        return absolute[0]
    builder.variable(BASE_URL_VARIABLE, "")
    relative = bases[0] if bases else ""
    return plan.variable_reference(BASE_URL_VARIABLE) + relative


# --- parameters ---------------------------------------------------------------------


def _parameters(document: dict[str, Json], operation: _Operation) -> list[dict[str, Json]]:
    """Path-level parameters, overridden by the operation's own of the same name and place."""
    merged: dict[tuple[str, str], dict[str, Json]] = {}
    for source in (operation.path_item, operation.definition):
        for raw in _sequence(source.get("parameters")):
            parameter = _mapping(_resolved(document, raw))
            name, location = _text(parameter.get("name")), _text(parameter.get("in"))
            if name and location:
                merged[(name, location)] = parameter
    return list(merged.values())


def _filled_path(
    builder: plan.PlanBuilder,
    document: dict[str, Json],
    path: str,
    parameters: list[dict[str, Json]],
    request: _Request,
) -> str:
    in_path = {_text(p.get("name")): p for p in parameters if p.get("in") == "path"}

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        example = _parameter_example(document, in_path[name]) if name in in_path else None
        if example is not None:
            return quote(_as_text(example.value), safe="")
        variable = plan.variable_name(name)
        builder.variable(variable, "")
        request.parts.needs_values.append(f"path parameter {name} has no example")
        return plan.variable_reference(variable)

    return _PATH_TEMPLATE.sub(fill, path)


def _add_query_header_and_cookie_parameters(
    builder: plan.PlanBuilder,
    document: dict[str, Json],
    parameters: list[dict[str, Json]],
    request: _Request,
) -> None:
    cookies: list[str] = []
    for parameter in parameters:
        location = parameter.get("in")
        name = _text(parameter.get("name"))
        if location not in ("query", "header", "cookie"):
            continue
        if location == "header" and name.lower() in IGNORED_HEADER_PARAMETERS:
            continue
        example = _parameter_example(document, parameter)
        if example is None and parameter.get("required") is not True:
            continue
        values = _values(builder, request, name, location, example)
        if location == "query":
            request.query.extend((name, value) for value in values)
        elif location == "header":
            request.parts.headers.append((name, ",".join(values)))
        else:
            cookies.extend(f"{name}={value}" for value in values)
    if cookies:
        request.parts.headers.append(("Cookie", "; ".join(cookies)))


def _values(
    builder: plan.PlanBuilder,
    request: _Request,
    name: str,
    location: str,
    example: Example | None,
) -> list[str]:
    if example is None:
        variable = plan.variable_name(name)
        builder.variable(variable, "")
        request.parts.needs_values.append(f"{location} parameter {name} has no example")
        return [plan.variable_reference(variable)]
    if isinstance(example.value, list):
        return [_as_text(value) for value in example.value]
    return [_as_text(example.value)]


def _parameter_example(document: dict[str, Json], parameter: dict[str, Json]) -> Example | None:
    if "x-example" in parameter:  # Swagger 2.0 has no example field on a parameter
        return Example(parameter["x-example"])
    found = _example_of(document, parameter)
    if found is not None:
        return found
    for media in _mapping(parameter.get("content")).values():
        found = _example_of(document, _mapping(_resolved(document, media)))
        if found is not None:
            return found
    return None


def _example_of(document: dict[str, Json], holder: dict[str, Json]) -> Example | None:
    """`example`, then the first of `examples`, then the schema's own."""
    if "example" in holder:
        return Example(holder["example"])
    for raw in _mapping(holder.get("examples")).values():
        example = _mapping(_resolved(document, raw))
        if "value" in example:
            return Example(example["value"])
    schema = _mapping(_resolved(document, holder.get("schema")))
    if "example" in schema:
        return Example(schema["example"])
    schema_examples = _sequence(schema.get("examples"))
    if schema_examples:
        return Example(schema_examples[0])
    return None


def _as_text(value: Json) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, default=str)


# --- bodies -------------------------------------------------------------------------


def _add_request_body(
    document: dict[str, Json],
    definition: dict[str, Json],
    request: _Request,
) -> None:
    body = _mapping(_resolved(document, definition.get("requestBody")))
    if not body:
        return
    content = _mapping(body.get("content"))
    json_type = next((media for media in content if _is_json(media)), None)
    example = (
        _example_of(document, _mapping(_resolved(document, content[json_type])))
        if json_type is not None
        else None
    )
    if example is not None:
        request.body = JsonBody(content=json.dumps(example.value, indent=JSON_INDENT, default=str))
    elif body.get("required") is True:
        media_types = ", ".join(content) or "no media type"
        request.parts.needs_values.append(f"the request body ({media_types}) has no JSON example")


def _add_swagger_body(
    builder: plan.PlanBuilder,
    document: dict[str, Json],
    operation: _Operation,
    parameters: list[dict[str, Json]],
    request: _Request,
) -> None:
    body_parameter = next((p for p in parameters if p.get("in") == "body"), None)
    if body_parameter is not None:
        schema = _mapping(_resolved(document, body_parameter.get("schema")))
        example = schema.get("example", body_parameter.get("x-example"))
        if "example" in schema or "x-example" in body_parameter:
            request.body = JsonBody(content=json.dumps(example, indent=JSON_INDENT, default=str))
        elif body_parameter.get("required") is True:
            request.parts.needs_values.append("the request body has no example")
        return
    form_parameters = [p for p in parameters if p.get("in") == "formData"]
    if not form_parameters:
        return
    consumes = _sequence(operation.definition.get("consumes") or document.get("consumes"))
    fields: list[tuple[str, str]] = []
    for parameter in form_parameters:
        name = _text(parameter.get("name"))
        if parameter.get("type") == "file":
            request.parts.needs_files.append(f"form field '{name}' is a file — attach it")
            continue
        example = _parameter_example(document, parameter)
        if example is None and parameter.get("required") is not True:
            continue
        fields.extend((name, value) for value in _values(builder, request, name, "form", example))
    if MULTIPART_CONTENT_TYPE in consumes or request.parts.needs_files:
        request.body = MultipartBody(parts=[MultipartField(name=n, value=v) for n, v in fields])
    else:
        request.body = FormBody(fields=[NameValue(name=n, value=v) for n, v in fields])


def _is_json(media_type: str) -> bool:
    essence = media_type.split(";", 1)[0].strip().lower()
    return essence == "application/json" or essence.endswith("+json")


# --- security -----------------------------------------------------------------------


def _add_security(
    builder: plan.PlanBuilder,
    document: dict[str, Json],
    operation: _Operation,
    request: _Request,
    *,
    swagger: bool,
) -> None:
    definition = operation.definition
    requirements = _sequence(
        definition["security"] if "security" in definition else document.get("security")
    )
    if not requirements:
        return
    schemes = _mapping(
        document.get("securityDefinitions")
        if swagger
        else _mapping(document.get("components")).get("securitySchemes")
    )
    chosen = next(
        (
            _mapping(requirement)
            for requirement in requirements
            if all(_is_supported(schemes, name, document) for name in _mapping(requirement))
        ),
        _mapping(requirements[0]),
    )
    for scheme_name in chosen:
        scheme = _mapping(_resolved(document, schemes.get(scheme_name)))
        _apply_scheme(builder, scheme_name, scheme, request)


def _is_supported(schemes: dict[str, Json], name: str, document: dict[str, Json]) -> bool:
    scheme = _mapping(_resolved(document, schemes.get(name)))
    kind = scheme.get("type")
    if kind in ("apiKey", "basic"):
        return True
    return kind == "http" and _text(scheme.get("scheme")).lower() in ("bearer", "basic")


def _apply_scheme(
    builder: plan.PlanBuilder, scheme_name: str, scheme: dict[str, Json], request: _Request
) -> None:
    kind = scheme.get("type")
    http_scheme = _text(scheme.get("scheme")).lower()
    auth_is_free = isinstance(request.parts.auth, NoAuth)
    if not scheme:
        builder.skip(f"security scheme {scheme_name}", "it is not defined in this file")
        return

    secret = plan.secret_name(scheme_name)
    if kind == "http" and http_scheme == "bearer":
        name = builder.secret(secret, "", f"security scheme {scheme_name} (bearer token)")
        if auth_is_free:
            request.parts.auth = BearerAuth(token_secret=name)
        else:
            reference = f"Bearer {plan.secret_reference(name)}"
            request.parts.headers.append(("Authorization", reference))
    elif kind == "basic" or (kind == "http" and http_scheme == "basic"):
        if not auth_is_free:
            builder.skip(
                f"security scheme {scheme_name}",
                "a request has one authentication, and another scheme already uses it",
            )
            return
        name = builder.secret(secret, "", f"security scheme {scheme_name} (basic password)")
        request.parts.auth = _basic(name)
        request.parts.needs_values.append(
            f"the user name for security scheme {scheme_name} "
            f"(the check uses '{BASIC_USERNAME_PLACEHOLDER}' until it is changed)"
        )
    elif kind == "apiKey":
        _apply_api_key(builder, scheme_name, scheme, request, auth_is_free=auth_is_free)
    else:
        described = f"{kind} {http_scheme}".strip() if isinstance(kind, str) else "this scheme"
        builder.skip(
            f"security scheme {scheme_name}",
            f"{described} authentication is not supported; checks are created without it",
        )


def _basic(password_secret: str) -> RequestAuth:
    return BasicAuth(username=BASIC_USERNAME_PLACEHOLDER, password_secret=password_secret)


def _apply_api_key(
    builder: plan.PlanBuilder,
    scheme_name: str,
    scheme: dict[str, Json],
    request: _Request,
    *,
    auth_is_free: bool,
) -> None:
    key_name = _text(scheme.get("name"))
    location = scheme.get("in")
    name = builder.secret(
        plan.secret_name(scheme_name), "", f"security scheme {scheme_name} (API key)"
    )
    reference = plan.secret_reference(name)
    if location == "cookie":
        request.parts.headers.append(("Cookie", f"{key_name}={reference}"))
    elif location in ("header", "query") and auth_is_free:
        request.parts.auth = ApiKeyAuth(
            name=key_name, value_secret=name, location="header" if location == "header" else "query"
        )
    elif location == "header":
        request.parts.headers.append((key_name, reference))
    elif location == "query":
        request.query.append((key_name, reference))
    else:
        builder.skip(
            f"security scheme {scheme_name}", f"an API key in {location!r} is not supported"
        )


# --- assertions ---------------------------------------------------------------------


def _success_assertions(responses: dict[str, Json]) -> list[Assertion]:
    """The lowest documented 2xx, or any 2xx when none is (AC9)."""
    documented = sorted(
        int(code)
        for code in responses
        if code.isdigit() and SUCCESS_FLOOR <= int(code) <= SUCCESS_CEILING
    )
    if documented:
        return [Assertion(source="status_code", comparison="equals", target=str(documented[0]))]
    return [
        Assertion(source="status_code", comparison="greater_than", target=str(SUCCESS_FLOOR - 1)),
        Assertion(source="status_code", comparison="less_than", target=str(SUCCESS_CEILING + 1)),
    ]
