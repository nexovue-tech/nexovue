"""Facts about this deployment, as opposed to about this customer.

The first versioned route was `/system/info`, and it established the shape —
every product route lives under ``/api/v1`` from the very first one, so no
client is ever asked to move.

`/system/ingest` is the second, and it exists because the answer to *"where do I
send telemetry"* was reachable exactly once. Signup returned it, the onboarding
screen printed it, and **nothing else in the product ever showed it again** — not
the projects list, not the API-keys screen, not the key-create response. A
customer who closed that tab could not recover the one value their exporter needs,
and no screen could tell them where to look.

Unauthenticated on purpose: it is the address telemetry is posted to, printed in
every setup page and pasted into deploy configs. It identifies nobody and grants
nothing.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter
from nexovue_platform.deployment import Deployment
from pydantic import BaseModel

from nexovue_api.buildinfo import BuildInfo, build_info
from nexovue_api.deps import AppSettings

router = APIRouter(prefix="/system", tags=["system"])


class IngestEndpoint(BaseModel):
    """Where a customer points an OpenTelemetry exporter."""

    # The base URL, never a full path: an SDK appends `/v1/traces` itself, and a
    # customer who pastes the path too gets `/v1/traces/v1/traces` and a 404.
    otlp_endpoint: str
    # The header the ingest key travels in. Returned rather than hard-coded in
    # the console so one spelling cannot drift from the receiver's.
    key_header: str


# The receiver's own name for it, in `apps/receiver/internal/auth`. Stated here
# because the API hands it to a screen that must print it exactly.
KEY_HEADER: Final = "x-nexovue-key"


class SystemInfo(BuildInfo):
    """The build, and which kind of install it is — the console hides billing on a
    customer's own install (nexovue_platform.deployment)."""

    deployment: Deployment


@router.get("/info", summary="Which build is serving this request, and what kind of install")
async def system_info(settings: AppSettings) -> SystemInfo:
    return SystemInfo(**build_info().model_dump(), deployment=settings.nexovue_deployment)


@router.get("/ingest", summary="Where to send telemetry")
async def ingest_endpoint(settings: AppSettings) -> IngestEndpoint:
    return IngestEndpoint(otlp_endpoint=settings.otlp_endpoint, key_header=KEY_HEADER)
