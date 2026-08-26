"""The registry of problem types, so clients branch on ``type`` and not on prose.

RFC 9457 makes ``type`` the stable, machine-readable identity of a failure.
Every problem this API can return is registered here exactly once; ``title`` and
``detail`` are for humans and may be reworded at any time, but a ``type`` URI is
a published contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Final

# Type URIs are identifiers, not endpoints. They are stable even before anything
# is published at them.
PROBLEM_TYPE_BASE: Final = "https://nexovue.com/problems/"

CONTENT_TYPE: Final = "application/problem+json"

# RFC 9457's default type: "this failure carries no further information beyond
# the status code". Never registered — it is the absence of a registered type.
ABOUT_BLANK: Final = "about:blank"


@dataclass(frozen=True, slots=True)
class ProblemType:
    """One kind of failure: its stable identity, its default wording and status."""

    slug: str
    title: str
    status: int

    @property
    def uri(self) -> str:
        if self.slug == ABOUT_BLANK:
            return ABOUT_BLANK
        return f"{PROBLEM_TYPE_BASE}{self.slug}"


_REGISTRY: Final[dict[str, ProblemType]] = {}


def register(slug: str, title: str, status: HTTPStatus) -> ProblemType:
    """Add a problem type, refusing a slug that is already taken."""
    if slug in _REGISTRY:
        raise ValueError(f"problem type {slug!r} is already registered")
    problem = ProblemType(slug=slug, title=title, status=int(status))
    _REGISTRY[slug] = problem
    return problem


def unspecified(status: int) -> ProblemType:
    """A problem for a status we have not given a type of its own."""
    try:
        title = HTTPStatus(status).phrase
    except ValueError:
        title = "Request failed"
    return ProblemType(slug=ABOUT_BLANK, title=title, status=status)


def registered() -> dict[str, ProblemType]:
    """Every registered problem type, keyed by slug."""
    return dict(_REGISTRY)


VALIDATION_FAILED: Final = register(
    "validation-failed",
    "The request body or parameters are not valid",
    HTTPStatus.UNPROCESSABLE_ENTITY,
)
UNAUTHENTICATED: Final = register(
    "unauthenticated",
    "The request has no valid session",
    HTTPStatus.UNAUTHORIZED,
)
PERMISSION_DENIED: Final = register(
    "permission-denied",
    "This session may not perform that action",
    HTTPStatus.FORBIDDEN,
)
NOT_FOUND: Final = register(
    "not-found",
    "No such resource in this organization",
    HTTPStatus.NOT_FOUND,
)
CONFLICT: Final = register(
    "conflict",
    "The request conflicts with the current state",
    HTTPStatus.CONFLICT,
)
RATE_LIMIT_EXCEEDED: Final = register(
    "rate-limit-exceeded",
    "Too many requests",
    HTTPStatus.TOO_MANY_REQUESTS,
)
QUERY_TOO_LARGE: Final = register(
    "query-too-large",
    "The query would read more data than the limit allows",
    HTTPStatus.BAD_REQUEST,
)
DEPENDENCY_UNAVAILABLE: Final = register(
    "dependency-unavailable",
    "A dependency this request needs is not available",
    HTTPStatus.SERVICE_UNAVAILABLE,
)
INTERNAL_ERROR: Final = register(
    "internal-error",
    "The request could not be completed",
    HTTPStatus.INTERNAL_SERVER_ERROR,
)
