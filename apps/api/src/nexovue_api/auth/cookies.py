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
