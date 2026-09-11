"""How the session token travels to the browser.

One place decides the cookie's attributes, because signup and login both set it
and a mismatch between them is the kind of bug that only shows up as a user
being silently signed out on one path and not the other.
"""

from __future__ import annotations

from typing import Final

from starlette.responses import Response

from nexovue_api.auth.sessions import SESSION_COOKIE_NAME
from nexovue_api.settings import Settings

# The cookie is for the API, not for scripts: nothing in the browser has any
# reason to read the token, and HttpOnly takes XSS off the table as a way to
# steal it. Lax still sends it on the top-level navigation that lands a user
# back on the app, while withholding it from cross-site form posts.
SAME_SITE: Final = "lax"
COOKIE_PATH: Final = "/"

# **No `domain` is set, and that omission is load-bearing.** A cookie with no
# Domain attribute is host-only: it goes back to exactly the host that set it and
# to no sibling. Setting `domain=".nexovue.com"` to make some subdomain work
# would send this session token to `ingest.nexovue.com` on every browser-door
# beacon — our own users' sessions, arriving at the endpoint that accepts
# unauthenticated public traffic from thousands of other people's pages.
# That is a privacy requirement rather than a preference.
# Do not add a Domain here.


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    """Attach a freshly minted session to the response."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_seconds,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=SAME_SITE,
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Remove the cookie. Attributes must match the ones it was set with."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=SAME_SITE,
    )
