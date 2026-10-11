"""Changes made with the session cookie come from the console's own site (group 9, A2).

The cookie is `SameSite=Lax`, which a sibling `*.nexovue.com` host — the
website — still rides. The browser already says where a request came from:
`Sec-Fetch-Site` (every browser since 2023), or failing that `Origin`. This is
the rule Go 1.25's `http.CrossOriginProtection` ships, kept to requests that
carry the session: an unsafe method from another site or origin is refused.

Not refused, on purpose:
- a request with neither header — not a browser, so not a forged one;
- a request without the session cookie — webhooks, heartbeats, API tokens and
  the MCP endpoint prove themselves otherwise;
- the payment processor posting the customer back from checkout, which changes
  nothing (`billing/router.return_from_checkout`).
"""

from __future__ import annotations

from typing import Final
from urllib.parse import urlsplit

from starlette.requests import Request, cookie_parser
from starlette.types import ASGIApp, Receive, Scope, Send

from nexovue_api.auth.sessions import SESSION_COOKIE_NAME
from nexovue_api.errors import problems
from nexovue_api.errors.handlers import problem_response
from nexovue_api.settings import API_PREFIX

SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
FROM_HERE: Final = frozenset({"same-origin", "none"})
CHECKOUT_RETURN_PATH: Final = f"{API_PREFIX}/billing/checkout/return"
REFUSED_DETAIL: Final = "This change did not come from the nexovue console."


class CrossOriginMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _forged(scope):
            request = Request(scope, receive)
            await problem_response(request, problems.PERMISSION_DENIED, REFUSED_DETAIL)(
                scope, receive, send
            )
            return
        await self._app(scope, receive, send)


def _forged(scope: Scope) -> bool:
    if scope["method"] in SAFE_METHODS or scope["path"] == CHECKOUT_RETURN_PATH:
        return False
    headers = {name.decode("latin-1"): value.decode("latin-1") for name, value in scope["headers"]}
    if SESSION_COOKIE_NAME not in cookie_parser(headers.get("cookie", "")):
        return False
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site not in FROM_HERE
    origin = headers.get("origin")
    if origin is None:
        return False
    return bool(urlsplit(origin).netloc != headers.get("host", ""))
