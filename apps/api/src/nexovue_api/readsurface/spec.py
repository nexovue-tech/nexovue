"""The customer-facing description of the read API (F36, FR17, FR20).

The API already generates an OpenAPI document for the whole console — 138
endpoints, most of which an API token is refused. Pointing a customer's code
generator at *that* produces a client full of methods that answer 403, and the
customer cannot tell a mistake of theirs from a fault of ours.

So this builds a second document holding **only the read surface**, with the
bearer scheme declared on every operation. It is derived from the routes
themselves — a route is on the surface when it depends on `get_current_reader`,
which is the same rule `test_read_surface_structure.py` enforces — so the
published description cannot drift from what a token can actually reach. Adding
a route to the surface publishes it; it is one fact, not two.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, Final

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.openapi.utils import get_openapi

from nexovue_api.auth.dependencies import get_current_identity, get_current_reader
from nexovue_api.settings import API_PREFIX

TITLE: Final = "nexovue read API"
# The contract's version is the one in its path: everything here lives under
# `/api/v1`, and a second version would be a second prefix.
API_VERSION: Final = "v1"
BEARER_SCHEME: Final = "apiToken"

DESCRIPTION: Final = """
Read your organization's telemetry with an API token.

Create a token in the console under **Settings → API tokens**, then send it as
`Authorization: Bearer nvt_live_…`. A token reads; it cannot change anything,
and it reaches only the endpoints described here. A token scoped to one project
answers about any other exactly as it answers about a project that does not
exist.

Full guide, including rate limits and the MCP server for AI assistants:
https://nexovue.com/docs/api
""".strip()


def _paths_as_served(reachable: dict[str, list[Any]], version: str) -> dict[str, Any]:
    """The path items of each group, under the path customers actually call."""
    served: dict[str, Any] = {}
    for path, routes in reachable.items():
        described = get_openapi(title=TITLE, version=version, routes=routes)
        for item in described["paths"].values():
            served.setdefault(path, {}).update(item)
    return served


def _asks_for(dependant: Dependant, wanted: Callable[..., Any]) -> bool:
    """Whether a route depends on `wanted`, however deep in its own tree."""
    if dependant.call is wanted:
        return True
    return any(_asks_for(nested, wanted) for nested in dependant.dependencies)


def _reads_as_a_token(dependant: Dependant) -> bool:
    """Whether a token reaches this route.

    Asking for the reader is not enough on its own: every telemetry route takes
    the ClickHouse repository, and *that* is built from a reader — so by the
    first test alone half the console would be published as the read API. What
    makes a route reachable by a token is that its own principal is the reader
    and it does not also demand a person, which is exactly the rule
    `test_read_surface_structure.py` enforces on the source.
    """
    return _asks_for(dependant, get_current_reader) and not _asks_for(
        dependant, get_current_identity
    )


def _every_route(app: FastAPI) -> Iterator[Any]:
    """Every route the app serves, including the ones inside included routers.

    FastAPI keeps an included router nested rather than flattening its routes
    into `app.routes`, so walking `app.routes` alone finds four probes and
    nothing else — which is how an earlier version of this produced an empty
    document. Each nested entry yields *route contexts*, which carry the path
    the route is actually served at along with its dependency tree.
    """
    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            yield from contexts()
        else:
            yield route


def surface_by_path(app: FastAPI) -> dict[str, list[Any]]:
    """Every route an API token may reach, keyed by the path it is served at.

    The path comes from the context, never from the route's own — a route's own
    path is relative to *its* router, so `/logs/search` published itself as
    `/search`: a document describing an endpoint that does not exist. The
    context knows the whole chain of prefixes.
    """
    found: dict[str, list[Any]] = {}
    for route in _every_route(app):
        dependant = getattr(route, "dependant", None)
        original = getattr(route, "original_route", None)
        if dependant is None or original is None or not _reads_as_a_token(dependant):
            continue
        served = str(route.path).removeprefix(API_PREFIX)
        found.setdefault(served, []).append(original)
    return found


def surface_routes(app: FastAPI) -> list[Any]:
    """Every route an API token may reach, in no particular order."""
    return [route for routes in surface_by_path(app).values() for route in routes]


def read_surface_spec(
    app: FastAPI, *, version: str = API_VERSION, base_url: str = ""
) -> dict[str, Any]:
    """The OpenAPI document for the read surface, bearer scheme and all.

    `base_url` is this API's own public address when the deployment knows it
    (`API_PUBLIC_URL`); without one the server is the prefix alone, which a
    generated client resolves against whatever host it was pointed at.
    """
    reachable = surface_by_path(app)
    document = get_openapi(
        title=TITLE,
        version=version,
        description=DESCRIPTION,
        routes=[route for routes in reachable.values() for route in routes],
        servers=[{"url": f"{base_url.rstrip('/')}{API_PREFIX}"}],
    )
    # Re-keyed by the path each route is *served* at. `get_openapi` keys by the
    # route's own path, which is relative to its own router; the schemas it
    # produced are unaffected, so only the keys move.
    document["paths"] = _paths_as_served(reachable, version)
    document.setdefault("components", {})["securitySchemes"] = {
        BEARER_SCHEME: {
            "type": "http",
            "scheme": "bearer",
            "description": "An API token from Settings → API tokens (`nvt_live_…`).",
        }
    }
    # On the document rather than per operation: every endpoint here takes the
    # same credential, and one declaration cannot fall out of step with itself.
    document["security"] = [{BEARER_SCHEME: []}]
    return document
