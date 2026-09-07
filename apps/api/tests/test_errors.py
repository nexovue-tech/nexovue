"""Every failure leaves as problem+json, correlated, with nothing internal in it."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nexovue_platform.telemetry.context import CORRELATION_ID_HEADER
from nexovue_platform.telemetry.logging import FORMAT_JSON, configure_logging
from pydantic import BaseModel

from nexovue_api.errors import problems
from nexovue_api.errors.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    RateLimitExceededError,
)
from nexovue_api.errors.handlers import install_error_handlers
from nexovue_api.middleware.correlation import CorrelationIdMiddleware
from nexovue_api.middleware.errors import ExceptionBoundaryMiddleware

SECRET_IN_TRACEBACK = "hunter2-do-not-leak-this"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN_ID = "00f067aa0ba902b7"


class Credentials(BaseModel):
    email: str
    password: str


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging(
        level="debug", log_format=FORMAT_JSON, service="api", env="test", stream=stream
    )
    yield stream
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def client(log_stream: io.StringIO) -> TestClient:
    """The real middleware and handlers over routes that fail on purpose."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(ExceptionBoundaryMiddleware)
    install_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise ValueError(f"connecting with password {SECRET_IN_TRACEBACK}")

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("No trace with that id in this organization.")

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError("That slug is taken.", suggested_slug="acme-2")

    @app.get("/throttled")
    async def throttled() -> None:
        raise RateLimitExceededError("Slow down.", retry_after_seconds=42)

    @app.post("/login")
    async def login(credentials: Credentials) -> dict[str, str]:
        return {"email": credentials.email}

    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_exception_becomes_problem_json(client: TestClient) -> None:
    response = client.get("/boom")

    assert response.status_code == 500
    assert response.headers["content-type"] == problems.CONTENT_TYPE
    assert response.json()["type"] == problems.INTERNAL_ERROR.uri


def test_internals_never_reach_the_client(client: TestClient) -> None:
    response = client.get("/boom")

    body = response.text
    assert SECRET_IN_TRACEBACK not in body
    assert "Traceback" not in body
    assert "ValueError" not in body


def test_the_correlation_id_links_the_response_to_the_log(
    client: TestClient, log_stream: io.StringIO
) -> None:
    response = client.get("/boom")

    correlation_id = response.json()["correlation_id"]
    assert response.headers[CORRELATION_ID_HEADER] == correlation_id
    assert response.json()["instance"].endswith(correlation_id)
    assert correlation_id in log_stream.getvalue()
    assert "Traceback" in log_stream.getvalue(), "the log is where internals belong"


def test_an_incoming_traceparent_becomes_the_correlation_id(client: TestClient) -> None:
    response = client.get("/boom", headers={"traceparent": f"00-{TRACE_ID}-{SPAN_ID}-01"})

    assert response.json()["correlation_id"] == TRACE_ID


def test_a_domain_exception_keeps_its_registered_type(client: TestClient) -> None:
    response = client.get("/missing")

    assert response.status_code == 404
    assert response.json()["type"] == problems.NOT_FOUND.uri
    assert response.json()["detail"] == "No trace with that id in this organization."


def test_extensions_ride_alongside_the_standard_members(client: TestClient) -> None:
    response = client.get("/conflict")

    assert response.status_code == 409
    assert response.json()["suggested_slug"] == "acme-2"


def test_a_rate_limit_carries_retry_after(client: TestClient) -> None:
    response = client.get("/throttled")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"


def test_validation_failures_report_the_field_but_not_the_value(client: TestClient) -> None:
    response = client.post("/login", json={"email": "a@b.com", "password": 12})

    assert response.status_code == 422
    assert response.json()["type"] == problems.VALIDATION_FAILED.uri
    assert response.json()["errors"][0]["field"] == "body.password"
    assert "12" not in str(response.json()["errors"])


def test_an_unrouted_path_is_a_problem_too(client: TestClient) -> None:
    response = client.get("/no-such-route")

    assert response.status_code == 404
    assert response.headers["content-type"] == problems.CONTENT_TYPE


def test_every_registered_problem_type_has_a_distinct_uri() -> None:
    types = problems.registered().values()
    uris = [problem.uri for problem in types]

    assert len(uris) == len(set(uris))
    assert all(uri.startswith(problems.PROBLEM_TYPE_BASE) for uri in uris)


def test_a_slug_cannot_be_registered_twice() -> None:
    with pytest.raises(ValueError, match="already registered"):
        problems.register("not-found", "Duplicate", problems.NOT_FOUND.status)  # type: ignore[arg-type]


def test_every_domain_error_maps_to_a_registered_problem() -> None:
    registered = set(problems.registered().values())

    for subclass in _all_subclasses(DomainError):
        assert subclass.problem in registered, f"{subclass.__name__} has an unregistered problem"


def _all_subclasses(root: type[DomainError]) -> list[type[DomainError]]:
    found: list[type[DomainError]] = []
    for subclass in root.__subclasses__():
        found.append(subclass)
        found.extend(_all_subclasses(subclass))
    return found


def test_an_untyped_status_falls_back_to_the_rfc_default_type() -> None:
    assert problems.unspecified(418).uri == problems.ABOUT_BLANK
    assert problems.unspecified(418).uri not in [p.uri for p in problems.registered().values()]
