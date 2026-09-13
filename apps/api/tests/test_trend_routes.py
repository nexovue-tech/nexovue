"""Every trend route is reachable, and answers as itself.

Written after `/error-groups/trend` returned 404 with "No such error group in this
window" — the body of the `/{group_id}` handler, which looked exactly like a route
shadowed by its sibling parameter. **It was not.** The API process had not reloaded the
new route yet, and this FastAPI version resolves a literal segment before a parameter
whatever order they are declared in; the test passes with the declarations either way.
The reorder in `errorgroups/router.py` is therefore convention, not a fix.

What remains worth asserting is narrower and still real: a trend route that is never
included, misspelled, or mounted under the wrong prefix answers 404, and every other
suite here calls service functions directly — which is the right place to prove what a
query returns and structurally cannot prove which URL reaches it.

No lifespan and no store. A route that exists gets as far as resolving its dependencies
and fails there, because `app.state.resources` is only set during startup. Any status but
404 means the request reached its own handler, which is the whole claim — and it needs
nothing running.
"""

from __future__ import annotations

from typing import Any, Final

import httpx
import pytest
from environment import TEST_ENVIRONMENT

from nexovue_api.main import create_app
from nexovue_api.settings import Settings

HTTP_NOT_FOUND: Final = 404

# The trend routes the redesign added. Add one here when you add one; the cost of
# forgetting is a 404 nobody sees until a screen renders a rule where a line should be.
TREND_ROUTES: Final = [
    "/api/v1/error-groups/trend",
    "/api/v1/synthetics/checks/trend",
    "/api/v1/services/trend",
]


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.parametrize("path", TREND_ROUTES)
async def test_a_trend_route_reaches_its_own_handler(path: str) -> None:
    app = create_app(Settings(**TEST_ENVIRONMENT))  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(
            path, params={"project_id": "00000000-0000-0000-0000-000000000000"}
        )

    assert response.status_code != HTTP_NOT_FOUND, (
        f"{path} answered 404: it is not registered, is misspelled, or is being matched "
        f"by another route. Check its router is included and its path is what the client "
        f"calls."
    )
