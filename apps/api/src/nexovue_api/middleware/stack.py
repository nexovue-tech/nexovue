"""The middleware every nexovue app runs, in one place.

Shared by the customer API (`main.py`) and the staff admin app (`admin/main.py`,
ADR-016), so rate limiting, correlation ids, request logging and the exception
boundary are the same on both and cannot drift apart.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

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
