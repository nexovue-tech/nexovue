"""The API conventions that are easy to break and hard to notice.

`docs/engineering/api.md` states them. Structural, like
`test_ratelimit_coverage.py`: a standard nothing checks is a standard that holds
until the next person writes a route without reading it.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Final

import pytest
from fastapi.responses import Response
from fastapi.routing import APIRoute

import nexovue_api

# What a downloadable response may be addressed as. A suffix rather than
# `?format=` because it is self-describing to a browser and to a person, and
# cannot be accidentally omitted the way a query parameter can.
FILE_EXTENSIONS: Final = (".csv",)


def _routers() -> list[tuple[str, object]]:
    found = []
    for module in pkgutil.walk_packages(nexovue_api.__path__, "nexovue_api."):
        if not module.name.endswith(".router"):
            continue
        router = getattr(importlib.import_module(module.name), "router", None)
        if router is not None:
            found.append((module.name, router))
    return found


def _file_routes() -> list[tuple[str, str, list[str]]]:
    """Every route that answers with something other than JSON, and its siblings."""
    routes = []
    for name, router in _routers():
        paths = [r.path for r in router.routes if isinstance(r, APIRoute)]  # type: ignore[attr-defined]
        for route in router.routes:  # type: ignore[attr-defined]
            # The bare `Response`, which is what a route serving bytes declares;
            # everything else in this API answers `JSONResponse`.
            if isinstance(route, APIRoute) and route.response_class is Response:
                routes.append((name, route.path, paths))
    return routes


_FILE_ROUTES: Final = _file_routes()


def test_the_scan_found_the_routes_it_is_meant_to_hold() -> None:
    """A structural test that matched nothing passes for the wrong reason."""
    assert _FILE_ROUTES, "no non-JSON route was found; the detection has drifted"


@pytest.mark.parametrize(
    ("module", "path"),
    [(module, path) for module, path, _ in _FILE_ROUTES],
)
def test_a_file_response_is_addressed_by_its_extension(module: str, path: str) -> None:
    assert path.endswith(FILE_EXTENSIONS), (
        f"{module} serves a file at {path}. `api.md` addresses one by suffixing the path "
        f"with its extension — never `?format=` or a separate `/export` route."
    )


@pytest.mark.parametrize(
    ("module", "path", "siblings"),
    _FILE_ROUTES,
)
def test_a_file_response_is_declared_above_any_wildcard(
    module: str, path: str, siblings: list[str]
) -> None:
    """`/traces/{trace_id}` would otherwise match `export.csv` as a trace id and
    answer a 404 that names neither mistake. FastAPI matches in declaration
    order, so this is about where the decorator sits, not about the path."""
    wildcards = [
        index for index, sibling in enumerate(siblings) if "{" in sibling and sibling != path
    ]
    if not wildcards:
        pytest.skip(f"{module} has no wildcard route to be shadowed by")

    assert siblings.index(path) < min(wildcards), (
        f"{module} declares {path} after a wildcard route, which will match it first."
    )
