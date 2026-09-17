"""A Postman collection v2.1 becomes checks; its scripts are listed, never run (FR14)."""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest
from nexovue_platform.checks.config import (
    MAX_RUN_SECONDS,
    ApiCheckConfig,
    ApiKeyAuth,
    BasicAuth,
    BearerAuth,
    FormBody,
    HttpRequest,
    JsonBody,
    MultipartBody,
    MultipartField,
    MultistepCheckConfig,
    NoAuth,
    TextBody,
    XmlBody,
    parse_config,
)

from nexovue_api.checkimports import postman
from nexovue_api.checkimports.schemas import ImportItem, ImportPlan
from nexovue_api.errors.exceptions import InvalidRequestError

SCHEMA_2_1 = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
SCHEMA_2_0 = "https://schema.getpostman.com/json/collection/v2.0.0/collection.json"

# If this ran, it would make a request anyone watching could see.
OBSERVABLE_SCRIPT = [
    "pm.sendRequest('https://observer.example/ran', function () {});",
    "require('child_process').execSync('touch /tmp/postman-script-ran');",
]


def _collection(items: list[dict[str, Any]], **extra: Any) -> str:
    return json.dumps({"info": {"name": "Shop API", "schema": SCHEMA_2_1}, "item": items, **extra})


def _request(name: str, url: Any, **request: Any) -> dict[str, Any]:
    return {"name": name, "request": {"method": "GET", "url": url, **request}}


def _preview(text: str, *, environment: str | None = None, folders: Any = "separate") -> ImportPlan:
    plan = postman.preview(text, environment=environment, folders=folders)
    for item in plan.items:
        parse_config(item.config.model_dump(mode="json"))
    return plan


def _items(plan: ImportPlan) -> dict[str, ImportItem]:
    return {item.key: item for item in plan.items}


def _api(item: ImportItem) -> HttpRequest:
    assert isinstance(item.config, ApiCheckConfig)
    assert item.config.request.assertions, "every imported check must be able to fail (BR1)"
    return item.config.request


def _skipped(plan: ImportPlan) -> dict[str, str]:
    return {skipped.item: skipped.reason for skipped in plan.skipped}


# --- AC11: scripts --------------------------------------------------------------------


def test_scripts_are_listed_as_skipped_and_never_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("an import opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    text = _collection(
        [
            {
                **_request("List orders", "https://shop.example.com/orders"),
                "event": [
                    {
                        "listen": "prerequest",
                        "script": {"type": "text/javascript", "exec": OBSERVABLE_SCRIPT},
                    },
                    {"listen": "test", "script": {"exec": "pm.test('ok', () => {})"}},
                ],
            }
        ],
        event=[{"listen": "prerequest", "script": {"exec": OBSERVABLE_SCRIPT}}],
    )

    plan = _preview(text)

    assert _api(plan.items[0]).url == "https://shop.example.com/orders"
    assert _skipped(plan) == {
        "collection: pre-request script": postman.SCRIPT_NOT_RUN,
        "List orders: pre-request script": postman.SCRIPT_NOT_RUN,
        "List orders: test script": postman.SCRIPT_NOT_RUN,
    }


def test_an_empty_script_is_not_reported() -> None:
    text = _collection(
        [
            {
                **_request("Ping", "https://shop.example.com/ping"),
                "event": [{"listen": "test", "script": {"exec": [""]}}],
            }
        ]
    )

    assert _preview(text).skipped == []


# --- AC12: folders --------------------------------------------------------------------


FOLDER = {
    "name": "Checkout",
    "item": [
        {
            **_request("Create cart", "https://shop.example.com/carts"),
            "request": {"method": "POST", "url": "https://shop.example.com/carts"},
        },
        _request("Add item", "https://shop.example.com/carts/1/items"),
        _request("Pay", "https://shop.example.com/carts/1/pay"),
        {"name": "Refunds", "item": [_request("Refund", "https://shop.example.com/refunds")]},
    ],
}


def test_a_folder_imported_as_multistep_is_one_check_with_its_requests_in_order() -> None:
    plan = _preview(
        _collection([FOLDER, _request("Health", "https://shop.example.com/health")]),
        folders="multistep",
    )
    items = _items(plan)

    assert sorted(items) == ["Checkout", "Checkout / Refunds", "Health"]
    checkout = items["Checkout"].config
    assert isinstance(checkout, MultistepCheckConfig)
    assert [(s.name, s.method) for s in checkout.steps] == [
        ("Create cart", "POST"),
        ("Add item", "GET"),
        ("Pay", "GET"),
    ]
    assert all(step.assertions for step in checkout.steps)
    assert isinstance(items["Health"].config, ApiCheckConfig)


def test_a_folder_imported_separately_names_each_check_for_its_folder() -> None:
    plan = _preview(_collection([FOLDER]), folders="separate")

    assert [item.name for item in plan.items] == [
        "Checkout / Create cart",
        "Checkout / Add item",
        "Checkout / Pay",
        "Checkout / Refunds / Refund",
    ]


def test_a_folder_longer_than_a_multistep_check_is_split_and_fits_one_run() -> None:
    requests = [_request(f"Step {n}", f"https://shop.example.com/s/{n}") for n in range(12)]
    plan = _preview(_collection([{"name": "Long", "item": requests}]), folders="multistep")

    parts = [item.config for item in plan.items]
    assert [item.name for item in plan.items] == ["Long (part 1 of 2)", "Long (part 2 of 2)"]
    assert all(isinstance(config, MultistepCheckConfig) for config in parts)
    steps = [len(c.steps) for c in parts if isinstance(c, MultistepCheckConfig)]
    assert steps == [10, 2]
    for config in parts:
        assert isinstance(config, MultistepCheckConfig)
        assert sum(step.timeout_seconds for step in config.steps) <= MAX_RUN_SECONDS


# --- AC13: versions -------------------------------------------------------------------


def test_a_v2_0_collection_is_refused_with_re_export_instructions() -> None:
    text = json.dumps({"info": {"name": "Old", "schema": SCHEMA_2_0}, "item": []})

    with pytest.raises(InvalidRequestError) as refused:
        _preview(text)

    assert "v2.0" in refused.value.detail
    assert "Export, choose Collection v2.1" in refused.value.detail


def test_a_v1_collection_is_refused_with_re_export_instructions() -> None:
    text = json.dumps({"id": "abc", "name": "Ancient", "requests": [{"url": "https://x.example"}]})

    with pytest.raises(InvalidRequestError, match=r"v1.*Collection v2\.1"):
        _preview(text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('{"info": {"name": "x"}, "item": []}', "does not declare the Postman v2.1 format"),
        ('{"hello": "world"}', "not a Postman collection"),
        ("[1, 2]", "object at its top level"),
        ('{"info": \n  nope}', "line 2, column 3"),
    ],
)
def test_a_file_that_is_not_a_v2_1_collection_is_refused(text: str, message: str) -> None:
    with pytest.raises(InvalidRequestError, match=message):
        _preview(text)


# --- variables and environments -------------------------------------------------------


def test_collection_variables_become_variables_and_the_environment_overrides_them() -> None:
    text = _collection(
        [_request("Orders", "{{base_url}}/orders?page={{page}}")],
        variable=[
            {"key": "base_url", "value": "https://staging.shop.example.com"},
            {"key": "page", "value": "1"},
        ],
    )
    environment = json.dumps(
        {
            "name": "Prod",
            "values": [{"key": "base_url", "value": "https://shop.example.com", "enabled": True}],
        }
    )

    plan = _preview(text, environment=environment)

    assert {(v.name, v.value) for v in plan.variables} == {
        ("base_url", "https://shop.example.com"),
        ("page", "1"),
    }
    assert _api(plan.items[0]).url == "{{base_url}}/orders?page={{page}}"
    assert plan.items[0].needs_values == []


def test_an_environment_secret_becomes_a_secret_and_its_references_are_rewritten() -> None:
    text = _collection(
        [
            _request(
                "Me",
                "https://shop.example.com/me",
                header=[{"key": "X-Session", "value": "{{session}}"}],
            )
        ]
    )
    environment = json.dumps(
        {"values": [{"key": "session", "value": "sess-abc-123", "type": "secret", "enabled": True}]}
    )

    plan = _preview(text, environment=environment)

    assert [(s.name, s.value) for s in plan.secrets] == [("session", "sess-abc-123")]
    assert [(h.name, h.value) for h in _api(plan.items[0]).headers] == [
        ("X-Session", "{{secret.session}}")
    ]
    assert plan.variables == []
    assert "sess-abc-123" not in plan.items[0].config.model_dump_json()


def test_a_query_url_object_with_path_variables_is_read() -> None:
    url = {
        "raw": "https://shop.example.com/orders/:order_id?expand=items",
        "host": ["shop", "example", "com"],
        "path": ["orders", ":order_id"],
        "query": [
            {"key": "expand", "value": "items"},
            {"key": "debug", "value": "1", "disabled": True},
        ],
        "variable": [{"key": "order_id", "value": ""}],
    }

    plan = _preview(_collection([_request("Order", url)]))

    request = _api(plan.items[0])
    assert request.url == "https://shop.example.com/orders/{{order_id}}"
    assert [(q.name, q.value) for q in request.query] == [("expand", "items")]
    assert plan.items[0].needs_values == ["path variable order_id has no value"]


def test_a_dynamic_variable_asks_to_be_replaced() -> None:
    plan = _preview(_collection([_request("Trace", "https://shop.example.com/t/{{$guid}}")]))

    assert plan.items[0].needs_values == [
        "{{$guid}} is not a name nexovue can define — replace it with a value or a variable"
    ]


# --- EC8: authentication --------------------------------------------------------------


def test_inherited_bearer_auth_with_a_literal_token_becomes_a_secret() -> None:
    text = _collection(
        [_request("Orders", "https://shop.example.com/orders")],
        auth={
            "type": "bearer",
            "bearer": [{"key": "token", "value": "tok-live-42", "type": "string"}],
        },
    )

    plan = _preview(text)

    assert _api(plan.items[0]).auth == BearerAuth(token_secret="SHOP_EXAMPLE_COM_TOKEN")
    assert [(s.name, s.value) for s in plan.secrets] == [("SHOP_EXAMPLE_COM_TOKEN", "tok-live-42")]
    assert "tok-live-42" not in plan.items[0].config.model_dump_json()


def test_a_variable_used_as_a_credential_becomes_a_secret_everywhere() -> None:
    text = _collection(
        [
            _request("Orders", "https://shop.example.com/orders"),
            _request(
                "Debug",
                "https://shop.example.com/debug?token={{apiToken}}",
                auth={"type": "noauth"},
            ),
        ],
        auth={"type": "bearer", "bearer": [{"key": "token", "value": "{{apiToken}}"}]},
        variable=[{"key": "apiToken", "value": "tok-var-77"}],
    )

    plan = _preview(text)
    items = _items(plan)

    assert _api(items["Orders"]).auth == BearerAuth(token_secret="apiToken")
    assert _api(items["Debug"]).auth == NoAuth()
    assert _api(items["Debug"]).url == "https://shop.example.com/debug?token={{secret.apiToken}}"
    assert [(s.name, s.value) for s in plan.secrets] == [("apiToken", "tok-var-77")]
    assert plan.variables == []


def test_basic_and_api_key_auth_become_secrets() -> None:
    text = _collection(
        [
            _request(
                "Basic",
                "https://shop.example.com/a",
                auth={
                    "type": "basic",
                    "basic": [
                        {"key": "username", "value": "ops"},
                        {"key": "password", "value": "pw-1234"},
                    ],
                },
            ),
            _request(
                "Key",
                "https://shop.example.com/b",
                auth={
                    "type": "apikey",
                    "apikey": [
                        {"key": "key", "value": "api_key"},
                        {"key": "value", "value": "k-5678"},
                        {"key": "in", "value": "query"},
                    ],
                },
            ),
        ]
    )

    plan = _preview(text)
    items = _items(plan)

    assert _api(items["Basic"]).auth == BasicAuth(
        username="ops", password_secret="SHOP_EXAMPLE_COM_PASSWORD"
    )
    assert _api(items["Key"]).auth == ApiKeyAuth(
        name="api_key", value_secret="SHOP_EXAMPLE_COM_API_KEY", location="query"
    )
    assert "pw-1234" not in items["Basic"].config.model_dump_json()
    assert "k-5678" not in items["Key"].config.model_dump_json()


def test_an_unsupported_auth_type_is_listed_as_skipped() -> None:
    text = _collection(
        [_request("Signed", "https://shop.example.com/s", auth={"type": "awsv4", "awsv4": []})]
    )

    plan = _preview(text)

    assert _api(plan.items[0]).auth == NoAuth()
    assert "Signed: awsv4 authentication" in _skipped(plan)


def test_a_literal_authorization_header_becomes_a_secret() -> None:
    text = _collection(
        [
            _request(
                "Me",
                "https://shop.example.com/me",
                header=[
                    {"key": "Authorization", "value": "Bearer hdr-token-1"},
                    {"key": "X-Debug", "value": "1", "disabled": True},
                ],
            )
        ]
    )

    plan = _preview(text)

    request = _api(plan.items[0])
    assert request.auth == BearerAuth(token_secret="SHOP_EXAMPLE_COM_TOKEN")
    assert request.headers == []


# --- bodies ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {"mode": "raw", "raw": '{"a": 1}', "options": {"raw": {"language": "json"}}},
            JsonBody(content='{"a": 1}'),
        ),
        (
            {"mode": "raw", "raw": "<a/>", "options": {"raw": {"language": "xml"}}},
            XmlBody(content="<a/>"),
        ),
        ({"mode": "raw", "raw": "hello"}, TextBody(content="hello", content_type="text/plain")),
        (
            {"mode": "graphql", "graphql": {"query": "{ me { id } }", "variables": '{"x": 1}'}},
            JsonBody(content='{"query": "{ me { id } }", "variables": {"x": 1}}'),
        ),
    ],
)
def test_a_body_keeps_its_kind(body: dict[str, Any], expected: object) -> None:
    text = _collection([_request("B", "https://shop.example.com/b", method="POST", body=body)])

    assert _api(_preview(text).items[0]).body == expected


def test_urlencoded_and_form_data_bodies_become_form_and_multipart() -> None:
    text = _collection(
        [
            _request(
                "Form",
                "https://shop.example.com/f",
                method="POST",
                body={
                    "mode": "urlencoded",
                    "urlencoded": [
                        {"key": "a", "value": "1"},
                        {"key": "b", "value": "2", "disabled": True},
                    ],
                },
            ),
            _request(
                "Upload",
                "https://shop.example.com/u",
                method="POST",
                header=[{"key": "Content-Type", "value": "multipart/form-data"}],
                body={
                    "mode": "formdata",
                    "formdata": [
                        {"key": "note", "value": "hi", "type": "text"},
                        {"key": "doc", "type": "file", "src": "/Users/me/doc.pdf"},
                    ],
                },
            ),
            _request(
                "Binary",
                "https://shop.example.com/bin",
                method="PUT",
                body={"mode": "file", "file": {"src": "/Users/me/image.png"}},
            ),
        ]
    )

    items = _items(_preview(text))

    form = _api(items["Form"]).body
    assert isinstance(form, FormBody)
    assert [(f.name, f.value) for f in form.fields] == [("a", "1")]
    upload = _api(items["Upload"])
    assert upload.body == MultipartBody(parts=[MultipartField(name="note", value="hi")])
    assert upload.headers == []
    assert items["Upload"].needs_files == [
        "form field 'doc' refers to a local file /Users/me/doc.pdf — attach it"
    ]
    assert items["Binary"].needs_files == [
        "the request body is the local file /Users/me/image.png — attach it"
    ]


def test_a_request_turned_off_redirects_keeps_them_off() -> None:
    text = _collection(
        [
            {
                **_request("R", "https://shop.example.com/r"),
                "protocolProfileBehavior": {"followRedirects": False},
            }
        ]
    )

    assert _api(_preview(text).items[0]).follow_redirects is False
