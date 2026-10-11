"""The single place an exception becomes an HTTP response.

Everything that can fail a request funnels through here, so every error the API
emits is RFC 9457 problem+json carrying the same correlation id the logs use.
Nothing internal — no stack trace, no SQL, no driver message — crosses this
boundary; the log line keyed by that correlation id has it all.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from nexovue_platform.telemetry.context import (
    CORRELATION_ID_HEADER,
    FIELD_CORRELATION_ID,
    current_correlation_id,
)
from nexovue_platform.telemetry.logging import get_logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from nexovue_api.errors import problems
from nexovue_api.errors.exceptions import DomainError, RateLimitExceededError

logger = get_logger(__name__)

# What a client is told when something we did not anticipate failed. The real
# reason is in the log line with this request's correlation id.
GENERIC_INTERNAL_DETAIL: Final = (
    "The request could not be completed. Quote the correlation id when reporting this."
)

# Statuses Starlette raises on its own — a route that does not exist, a method
# that is not allowed — mapped onto our registered types.
_STATUS_PROBLEMS: Final[dict[int, problems.ProblemType]] = {
    int(HTTPStatus.UNAUTHORIZED): problems.UNAUTHENTICATED,
    int(HTTPStatus.FORBIDDEN): problems.PERMISSION_DENIED,
    int(HTTPStatus.NOT_FOUND): problems.NOT_FOUND,
    int(HTTPStatus.CONFLICT): problems.CONFLICT,
    int(HTTPStatus.TOO_MANY_REQUESTS): problems.RATE_LIMIT_EXCEEDED,
    int(HTTPStatus.REQUEST_ENTITY_TOO_LARGE): problems.CONTENT_TOO_LARGE,
    int(HTTPStatus.SERVICE_UNAVAILABLE): problems.DEPENDENCY_UNAVAILABLE,
}


def install_error_handlers(app: FastAPI) -> None:
    """Route every exception type through the one mapping."""
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)


def problem_response(
    request: Request,
    problem: problems.ProblemType,
    detail: str,
    *,
    extensions: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build one problem+json response, correlation id included."""
    correlation_id = _correlation_id(request)
    body: dict[str, Any] = {
        "type": problem.uri,
        "title": problem.title,
        "status": problem.status,
        "detail": detail,
        # RFC 9457's instance identifies this occurrence. Ours is the correlation
        # id, so a support conversation and a log query use the same string.
        "instance": f"urn:nexovue:request:{correlation_id}",
        FIELD_CORRELATION_ID: correlation_id,
    }
    body.update(extensions or {})

    response_headers = {CORRELATION_ID_HEADER: correlation_id}
    response_headers.update(headers or {})
    return JSONResponse(
        status_code=problem.status,
        content=body,
        media_type=problems.CONTENT_TYPE,
        headers=response_headers,
    )


async def _handle_domain_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, DomainError)
    headers = dict(getattr(error, "headers", {}))
    if isinstance(error, RateLimitExceededError):
        headers["Retry-After"] = str(error.retry_after_seconds)

    _log_problem(error.problem, detail=error.detail, path=request.url.path)
    return problem_response(
        request,
        error.problem,
        error.detail,
        extensions=error.extensions,
        headers=headers,
    )


async def _handle_request_validation_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    problem = problems.VALIDATION_FAILED
    _log_problem(problem, detail="request validation failed", path=request.url.path)
    return problem_response(
        request,
        problem,
        problem.title,
        # Field name and reason only. The submitted value is left out: it is the
        # most likely place for a credential to be sitting.
        extensions={"errors": [_describe_validation_problem(item) for item in error.errors()]},
    )


async def _handle_http_exception(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)
    problem = _STATUS_PROBLEMS.get(error.status_code) or problems.unspecified(error.status_code)
    detail = str(error.detail) if error.detail else problem.title
    _log_problem(problem, detail=detail, path=request.url.path)
    return problem_response(request, problem, detail, headers=dict(error.headers or {}))


async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    """Anything unanticipated: generic on the wire, complete in the log.

    Public because the exception boundary middleware calls it directly, so both
    routes to a 500 produce the same body and the same log line.
    """
    logger.error(
        "unhandled exception",
        exc_info=error,
        path=request.url.path,
        method=request.method,
    )
    return problem_response(request, problems.INTERNAL_ERROR, GENERIC_INTERNAL_DETAIL)


def _describe_validation_problem(item: dict[str, Any]) -> dict[str, str]:
    location = ".".join(str(part) for part in item.get("loc", ()))
    return {"field": location, "reason": str(item.get("msg", ""))}


def _log_problem(problem: problems.ProblemType, *, detail: str, path: str) -> None:
    """Log at the level the status deserves: 5xx needs a human, 4xx is the caller."""
    event = "request failed"
    fields = {"problem_type": problem.uri, "status": problem.status, "path": path}
    if problem.status >= int(HTTPStatus.INTERNAL_SERVER_ERROR):
        logger.error(event, detail=detail, **fields)
    elif problem.status == int(HTTPStatus.TOO_MANY_REQUESTS):
        logger.warning(event, detail=detail, **fields)
    else:
        logger.info(event, detail=detail, **fields)


def _correlation_id(request: Request) -> str:
    """The id the middleware bound, with the request scope as the fallback."""
    return current_correlation_id() or str(getattr(request.state, "correlation_id", "unknown"))
