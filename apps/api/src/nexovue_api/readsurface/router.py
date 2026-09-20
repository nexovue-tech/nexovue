"""Serving the read API's own description (F36, FR20). HTTP only.

Public, like the console's own document and like every published API
description: it names endpoints and field shapes, holds no customer data, and
every endpoint it describes still demands a token.

Built once per process rather than per request — the routes cannot change while
the app is running, and a customer's tooling fetches this on every generate.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Request

from nexovue_api.deps import AppSettings
from nexovue_api.readsurface.spec import read_surface_spec

router = APIRouter(tags=["read-api"])

READ_SPEC_PATH = "/openapi.read.json"


@lru_cache(maxsize=1)
def _spec(app: Any, base_url: str) -> dict[str, Any]:
    return read_surface_spec(app, base_url=base_url)


@router.get(
    READ_SPEC_PATH,
    summary="The read API, as OpenAPI",
    include_in_schema=False,
)
async def read_api_spec(request: Request, settings: AppSettings) -> dict[str, Any]:
    """Only the endpoints an API token may reach, with the bearer scheme declared."""
    return _spec(request.app, settings.api_public_url)
