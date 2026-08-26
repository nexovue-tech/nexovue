"""The two probe endpoints.

``/healthz`` is liveness and touches nothing external. If it checked Postgres, a
single bad second on the database would restart every replica of every service
at once — turning a blip into an outage.

``/readyz`` is readiness: it probes each dependency this service actually needs
and names the one that failed, so the answer is diagnostic and not just a colour.

Neither lives under ``/api/v1``. They are operational endpoints, not product API,
and an orchestrator's manifest should never have to move when the API version does.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Final

from fastapi import APIRouter, Request, Response

from nexovue_api.health.registry import (
    STATUS_OK,
    CheckRegistry,
    Report,
    liveness_report,
    readiness_report,
)

LIVENESS_PATH: Final = "/healthz"
READINESS_PATH: Final = "/readyz"

# Probe answers are a snapshot of right now; a cached one is worse than none.
NO_STORE: Final = "no-store"

router = APIRouter(tags=["health"])


@router.get(
    LIVENESS_PATH,
    summary="Liveness: is this process working?",
    response_model_exclude_none=True,
)
async def liveness(response: Response) -> Report:
    response.headers["Cache-Control"] = NO_STORE
    return liveness_report()


@router.get(
    READINESS_PATH,
    summary="Readiness: can this instance serve traffic?",
    response_model_exclude_none=True,
)
async def readiness(request: Request, response: Response) -> Report:
    registry: CheckRegistry = request.app.state.health
    report = readiness_report(await registry.run())

    response.headers["Cache-Control"] = NO_STORE
    if report.status != STATUS_OK:
        response.status_code = int(HTTPStatus.SERVICE_UNAVAILABLE)
    return report
