"""A change made with the session cookie must come from the console's own site (group 9, A2).

The cookie is `SameSite=Lax`, which a sibling `*.nexovue.com` host still rides,
and nothing checked where a request came from. This is the rule Go 1.25's
`CrossOriginProtection` ships: the browser says, in `Sec-Fetch-Site` or failing
that `Origin`, and an unsafe request from elsewhere that carries the session is
refused. Requests without the cookie are not this rule's business — webhooks,
heartbeats, API tokens and the MCP endpoint authenticate otherwise.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexovue_api.auth.sessions import SESSION_COOKIE_NAME
from nexovue_api.billing import router as billing_router
from nexovue_api.errors.handlers import install_error_handlers
from nexovue_api.middleware.cross_origin import CHECKOUT_RETURN_PATH, CrossOriginMiddleware
from nexovue_api.settings import API_PREFIX

HOST = "app.nexovue.test"
CHANGE = f"{API_PREFIX}/projects"


def _client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(CrossOriginMiddleware)

    @app.post(CHANGE)
    @app.post(CHECKOUT_RETURN_PATH)
    @app.get(CHANGE)
    async def endpoint() -> dict[str, str]:
        return {"ok": "yes"}

    return TestClient(app, base_url=f"https://{HOST}")


def _send(
    method: str = "POST",
    path: str = CHANGE,
    *,
    cookie: bool = True,
    headers: dict[str, str] | None = None,
) -> int:
    client = _client()
    if cookie:
        client.cookies.set(SESSION_COOKIE_NAME, "a-session")
    return client.request(method, path, headers=headers or {}).status_code


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_change_from_another_site_carrying_the_session_is_refused(site: str) -> None:
    assert _send(headers={"sec-fetch-site": site}) == 403


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_a_change_from_the_console_itself_passes(site: str) -> None:
    assert _send(headers={"sec-fetch-site": site}) == 200


def test_without_sec_fetch_site_a_foreign_origin_is_refused() -> None:
    assert _send(headers={"origin": "https://www.nexovue.test"}) == 403


def test_without_sec_fetch_site_the_console_s_own_origin_passes() -> None:
    assert _send(headers={"origin": f"https://{HOST}"}) == 200


def test_a_request_with_neither_header_is_not_a_browser_and_passes() -> None:
    assert _send() == 200


def test_a_request_without_the_session_is_not_this_rule_s_business() -> None:
    assert _send(cookie=False, headers={"sec-fetch-site": "cross-site"}) == 200


def test_reading_is_never_refused() -> None:
    assert _send("GET", headers={"sec-fetch-site": "cross-site"}) == 200


def test_the_processor_s_return_from_checkout_passes() -> None:
    """The payment page posts back to us from its own site; it changes nothing."""
    assert _send(path=CHECKOUT_RETURN_PATH, headers={"sec-fetch-site": "cross-site"}) == 200


def test_the_exempt_path_is_the_billing_router_s_own() -> None:
    assert (
        f"{API_PREFIX}{billing_router.router.prefix}{billing_router.CHECKOUT_RETURN_PATH}"
    ) == CHECKOUT_RETURN_PATH
