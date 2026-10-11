"""The middleware the API runs, in one place.

There was a second app once (ADR-016's staff admin API, superseded by ADR-018),
and this module is what kept rate limiting, correlation ids, request logging and
the exception boundary from drifting between them. One app applies it today; it
stays here because that is still where the whole stack is legible at once.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from nexovue_api.middleware.body_limit import RequestBodyLimitMiddleware
from nexovue_api.middleware.correlation import CorrelationIdMiddleware
from nexovue_api.middleware.errors import ExceptionBoundaryMiddleware
from nexovue_api.middleware.ratelimit import RateLimitMiddleware
from nexovue_api.middleware.request_log import RequestLogMiddleware
from nexovue_api.settings import Settings


def install_middleware(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(
        RateLimitMiddleware,
        enabled=settings.rate_limit_enabled,
        trusted_proxy_count=settings.trusted_proxy_count,
    )
    # Outside the rate limiter, whose auth tier peeks at the body: every byte any
    # layer reads is counted.
    app.add_middleware(RequestBodyLimitMiddleware)
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(ExceptionBoundaryMiddleware)

    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
