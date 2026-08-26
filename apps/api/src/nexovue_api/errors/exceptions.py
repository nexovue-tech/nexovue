"""The one domain exception hierarchy.

Routers and services raise these. They never build an HTTP response, never pick
a status code, and never import ``fastapi.HTTPException`` — one handler owns the
translation, which is what keeps the wire format consistent as the API grows.
"""

from __future__ import annotations

from typing import Any

from nexovue_api.errors import problems


class DomainError(Exception):
    """Base for every failure the API reports to a client.

    ``detail`` is written for the caller and goes on the wire. Anything the
    caller must not see — a query, a driver message, a stack trace — belongs in
    the log, keyed by the correlation id, and never in here.
    """

    problem: problems.ProblemType = problems.INTERNAL_ERROR

    def __init__(self, detail: str | None = None, **extensions: Any) -> None:
        self.detail = detail or self.problem.title
        self.extensions = extensions
        super().__init__(self.detail)


class InvalidRequestError(DomainError):
    """The request was understood but its contents are not acceptable."""

    problem = problems.VALIDATION_FAILED


class UnauthenticatedError(DomainError):
    """No session, or a session that has expired."""

    problem = problems.UNAUTHENTICATED


class PermissionDeniedError(DomainError):
    """Authenticated, but not allowed to do this."""

    problem = problems.PERMISSION_DENIED


class NotFoundError(DomainError):
    """No such resource inside the caller's organization.

    Deliberately indistinguishable from a resource that exists in another
    organization: telling the two apart would leak the other tenant's data.
    """

    problem = problems.NOT_FOUND


class ConflictError(DomainError):
    """The request cannot be applied to the current state."""

    problem = problems.CONFLICT


class RateLimitExceededError(DomainError):
    """The caller is over one of its limits."""

    problem = problems.RATE_LIMIT_EXCEEDED

    def __init__(
        self,
        detail: str | None = None,
        *,
        retry_after_seconds: int,
        headers: dict[str, str] | None = None,
        **extensions: Any,
    ) -> None:
        super().__init__(detail, **extensions)
        self.retry_after_seconds = retry_after_seconds
        self.headers = headers or {}


class QueryTooLargeError(DomainError):
    """The query would read more rows than the per-query guard allows."""

    problem = problems.QUERY_TOO_LARGE


class DependencyUnavailableError(DomainError):
    """A store or broker this request needs did not answer."""

    problem = problems.DEPENDENCY_UNAVAILABLE


class InternalError(DomainError):
    """A failure with no better description. The detail stays generic on purpose."""

    problem = problems.INTERNAL_ERROR
