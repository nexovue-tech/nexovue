"""The application factory: configuration, logging, middleware, routes.

Middleware order is load-bearing and reads bottom-up in Starlette. Added in this
order, a request passes through:

1. exception boundary — nothing leaves as a raw exception, ever;
2. correlation id — so every later line, including a rejection, is correlated;
3. request log — so the line it writes carries that id;
4. rate limit — so a rejected request costs nothing beyond these three.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware

from nexovue_api.apikeys import router as apikeys_router
from nexovue_api.auth import router as auth_router
from nexovue_api.buildinfo import SERVICE_NAME
from nexovue_api.checks import router as checks_router
from nexovue_api.dbstats import router as dbstats_router
from nexovue_api.deploys import router as deploys_router
from nexovue_api.errorgroups import router as error_groups_router
from nexovue_api.errors.handlers import install_error_handlers
from nexovue_api.health import router as health_router
from nexovue_api.infrastructure import router as infrastructure_router
from nexovue_api.integrations import router as integrations_router
from nexovue_api.lifespan import build_lifespan
from nexovue_api.logs import router as logs_router
from nexovue_api.middleware.correlation import CorrelationIdMiddleware
from nexovue_api.middleware.errors import ExceptionBoundaryMiddleware
from nexovue_api.middleware.ratelimit import RateLimitMiddleware
from nexovue_api.middleware.request_log import RequestLogMiddleware
from nexovue_api.projects import router as projects_router
from nexovue_api.rum import router as rum_router
from nexovue_api.servicemap import router as service_map_router
from nexovue_api.services import router as services_router
from nexovue_api.settings import API_PREFIX, Settings, load_settings_or_exit
from nexovue_api.signup import router as signup_router
from nexovue_api.sourcemaps import router as source_maps_router
from nexovue_api.synthetics import router as synthetics_router
from nexovue_api.system import router as system_router
from nexovue_api.telemetry.logging import configure_logging
from nexovue_api.traces import router as traces_router
from nexovue_api.usage import router as usage_router

API_TITLE = "nexovue API"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API. Configuration is read and validated before anything else."""
    settings = settings or load_settings_or_exit()
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        service=SERVICE_NAME,
        env=settings.env,
    )

    app = FastAPI(
        title=API_TITLE,
        lifespan=build_lifespan(settings),
        # Ours are problem+json; FastAPI's default handlers would emit its own
        # `{"detail": ...}` shape alongside them.
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
    )

    # Dependencies read configuration from here, so a test that builds the app
    # with explicit settings gets those and not whatever the environment holds.
    app.state.settings = settings

    _install_middleware(app, settings)
    install_error_handlers(app)
    _install_routes(app)
    return app


def _install_middleware(app: FastAPI, settings: Settings) -> None:
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


def _install_routes(app: FastAPI) -> None:
    # Probes sit at the root: they are operational, not product API, and an
    # orchestrator manifest should not move when the API version does.
    app.include_router(health_router.router)

    versioned = APIRouter(prefix=API_PREFIX)
    versioned.include_router(system_router.router)
    versioned.include_router(signup_router.router)
    versioned.include_router(auth_router.router)
    versioned.include_router(projects_router.router)
    versioned.include_router(apikeys_router.router)
    versioned.include_router(apikeys_router.site_key_router)
    versioned.include_router(checks_router.router)
    versioned.include_router(deploys_router.router)
    versioned.include_router(integrations_router.router)
    versioned.include_router(services_router.router)
    versioned.include_router(service_map_router.router)
    versioned.include_router(synthetics_router.router)
    versioned.include_router(dbstats_router.router)
    versioned.include_router(infrastructure_router.router)
    versioned.include_router(usage_router.router)
    versioned.include_router(traces_router.router)
    versioned.include_router(logs_router.router)
    versioned.include_router(error_groups_router.router)
    versioned.include_router(rum_router.router)
    versioned.include_router(source_maps_router.router)
    app.include_router(versioned)


app = create_app()
