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
- `DELETE /installation/token` revokes the token it is called with (204);
- Step 4's issue calls, from the REST *Issues* and *Repositories* pages as the design
  read them on 2026-11-02 and general knowledge where it says so: `GET /repos/{r}`
  carries `private`; `POST /repos/{r}/issues` takes `title`, `body` and `labels` and
  answers 201 with `id` and `number`; `GET /repos/{r}/issues/{n}` carries `state` and
  `state_reason`, and answers 410 for a deleted issue, 404 for one not found, and 301 to
  the new address for a transferred one (general knowledge; T4.8); `PATCH` takes `state`
  and `state_reason`; `POST .../comments` takes `body`; the list
  `GET /repos/{r}/issues?creator=&since=&state=all` reads the issues themselves, not a
  search index, and an App's issues are created by `<slug>[bot]` (general knowledge).

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
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Final
from urllib.parse import quote

import jwt
from nexovue_platform.cases import issue_close_reason
from nexovue_platform.connections import (
    ISSUE_LABEL,
    CommentText,
    IssueText,
    github_issue_key,
    is_manifest_code,
    is_repository_key,
    split_github_issue_key,
)

from nexovue_scheduler.connections.trackers import (
    IssueGone,
    IssueMissing,
    IssueMoved,
    IssueRead,
    IssueRef,
    IssueSeen,
    Place,
    PlaceKey,
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

# What one sync run's token may do, narrowed to one repository (design, *Tokens*).
ISSUES_AND_METADATA: Final = {"issues": "write", "metadata": "read"}
# The issues an App created since a first try, read to adopt a half-made one.
EXISTING_ISSUES_PER_PAGE: Final = 100
# GitHub's documented search is the issue list itself, so a write is believed visible
# at once (design, *Creating without duplicates*; T4.8 tries a create then a list).
GITHUB_SEARCH_SETTLES_SECONDS: Final = 0.0
# Paused this long after a rate limit that names no wait, a guess.
DEFAULT_RETRY_AFTER_SECONDS: Final = 60.0

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


@dataclass(frozen=True, slots=True)
class _Answer:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError as unreadable:
            raise TrackerFailure("unexpected_answer", self.status) from unreadable


def _exchange(
    endpoint: GitHubEndpoint,
    method: str,
    path: str,
    *,
    bearer: str | None = None,
    body: dict[str, Any] | None = None,
) -> _Answer:
    """One request and its raw answer; a transport failure is a `TrackerFailure`."""
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
    return _Answer(
        status=exchange.status_code or 0,
        headers={name.lower(): value for name, value in exchange.headers},
        body=exchange.body,
    )


def _call(
    endpoint: GitHubEndpoint,
    method: str,
    path: str,
    *,
    bearer: str | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    """One request, its JSON answer, or a `TrackerFailure` naming what went wrong."""
    answer = _exchange(endpoint, method, path, bearer=bearer, body=body)
    if answer.status == 204:
        return None
    if 200 <= answer.status < 300:
        return answer.json()
    raise _refusal(answer)


def _refusal(answer: _Answer) -> TrackerFailure:
    failure = _failure_for(answer.status, answer.headers)
    retry_after = _retry_after(answer.headers) if failure == "rate_limited" else None
    return TrackerFailure(failure, answer.status, retry_after=retry_after)


def _failure_for(status: int, headers: dict[str, str]) -> str:
    if status in _PASSING_STATUSES:
        return _PASSING_STATUSES[status]
    # GitHub's primary and secondary limits answer 403 with the remaining count at zero
    # or a `retry-after` (general knowledge; T4.8 checks a real one).
    if status == 403 and (headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers):
        return "rate_limited"
    if status in (401, 403):
        return "unauthorized"
    if status >= 500:
        return "server_error"
    return "unexpected_answer"


def _retry_after(headers: dict[str, str]) -> float:
    """How long GitHub asked for: `retry-after` in seconds or as a date, else the
    primary limit's reset time, else a default (AC49)."""
    named = headers.get("retry-after")
    if named is not None:
        try:
            return max(float(named), 0.0)
        except ValueError:
            with contextlib.suppress(TypeError, ValueError):
                return max((parsedate_to_datetime(named) - datetime.now(UTC)).total_seconds(), 0.0)
    reset = headers.get("x-ratelimit-reset")
    if reset is not None:
        with contextlib.suppress(ValueError):
            return max(float(reset) - time.time(), 0.0)
    return DEFAULT_RETRY_AFTER_SECONDS


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

    @property
    def endpoint(self) -> GitHubEndpoint:
        return self._endpoint

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

    def token(
        self,
        installation_id: int,
        permissions: dict[str, str],
        *,
        repository_ids: list[int] | None = None,
    ) -> str:
        asked: dict[str, Any] = {"permissions": permissions}
        if repository_ids is not None:
            asked["repository_ids"] = repository_ids
        answer = _exchange(
            self._endpoint,
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            bearer=self._jwt(),
            body=asked,
        )
        if repository_ids is not None and answer.status in (404, 422):
            # The installation no longer reaches that repository (general knowledge of
            # GitHub's answer; T4.8 removes a repository and records it).
            raise TrackerFailure("repository_unreachable", answer.status)
        if not 200 <= answer.status < 300:
            raise _refusal(answer)
        answer = answer.json()
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

    def __init__(
        self,
        app: GitHubApp,
        *,
        installation_id: int | None,
        app_slug: str | None = None,
        search_settles_seconds: float = GITHUB_SEARCH_SETTLES_SECONDS,
    ) -> None:
        self._app = app
        self._installation_id = installation_id
        self._app_slug = app_slug
        self.search_settles_seconds = search_settles_seconds
        # One token per repository for the run, minted on first use and revoked by
        # `finish` (design, *Tokens*): never stored.
        self._tokens: dict[str, str] = {}

    @classmethod
    def for_connection(
        cls,
        connection: StoredConnection,
        endpoint: GitHubEndpoint,
        *,
        search_settles_seconds: float = GITHUB_SEARCH_SETTLES_SECONDS,
    ) -> GitHubTracker:
        if connection.github_app_id is None:
            raise TrackerFailure("app_not_installed")
        app = GitHubApp(
            endpoint, app_id=connection.github_app_id, private_key=connection.credential
        )
        return cls(
            app,
            installation_id=connection.github_installation_id,
            app_slug=connection.github_app_slug,
            search_settles_seconds=search_settles_seconds,
        )

    # ------------------------------------------------------------ issue calls

    def _token_for(self, place: PlaceKey) -> str:
        if self._installation_id is None:
            raise TrackerFailure("app_not_installed")
        token = self._tokens.get(place.external_id)
        if token is None:
            token = self._app.token(
                self._installation_id,
                ISSUES_AND_METADATA,
                repository_ids=[int(place.external_id)],
            )
            self._tokens[place.external_id] = token
        return token

    def _repository(self, place: PlaceKey) -> str:
        if not is_repository_key(place.key):
            raise TrackerFailure("unexpected_answer")
        return place.key

    def _issue_path(self, place: PlaceKey, issue: IssueRef) -> str:
        repository, number = split_github_issue_key(issue.external_key)
        return f"/repos/{repository}/issues/{number}"

    def is_public(self, place: PlaceKey) -> bool:
        answer = _exchange(
            self._app.endpoint,
            "GET",
            f"/repos/{self._repository(place)}",
            bearer=self._token_for(place),
        )
        if answer.status in (403, 404):
            raise TrackerFailure("repository_unreachable", answer.status)
        if not 200 <= answer.status < 300:
            raise _refusal(answer)
        private = answer.json().get("private") if isinstance(answer.json(), dict) else None
        if not isinstance(private, bool):
            raise TrackerFailure("unexpected_answer", answer.status)
        return not private

    def create(self, place: PlaceKey, text: IssueText) -> IssueRef:
        answer = _call(
            self._app.endpoint,
            "POST",
            f"/repos/{self._repository(place)}/issues",
            bearer=self._token_for(place),
            body={"title": text.title, "body": text.markdown, "labels": [ISSUE_LABEL]},
        )
        return _issue_ref(place.key, answer)

    def find_existing(self, place: PlaceKey, link_back: str, since: datetime) -> IssueRef | None:
        if self._app_slug is None:
            raise TrackerFailure("app_not_installed")
        creator = quote(f"{self._app_slug}[bot]", safe="")
        stamp = quote(since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), safe="")
        listed = _call(
            self._app.endpoint,
            "GET",
            f"/repos/{self._repository(place)}/issues?creator={creator}&since={stamp}"
            f"&state=all&per_page={EXISTING_ISSUES_PER_PAGE}",
            bearer=self._token_for(place),
        )
        if not isinstance(listed, list):
            raise TrackerFailure("unexpected_answer")
        for issue in listed:
            body = issue.get("body") if isinstance(issue, dict) else None
            # The link back is the marker: the exact address, on a line of its own.
            if isinstance(body, str) and f": {link_back}\n" in body and "pull_request" not in issue:
                return _issue_ref(place.key, issue)
        return None

    def read(self, place: PlaceKey, issue: IssueRef) -> IssueRead:
        endpoint = self._app.endpoint
        token = self._token_for(place)
        answer = _exchange(endpoint, "GET", self._issue_path(place, issue), bearer=token)
        if answer.status == 410:
            return IssueGone()
        if answer.status == 404:
            return IssueMissing(repository_readable=self._readable(place, token))
        if answer.status in (301, 302, 307, 308):
            return self._moved(answer, token)
        if not 200 <= answer.status < 300:
            raise _refusal(answer)
        return _seen(answer.json())

    def _readable(self, place: PlaceKey, token: str) -> bool:
        answer = _exchange(
            self._app.endpoint, "GET", f"/repos/{self._repository(place)}", bearer=token
        )
        if answer.status in (403, 404):
            return False
        if not 200 <= answer.status < 300:
            raise _refusal(answer)
        return True

    def _moved(self, answer: _Answer, token: str) -> IssueMoved:
        """Row 11: the target is read through the guard, never stored as read, and its
        repository and number taken from the JSON under the strict forms."""
        nowhere = IssueMoved(repository=None, number=None, issue_id=None)
        base = self._app.endpoint.base_url
        location = answer.headers.get("location", "")
        if not location.startswith(f"{base}/"):
            return nowhere
        target = _exchange(self._app.endpoint, "GET", location.removeprefix(base), bearer=token)
        if not 200 <= target.status < 300:
            return nowhere
        document = target.json()
        if not isinstance(document, dict):
            return nowhere
        repository_url, number, issue_id = (
            document.get("repository_url"),
            document.get("number"),
            document.get("id"),
        )
        prefix = f"{base}/repos/"
        if not (
            isinstance(repository_url, str)
            and repository_url.startswith(prefix)
            and isinstance(number, int)
            and isinstance(issue_id, int)
        ):
            return nowhere
        repository = repository_url.removeprefix(prefix)
        if github_issue_key(repository, number) is None:
            return nowhere
        return IssueMoved(repository=repository, number=number, issue_id=str(issue_id))

    def set_status(self, place: PlaceKey, issue: IssueRef, wanted: str, close_reason: str) -> None:
        change: dict[str, Any] = {"state": wanted}
        if wanted == "closed":
            change["state_reason"] = close_reason
        answer = _exchange(
            self._app.endpoint,
            "PATCH",
            self._issue_path(place, issue),
            bearer=self._token_for(place),
            body=change,
        )
        _written(answer)

    def comment(self, place: PlaceKey, issue: IssueRef, text: CommentText) -> None:
        answer = _exchange(
            self._app.endpoint,
            "POST",
            f"{self._issue_path(place, issue)}/comments",
            bearer=self._token_for(place),
            body={"body": text.markdown},
        )
        _written(answer)

    def finish(self) -> None:
        for token in self._tokens.values():
            self._app.revoke(token)
        self._tokens.clear()

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


def _issue_ref(repository: str, answer: Any) -> IssueRef:
    if not isinstance(answer, dict):
        raise TrackerFailure("unexpected_answer")
    issue_id, number = answer.get("id"), answer.get("number")
    key = github_issue_key(repository, number) if isinstance(number, int) else None
    if not isinstance(issue_id, int) or key is None:
        raise TrackerFailure("unexpected_answer")
    return IssueRef(external_id=str(issue_id), external_key=key)


def _seen(document: Any) -> IssueSeen:
    state = document.get("state") if isinstance(document, dict) else None
    if state not in ("open", "closed"):
        raise TrackerFailure("unexpected_answer")
    reason = document.get("state_reason")
    return IssueSeen(
        state=state,
        close_reason=issue_close_reason(reason if isinstance(reason, str) else None)
        if state == "closed"
        else None,
    )


def _written(answer: _Answer) -> None:
    """A write's outcome: done, a failure that may pass, or a refusal a person must see."""
    if 200 <= answer.status < 300:
        return
    failure = _refusal(answer)
    if failure.failure == "unexpected_answer":
        # GitHub refused this write for a lasting reason (a 404, 410 or 422).
        raise TrackerFailure("write_refused", answer.status)
    raise failure
