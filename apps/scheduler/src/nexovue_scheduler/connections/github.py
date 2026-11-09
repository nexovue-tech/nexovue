"""GitHub, as an App (F22, FD11; design *Connecting GitHub*).

Each install's own private App signs in with a JSON Web Token signed RS256 by its key,
asks GitHub for its own installation, and mints an installation token narrowed to what
one call needs, held in memory and revoked at the end. The API holds no GitHub token;
F5 relies on this module too.

**What GitHub's answers look like was read from its documentation through web search on
2026-11-08, not from the pages themselves and never from the service** (docs.github.com
could not be fetched from the build session). The shapes used here, and the fake that
replays them (`tests/tracker_fake.py`), stay unverified until the founder's real run
after Phase 2 (T3.0, T4.8; foundation item 28):

- `POST /app-manifests/{code}/conversions` answers `id`, `slug`, `pem`, `webhook_secret`,
  plus `client_id` and `client_secret`, which are discarded (D41);
- `GET /app/installations`, with the JWT, lists the App's installations;
- `POST /app/installations/{id}/access_tokens` takes `permissions` (and may take
  `repository_ids`) and answers `token` and `expires_at`, an hour ahead; since
  2026-04-27 GitHub is rolling out a longer token format, so no length is assumed;
- `GET /installation/repositories` answers `total_count` and `repositories`, paged;
- `DELETE /installation/token` revokes the token it is called with (204).

The JWT's `iss` is the App's id: GitHub's current page recommends the client id and
still accepts the App id, and F22 keeps no client id (D41). Unverified against GitHub.

Every call goes through the guarded libcurl client, never follows a redirect, and is
classified into a `CONNECTION_FAILURES` word; a response body is never kept or logged.
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass
from typing import Any, Final

import jwt
from nexovue_platform.connections import is_manifest_code, is_repository_key

from nexovue_scheduler.connections.trackers import (
    Place,
    Reach,
    StoredConnection,
    TrackerFailure,
)
from nexovue_scheduler.probes.http_check import PreparedRequest, perform

GITHUB_API: Final = "https://api.github.com"
_API_VERSION: Final = ("X-GitHub-Api-Version", "2022-11-28")
_ACCEPT: Final = ("Accept", "application/vnd.github+json")
_USER_AGENT: Final = ("User-Agent", "nexovue-connections")
_JSON: Final = ("Content-Type", "application/json")
TIMEOUT_SECONDS: Final = 10

# GitHub accepts a JWT living at most 10 minutes; `iat` is backdated for clock drift.
JWT_BACKDATE_SECONDS: Final = 60
JWT_LIFETIME_SECONDS: Final = 540

# Reading what an installation reaches needs nothing but repository metadata.
METADATA_ONLY: Final = {"metadata": "read"}
REPOSITORIES_PER_PAGE: Final = 100
# 2,000 repositories is far beyond a small team's; a larger installation is read in part.
MAX_REPOSITORY_PAGES: Final = 20

_PASSING_STATUSES: Final = {429: "rate_limited"}


@dataclass(frozen=True, slots=True)
class GitHubEndpoint:
    """Where GitHub is. Only `tracker_for` and tests build one (AC89)."""

    base_url: str
    # The development escape the prober has; never true outside development.
    private_allowed: bool = False


@dataclass(frozen=True, slots=True)
class Converted:
    app_id: int
    app_slug: str
    private_key: str
    webhook_secret: str | None


def _call(
    endpoint: GitHubEndpoint,
    method: str,
    path: str,
    *,
    bearer: str | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    """One request, its JSON answer, or a `TrackerFailure` naming what went wrong."""
    headers = [_ACCEPT, _API_VERSION, _USER_AGENT]
    if bearer is not None:
        headers.append(("Authorization", f"Bearer {bearer}"))
    if body is not None:
        headers.append(_JSON)
    exchange = perform(
        PreparedRequest(
            method=method,
            url=f"{endpoint.base_url}{path}",
            headers=headers,
            body=None if body is None else json.dumps(body).encode(),
            follow_redirects=False,
            timeout_seconds=TIMEOUT_SECONDS,
        ),
        private_allowed=endpoint.private_allowed,
    )
    if exchange.error is not None:
        timed_out = "timed out" in exchange.error.lower()
        raise TrackerFailure("timed_out" if timed_out else "unreachable")
    status = exchange.status_code or 0
    if status == 204:
        return None
    if 200 <= status < 300:
        try:
            return json.loads(exchange.body)
        except ValueError as unreadable:
            raise TrackerFailure("unexpected_answer", status) from unreadable
    raise TrackerFailure(_failure_for(status, exchange.headers), status)


def _failure_for(status: int, headers: list[tuple[str, str]]) -> str:
    if status in _PASSING_STATUSES:
        return _PASSING_STATUSES[status]
    # GitHub's primary and secondary limits answer 403 with the remaining count at zero
    # or a `retry-after` (general knowledge; T4.8 checks a real one).
    named = {name.lower(): value for name, value in headers}
    if status == 403 and (named.get("x-ratelimit-remaining") == "0" or "retry-after" in named):
        return "rate_limited"
    if status in (401, 403):
        return "unauthorized"
    if status >= 500:
        return "server_error"
    return "unexpected_answer"


def convert_manifest(endpoint: GitHubEndpoint, code: str) -> Converted:
    """Trade a manifest code for the App's id, key and webhook secret. Unauthenticated:
    the one-hour code is the credential. The client id and secret are dropped here."""
    if not is_manifest_code(code):
        # The API refuses such a code; this is the second fence before it joins a path.
        raise TrackerFailure("manifest_refused")
    answer = _call(endpoint, "POST", f"/app-manifests/{code}/conversions")
    if not isinstance(answer, dict):
        raise TrackerFailure("unexpected_answer")
    app_id, slug, pem = answer.get("id"), answer.get("slug"), answer.get("pem")
    webhook_secret = answer.get("webhook_secret")
    if not (isinstance(app_id, int) and isinstance(slug, str) and isinstance(pem, str)):
        raise TrackerFailure("unexpected_answer")
    return Converted(
        app_id=app_id,
        app_slug=slug,
        private_key=pem,
        webhook_secret=webhook_secret if isinstance(webhook_secret, str) else None,
    )


class GitHubApp:
    """Signs in as one App, and mints installation tokens for it."""

    def __init__(self, endpoint: GitHubEndpoint, *, app_id: int, private_key: str) -> None:
        self._endpoint = endpoint
        self._app_id = app_id
        self._private_key = private_key

    def _jwt(self) -> str:
        issued = int(time.time()) - JWT_BACKDATE_SECONDS
        claims = {"iat": issued, "exp": issued + JWT_LIFETIME_SECONDS, "iss": str(self._app_id)}
        try:
            return jwt.encode(claims, self._private_key, algorithm="RS256")
        except (ValueError, TypeError, jwt.PyJWTError) as unusable:
            # A stored key that is not an RSA key a person must replace (FR43).
            raise TrackerFailure("key_unreadable") from unusable

    def describe(self) -> None:
        """`GET /app`: whether GitHub accepts this App's key."""
        _call(self._endpoint, "GET", "/app", bearer=self._jwt())

    def installation_ids(self) -> list[int]:
        answer = _call(self._endpoint, "GET", "/app/installations", bearer=self._jwt())
        if not isinstance(answer, list):
            raise TrackerFailure("unexpected_answer")
        return [entry["id"] for entry in answer if isinstance(entry.get("id"), int)]

    def token(self, installation_id: int, permissions: dict[str, str]) -> str:
        answer = _call(
            self._endpoint,
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            bearer=self._jwt(),
            body={"permissions": permissions},
        )
        token = answer.get("token") if isinstance(answer, dict) else None
        if not isinstance(token, str):
            raise TrackerFailure("unexpected_answer")
        return token

    def revoke(self, token: str) -> None:
        """Best effort (design, *Tokens*): a token not revoked lapses within its hour."""
        with contextlib.suppress(TrackerFailure):
            _call(self._endpoint, "DELETE", "/installation/token", bearer=token)

    def repositories(self, token: str, *, pages: int = MAX_REPOSITORY_PAGES) -> list[Place]:
        places: list[Place] = []
        for page in range(1, pages + 1):
            answer = _call(
                self._endpoint,
                "GET",
                f"/installation/repositories?per_page={REPOSITORIES_PER_PAGE}&page={page}",
                bearer=token,
            )
            listed = answer.get("repositories") if isinstance(answer, dict) else None
            if not isinstance(listed, list):
                raise TrackerFailure("unexpected_answer")
            places.extend(place for place in map(_place, listed) if place is not None)
            if len(listed) < REPOSITORIES_PER_PAGE:
                break
        return places


def _place(repository: Any) -> Place | None:
    """A repository by its strict key; one whose name is outside it is never stored."""
    if not isinstance(repository, dict):
        return None
    identifier, name, private = (
        repository.get("id"),
        repository.get("full_name"),
        repository.get("private"),
    )
    if not (isinstance(identifier, int) and isinstance(name, str) and isinstance(private, bool)):
        return None
    if not is_repository_key(name):
        return None
    return Place(external_id=str(identifier), key=name, is_private=private)


class GitHubTracker:
    """The GitHub adapter behind `Tracker`."""

    def __init__(self, app: GitHubApp, *, installation_id: int | None) -> None:
        self._app = app
        self._installation_id = installation_id

    @classmethod
    def for_connection(
        cls, connection: StoredConnection, endpoint: GitHubEndpoint
    ) -> GitHubTracker:
        if connection.github_app_id is None:
            raise TrackerFailure("app_not_installed")
        app = GitHubApp(
            endpoint, app_id=connection.github_app_id, private_key=connection.credential
        )
        return cls(app, installation_id=connection.github_installation_id)

    def check(self) -> None:
        """The App's key works, its installation answers, and it reaches a repository."""
        self._app.describe()
        if self._installation_id is None:
            raise TrackerFailure("app_not_installed")
        token = self._app.token(self._installation_id, METADATA_ONLY)
        try:
            if not self._app.repositories(token, pages=1):
                raise TrackerFailure("no_repository")
        finally:
            self._app.revoke(token)

    def reach(self) -> Reach:
        """The App's own installation, as GitHub lists it (AC38), and its repositories.
        A private App installs only on its owner's account, so one is expected."""
        installations = self._app.installation_ids()
        if not installations:
            raise TrackerFailure("app_not_installed")
        if len(installations) > 1:
            raise TrackerFailure("installation_ambiguous")
        [installation_id] = installations
        token = self._app.token(installation_id, METADATA_ONLY)
        try:
            places = tuple(self._app.repositories(token))
        finally:
            self._app.revoke(token)
        return Reach(places=places, github_installation_id=installation_id)
