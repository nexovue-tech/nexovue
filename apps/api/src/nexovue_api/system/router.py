"""The first versioned route: which build is serving this request.

Small, but it establishes the shape — every product route lives under
``/api/v1`` from the very first one, so no client is ever asked to move.
"""

from __future__ import annotations

from fastapi import APIRouter

from nexovue_api.buildinfo import BuildInfo, build_info

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/info", summary="Which build is serving this request")
async def system_info() -> BuildInfo:
    return build_info()
