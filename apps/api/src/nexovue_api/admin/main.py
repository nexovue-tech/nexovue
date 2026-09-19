"""The staff admin console's app (F31, ADR-016).

A second FastAPI app over the same package — its services, queries and database
session — served as its own process on its own host, with its own cookie. It
has no customer route, and the customer app has none of its routes.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, FastAPI
from nexovue_platform.telemetry.logging import configure_logging

from nexovue_api.admin import router as admin_router
from nexovue_api.errors.handlers import install_error_handlers
from nexovue_api.health import router as health_router
from nexovue_api.lifespan import build_lifespan
from nexovue_api.middleware.stack import install_middleware
from nexovue_api.settings import Settings, load_settings_or_exit

ADMIN_PREFIX: Final = "/admin/v1"
SERVICE_NAME: Final = "admin-api"


def create_admin_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings_or_exit()
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        service=SERVICE_NAME,
        env=settings.env,
    )
    app = FastAPI(
        title="nexovue admin",
        lifespan=build_lifespan(settings),
        # Not published: this is not an API anyone outside nexovue builds against.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    install_middleware(app, settings)
    install_error_handlers(app)
    app.include_router(health_router.router)
    versioned = APIRouter(prefix=ADMIN_PREFIX)
    versioned.include_router(admin_router.router)
    app.include_router(versioned)
    return app


app = create_admin_app()
