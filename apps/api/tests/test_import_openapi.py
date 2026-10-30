"""An OpenAPI description becomes one check per operation (FR13, AC9, AC10, EC6)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import pytest
from nexovue_platform.checks.config import (
    ApiCheckConfig,
    ApiKeyAuth,
    BasicAuth,
    BearerAuth,
    FormBody,
    HttpRequest,
    JsonBody,
    NoAuth,
    parse_config,
)

from nexovue_api.checkimports import openapi
from nexovue_api.checkimports.schemas import ImportItem, ImportPlan
from nexovue_api.errors.exceptions import InvalidRequestError

OPENAPI_3_2 = """
openapi: 3.2.0
info:
  title: Orders
  version: "1.0"
servers:
  - url: https://{region}.orders.example.com/v1
    variables:
      region:
        default: eu
  - url: https://staging.orders.example.com/v1
security:
  - bearerAuth: []
paths:
  /orders/{order_id}:
    parameters:
      - $ref: '#/components/parameters/OrderId'
    get:
      operationId: getOrder
      parameters:
        - name: expand
          in: query
          required: true
          schema: {type: string}
        - name: X-Tenant
          in: header
          required: true
          schema: {type: string}
          example: acme
      responses:
        201:
          description: made
        200:
          description: ok
  /orders:
    post:
      summary: Create an order
      requestBody:
        $ref: '#/components/requestBodies/NewOrder'
      responses:
        2XX:
          description: accepted
    query:
      responses:
        default:
          description: anything
  /public/status:
    get:
      security: []
      responses:
        "204":
          description: fine
components:
  parameters:
    OrderId:
      name: order_id
      in: path
      required: true
      schema:
        type: string
        example: ord_123
  requestBodies:
    NewOrder:
      required: true
      content:
        application/json:
          examples:
            simple:
              value: {sku: "A-1", quantity: 2}
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
"""

SWAGGER_2_0: dict[str, Any] = {
    "swagger": "2.0",
    "info": {"title": "Pets", "version": "1"},
    "host": "pets.example.com",
    "basePath": "/api",
    "schemes": ["https", "http"],
    "securityDefinitions": {
        "key": {"type": "apiKey", "in": "header", "name": "X-Api-Key"},
        "login": {"type": "basic"},
    },
    "security": [{"key": []}],
    "paths": {
        "/pets/{petId}": {
            "get": {
                "operationId": "showPet",
                "parameters": [{"name": "petId", "in": "path", "required": True, "type": "string"}],
                "responses": {"200": {"description": "ok"}, "404": {"description": "missing"}},
            },
        },
        "/pets": {
            "post": {
                "parameters": [
                    {
                        "name": "pet",
                        "in": "body",
                        "required": True,
                        "schema": {"type": "object", "example": {"name": "Rex"}},
                    }
                ],
                "security": [{"login": []}],
                "responses": {"201": {"description": "made"}},
            },
            "get": {
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "required": True,
                        "type": "integer",
                        "x-example": 25,
                    }
                ],
                "responses": {"200": {"description": "ok"}},
            },
        },
        "/pets/search": {
            "post": {
                "consumes": ["application/x-www-form-urlencoded"],
                "parameters": [
                    {"name": "q", "in": "formData", "type": "string", "x-example": "rex"}
                ],
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
}


def _minimal(**extra: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": "T", "version": "1"},
        "paths": {"/health": {"get": {"responses": {"200": {"description": "ok"}}}}},
    }
    document.update(extra)
    return document


def _items(plan: ImportPlan) -> dict[str, ImportItem]:
    for item in plan.items:
        assert isinstance(parse_config(item.config.model_dump(mode="json")), ApiCheckConfig)
    return {item.key: item for item in plan.items}


def _request(item: ImportItem) -> HttpRequest:
    assert isinstance(item.config, ApiCheckConfig)
    return item.config.request


def _statuses(item: ImportItem) -> list[tuple[str, str]]:
    return [(a.comparison, a.target) for a in _request(item).assertions]


# --- AC9: OpenAPI 3.2 ---------------------------------------------------------------


def test_an_openapi_3_2_description_lists_one_check_per_operation() -> None:
    plan = openapi.preview(OPENAPI_3_2, server_url=None)

    assert plan.title == "Orders"
    assert sorted(_items(plan)) == [
        "GET /orders/{order_id}",
        "GET /public/status",
        "POST /orders",
        "QUERY /orders",
    ]


def test_each_operation_asserts_its_lowest_documented_success_status() -> None:
    items = _items(openapi.preview(OPENAPI_3_2, server_url=None))

    assert _statuses(items["GET /orders/{order_id}"]) == [("equals", "200")]
    assert _statuses(items["GET /public/status"]) == [("equals", "204")]


def test_an_operation_without_a_numbered_success_status_asserts_any_2xx() -> None:
    items = _items(openapi.preview(OPENAPI_3_2, server_url=None))

    assert _statuses(items["POST /orders"]) == [("greater_than", "199"), ("less_than", "300")]


def test_servers_are_listed_with_their_variables_filled_and_the_first_is_used() -> None:
    plan = openapi.preview(OPENAPI_3_2, server_url=None)

    assert plan.servers == [
        "https://eu.orders.example.com/v1",
        "https://staging.orders.example.com/v1",
    ]
    item = _items(plan)["GET /public/status"]
    assert item.url == "https://eu.orders.example.com/v1/public/status"


def test_the_chosen_server_is_used_when_it_is_one_the_description_names() -> None:
    plan = openapi.preview(OPENAPI_3_2, server_url="https://staging.orders.example.com/v1")

    chosen = urlsplit(_items(plan)["GET /public/status"].url)
    assert (chosen.scheme, chosen.hostname) == ("https", "staging.orders.example.com")


def test_a_documented_example_fills_a_path_parameter_through_a_local_reference() -> None:
    item = _items(openapi.preview(OPENAPI_3_2, server_url=None))["GET /orders/{order_id}"]

    assert item.url == "https://eu.orders.example.com/v1/orders/ord_123"
    assert item.name == "getOrder"


def test_a_required_parameter_without_an_example_asks_for_a_value() -> None:
    plan = openapi.preview(OPENAPI_3_2, server_url=None)
    item = _items(plan)["GET /orders/{order_id}"]

    request = _request(item)
    assert [(q.name, q.value) for q in request.query] == [("expand", "{{expand}}")]
    assert [(h.name, h.value) for h in request.headers] == [("X-Tenant", "acme")]
    assert item.needs_values == ["query parameter expand has no example"]
    assert ("expand", "") in [(v.name, v.value) for v in plan.variables]


def test_a_json_request_body_example_becomes_the_body() -> None:
    item = _items(openapi.preview(OPENAPI_3_2, server_url=None))["POST /orders"]

    body = _request(item).body
    assert isinstance(body, JsonBody)
    assert json.loads(body.content) == {"sku": "A-1", "quantity": 2}
    assert item.name == "Create an order"


def test_a_bearer_security_scheme_asks_for_a_secret_and_an_empty_requirement_means_none() -> None:
    plan = openapi.preview(OPENAPI_3_2, server_url=None)
    items = _items(plan)

    assert _request(items["POST /orders"]).auth == BearerAuth(token_secret="BEARER_AUTH")
    assert _request(items["GET /public/status"]).auth == NoAuth()
    assert [(s.name, s.value) for s in plan.secrets] == [("BEARER_AUTH", "")]


# --- AC9: Swagger 2.0 ---------------------------------------------------------------


def test_a_swagger_2_0_description_lists_its_operations_on_its_host() -> None:
    plan = openapi.preview(json.dumps(SWAGGER_2_0), server_url=None)
    items = _items(plan)

    assert sorted(items) == ["GET /pets", "GET /pets/{petId}", "POST /pets", "POST /pets/search"]
    assert plan.servers == ["https://pets.example.com/api"]
    assert _statuses(items["GET /pets/{petId}"]) == [("equals", "200")]
    assert _statuses(items["POST /pets"]) == [("equals", "201")]


def test_swagger_parameters_take_x_example_and_ask_where_there_is_none() -> None:
    plan = openapi.preview(json.dumps(SWAGGER_2_0), server_url=None)
    items = _items(plan)

    assert [(q.name, q.value) for q in _request(items["GET /pets"]).query] == [("limit", "25")]
    show = items["GET /pets/{petId}"]
    assert show.url == "https://pets.example.com/api/pets/{{petId}}"
    assert show.needs_values == ["path parameter petId has no example"]


def test_swagger_body_and_form_parameters_become_bodies() -> None:
    items = _items(openapi.preview(json.dumps(SWAGGER_2_0), server_url=None))

    body = _request(items["POST /pets"]).body
    assert isinstance(body, JsonBody)
    assert json.loads(body.content) == {"name": "Rex"}
    form = _request(items["POST /pets/search"]).body
    assert isinstance(form, FormBody)
    assert [(f.name, f.value) for f in form.fields] == [("q", "rex")]


def test_swagger_api_key_and_basic_schemes_become_secrets() -> None:
    plan = openapi.preview(json.dumps(SWAGGER_2_0), server_url=None)
    items = _items(plan)

    assert _request(items["GET /pets"]).auth == ApiKeyAuth(
        name="X-Api-Key", value_secret="KEY", location="header"
    )
    assert _request(items["POST /pets"]).auth == BasicAuth(username="user", password_secret="LOGIN")
    assert any("user name for security scheme login" in n for n in items["POST /pets"].needs_values)
    assert sorted(s.name for s in plan.secrets) == ["KEY", "LOGIN"]


# --- servers, security edge cases ---------------------------------------------------


def test_a_description_without_a_server_asks_for_a_base_url() -> None:
    plan = openapi.preview(json.dumps(_minimal()), server_url=None)

    item = plan.items[0]
    assert item.url == "{{base_url}}/health"
    assert ("base_url", "") in [(v.name, v.value) for v in plan.variables]
    assert item.needs_values == ["the description names no server URL — set base_url"]


def test_unsupported_security_is_listed_as_skipped() -> None:
    document = _minimal(
        security=[{"oauth": []}],
        components={
            "securitySchemes": {
                "oauth": {
                    "type": "oauth2",
                    "flows": {
                        "clientCredentials": {"tokenUrl": "https://auth.example.com", "scopes": {}}
                    },
                }
            }
        },
    )

    plan = openapi.preview(json.dumps(document), server_url=None)

    assert [(s.item, s.reason) for s in plan.skipped] == [
        (
            "security scheme oauth",
            "oauth2 authentication is not supported; checks are created without it",
        )
    ]


# --- AC10 and EC6 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "reference",
    ["https://evil.example/schema.json", "common.yaml#/components/schemas/Pet", "./pet.json"],
)
def test_a_reference_outside_the_file_is_refused_naming_it(reference: str) -> None:
    document = _minimal()
    document["paths"]["/health"]["get"]["responses"]["200"] = {"$ref": reference}

    with pytest.raises(InvalidRequestError) as refused:
        openapi.preview(json.dumps(document), server_url=None)

    assert reference in refused.value.detail
    assert "self-contained" in refused.value.detail


def test_a_local_reference_to_nothing_is_refused() -> None:
    document = _minimal()
    document["paths"]["/health"]["get"]["responses"]["200"] = {"$ref": "#/components/nope"}

    with pytest.raises(InvalidRequestError, match="points to nothing"):
        openapi.preview(json.dumps(document), server_url=None)


def test_an_invalid_description_is_refused_naming_where() -> None:
    document = _minimal()
    del document["info"]["version"]

    with pytest.raises(InvalidRequestError) as refused:
        openapi.preview(json.dumps(document), server_url=None)

    assert "not valid at #/info" in refused.value.detail
    assert "'version' is a required property" in refused.value.detail


@pytest.mark.parametrize(
    ("declared", "named"),
    [
        ({"openapi": "4.0.0"}, "version '4.0.0'"),
        ({"swagger": "1.2"}, "version '1.2'"),
        ({}, "no OpenAPI version"),
    ],
)
def test_an_unsupported_version_is_refused(declared: dict[str, str], named: str) -> None:
    document = {"info": {"title": "T", "version": "1"}, "paths": {}, **declared}

    with pytest.raises(InvalidRequestError) as refused:
        openapi.preview(json.dumps(document), server_url=None)

    assert named in refused.value.detail
    assert "Swagger 2.0 and OpenAPI 3.0, 3.1 and 3.2" in refused.value.detail


def test_malformed_json_is_refused_naming_its_line() -> None:
    with pytest.raises(InvalidRequestError, match="line 2, column"):
        openapi.preview('{"openapi": "3.0.3",\n "info": }', server_url=None)


def test_a_file_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(InvalidRequestError, match="object at its top level"):
        openapi.preview("- just\n- a list\n", server_url=None)


def test_more_than_500_operations_are_refused_naming_the_limit() -> None:
    paths = {f"/r{n}": {"get": {"responses": {"200": {"description": "ok"}}}} for n in range(501)}

    with pytest.raises(InvalidRequestError, match="at most 500"):
        openapi.preview(json.dumps(_minimal(paths=paths)), server_url=None)
