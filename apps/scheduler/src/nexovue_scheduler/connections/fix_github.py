"""The Fix App's GitHub client: one method per route, on a fixed host (F5 design, *GitHub
routes allowed*; spec AC7; ADR-082).

**Reads only in Step 3.** Nothing here writes to a repository: the write routes (blobs,
trees, commits, the one `nexovue/suggestion-<id>` ref, the draft) come with Step 7, behind
the founder's written go (foundation item 33). `ROUTES` is the checked-in list, and a test
fails when a method is added without it.

- Every call goes through the guarded libcurl client, **never follows a redirect**, and
  checks `body_truncated` first: a body cut at the client's 1 MiB is invalid JSON with no
  flag, so a cut tree reads as `repository_too_large`, never as a short listing (AC16a).
- Path segments are percent-encoded; a repository is only ever a strict `owner/name` key.
- Tokens are minted per job, narrowed to one repository and `contents: read`, held in
  memory, and revoked at the end (F22's `GitHubApp`). Never stored, logged or put in a job.
- **What GitHub answers is read from its documents, never from the service** (foundation
  item 28): the simulated GitHub (`tests/tracker_fake.py`) replays those shapes, and the
  founder's real run after Phase 2 checks them.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote

from nexovue_platform.connections import is_manifest_code, is_repository_key

from nexovue_scheduler.connections.github import (
    GITHUB_API,
    GitHubApp,
    GitHubEndpoint,
)
from nexovue_scheduler.connections.trackers import TrackerFailure
from nexovue_scheduler.probes.http_check import PreparedRequest, perform

# GitHub's sign-in host, used only for the account link's OAuth exchange.
GITHUB_WEB: Final = "https://github.com"
_HEADERS: Final = (
    ("Accept", "application/vnd.github+json"),
    ("X-GitHub-Api-Version", "2022-11-28"),
    ("User-Agent", "nexovue-suggestions"),
)
# Longer than F22's 10 seconds: a recursive tree of a large folder is a slow answer.
TIMEOUT_SECONDS: Final = 30
# Rungs 3 and 4 need only this (design, *Fix App*, tiers).
CONTENTS_READ: Final = {"contents": "read", "metadata": "read"}
# The access a person needs to read quoted code under the owner's requirement (D20).
READ_LEVELS: Final = frozenset({"read", "triage", "write", "maintain", "admin"})
_SHA: Final = frozenset("0123456789abcdef")

# The checked-in route list (AC7): one method each, and no general request method.
ROUTES: Final = (
    ("POST", "/app-manifests/{code}/conversions", "convert_manifest"),
    ("GET", "/app/installations/{id}", "granted_permissions"),
    ("POST", "/app/installations/{id}/access_tokens", "read_token"),
    ("GET", "/repos/{r}", "repository"),
    ("GET", "/user/{id}", "login_of"),
    ("GET", "/repos/{r}/collaborators/{login}/permission", "permission_of"),
    ("GET", "/repos/{r}/git/ref/heads/{branch}", "head_of"),
    ("GET", "/repos/{r}/git/commits/{sha}", "tree_of_commit"),
    ("GET", "/repos/{r}/git/trees/{sha}", "tree"),
    ("GET", "/repos/{r}/git/blobs/{sha}", "blob"),
    ("GET", "/repos/{r}/compare/{sha}...{default}", "is_ancestor"),
    ("POST", "https://github.com/login/oauth/access_token", "exchange_code"),
    ("GET", "/user", "user_of_token"),
)


class GitHubFailure(Exception):  # noqa: N818 — named for what it reports
    """A call failed, by a fixed code; never GitHub's text. `unavailable` (a 5xx, a rate
    limit or no answer) may pass, so the job retries it; the rest will not."""

    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code if status is None else f"{code} ({status})")
        self.code = code
        self.status = status

    @property
    def passing(self) -> bool:
        return self.code == "unavailable"


# F22's words for a failed App call, as F5 reads them.
_FROM_TRACKER: Final = {
    "unreachable": "unavailable",
    "timed_out": "unavailable",
    "server_error": "unavailable",
    "rate_limited": "unavailable",
    "unauthorized": "permission_missing",
    "repository_unreachable": "permission_missing",
    "app_not_installed": "permission_missing",
    "key_unreadable": "permission_missing",
}


def from_tracker(failure: TrackerFailure) -> GitHubFailure:
    return GitHubFailure(_FROM_TRACKER.get(failure.failure, "unexpected"), failure.status)


@dataclass(frozen=True, slots=True)
class FixEndpoints:
    """Where GitHub is. Production uses the constants; a test points both at the fake."""

    api: str = GITHUB_API
    web: str = GITHUB_WEB
    private_allowed: bool = False

    def tracker_endpoint(self) -> GitHubEndpoint:
        return GitHubEndpoint(base_url=self.api, private_allowed=self.private_allowed)


@dataclass(frozen=True, slots=True)
class ConvertedFixApp:
    app_id: int
    app_slug: str
    private_key: str
    client_id: str
    client_secret: str


@dataclass(frozen=True, slots=True)
class RepositoryFacts:
    default_branch: str
    private: bool


@dataclass(frozen=True, slots=True)
class TreeEntry:
    path: str
    mode: str
    kind: str
    sha: str
    size: int | None


@dataclass(frozen=True, slots=True)
class GitHubPerson:
    user_id: int
    login: str


@dataclass(frozen=True, slots=True)
class _Answer:
    status: int
    body: bytes
    truncated: bool

    def json(self) -> Any:
        if self.truncated:
            raise GitHubFailure("repository_too_large", self.status)
        try:
            return json.loads(self.body)
        except ValueError as unreadable:
            raise GitHubFailure("unexpected", self.status) from unreadable


def _segment(value: str) -> str:
    return quote(value, safe="")


def _repository(key: str) -> str:
    if not is_repository_key(key):
        raise GitHubFailure("unexpected")
    owner, name = key.split("/", 1)
    return f"{_segment(owner)}/{_segment(name)}"


def _sha(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 40 or not set(value) <= _SHA:
        raise GitHubFailure("unexpected")
    return value


def _send(
    endpoints: FixEndpoints,
    method: str,
    url: str,
    *,
    bearer: str | None = None,
    body: dict[str, Any] | None = None,
    form: bool = False,
) -> _Answer:
    headers = list(_HEADERS)
    if bearer is not None:
        headers.append(("Authorization", f"Bearer {bearer}"))
    encoded = None
    if body is not None:
        headers.append(("Content-Type", "application/json"))
        encoded = json.dumps(body).encode()
    if form:
        headers[0] = ("Accept", "application/json")
    exchange = perform(
        PreparedRequest(
            method=method,
            url=url,
            headers=headers,
            body=encoded,
            follow_redirects=False,
            timeout_seconds=TIMEOUT_SECONDS,
        ),
        private_allowed=endpoints.private_allowed,
    )
    if exchange.error is not None:
        raise GitHubFailure("unavailable")
    return _Answer(exchange.status_code or 0, exchange.body, exchange.body_truncated)


def _ok(answer: _Answer) -> Any:
    """The JSON of a 2xx answer, or the failure F22's words name."""
    if answer.truncated:
        raise GitHubFailure("repository_too_large", answer.status)
    if 200 <= answer.status < 300:
        return answer.json()
    if answer.status == 429 or answer.status >= 500:
        raise GitHubFailure("unavailable", answer.status)
    if answer.status in (401, 403):
        raise GitHubFailure("permission_missing", answer.status)
    if answer.status == 404:
        raise GitHubFailure("not_found", answer.status)
    raise GitHubFailure("unexpected", answer.status)


def convert_manifest(endpoints: FixEndpoints, code: str) -> ConvertedFixApp:
    """F5's manifest conversion: unlike F22's, it keeps the client id and secret, which the
    account link's OAuth exchange needs (design, *Fix App*)."""
    if not is_manifest_code(code):
        raise GitHubFailure("refused")
    answer = _ok(
        _send(endpoints, "POST", f"{endpoints.api}/app-manifests/{_segment(code)}/conversions")
    )
    fields = ("id", "slug", "pem", "client_id", "client_secret")
    values = [answer.get(name) if isinstance(answer, dict) else None for name in fields]
    app_id, slug, pem, client_id, client_secret = values
    if not (
        isinstance(app_id, int)
        and all(isinstance(each, str) and each for each in (slug, pem, client_id, client_secret))
    ):
        raise GitHubFailure("unexpected")
    return ConvertedFixApp(
        app_id=app_id,
        app_slug=str(slug),
        private_key=str(pem),
        client_id=str(client_id),
        client_secret=str(client_secret),
    )


class FixGitHub:
    """One job's reads of one repository, through the Fix App."""

    def __init__(self, endpoints: FixEndpoints, *, app_id: int, private_key: str) -> None:
        self._endpoints = endpoints
        self._app = GitHubApp(endpoints.tracker_endpoint(), app_id=app_id, private_key=private_key)

    # ------------------------------------------------------------- the App

    def granted_permissions(self, installation_id: int) -> dict[str, str]:
        """What GitHub says the installation was granted (refreshed hourly; design)."""
        answer = _ok(
            _send(
                self._endpoints,
                "GET",
                f"{self._endpoints.api}/app/installations/{int(installation_id)}",
                bearer=self._app.signed_jwt(),
            )
        )
        granted = answer.get("permissions") if isinstance(answer, dict) else None
        if not isinstance(granted, dict):
            raise GitHubFailure("unexpected")
        return {str(k): str(v) for k, v in granted.items() if isinstance(v, str)}

    def installation_ids(self) -> list[int]:
        try:
            return self._app.installation_ids()
        except TrackerFailure as failure:
            raise from_tracker(failure) from failure

    def read_token(self, installation_id: int, repository_id: int) -> str:
        """A one-hour `contents: read` token for one repository; minted, never stored."""
        try:
            return self._app.token(
                installation_id, dict(CONTENTS_READ), repository_ids=[int(repository_id)]
            )
        except TrackerFailure as failure:
            raise from_tracker(failure) from failure

    def revoke(self, token: str) -> None:
        self._app.revoke(token)

    # ------------------------------------------------------------ reading

    def repository(self, token: str, key: str) -> RepositoryFacts:
        answer = _ok(
            _send(
                self._endpoints,
                "GET",
                f"{self._endpoints.api}/repos/{_repository(key)}",
                bearer=token,
            )
        )
        branch = answer.get("default_branch") if isinstance(answer, dict) else None
        private = answer.get("private") if isinstance(answer, dict) else None
        if not isinstance(branch, str) or not branch or not isinstance(private, bool):
            raise GitHubFailure("unexpected")
        return RepositoryFacts(default_branch=branch, private=private)

    def head_of(self, token: str, key: str, branch: str) -> str:
        branch_path = quote(branch, safe="/")
        url = f"{self._endpoints.api}/repos/{_repository(key)}/git/ref/heads/{branch_path}"
        answer = _ok(_send(self._endpoints, "GET", url, bearer=token))
        target = answer.get("object") if isinstance(answer, dict) else None
        return _sha(target.get("sha") if isinstance(target, dict) else None)

    def tree_of_commit(self, token: str, key: str, commit: str) -> str:
        url = f"{self._endpoints.api}/repos/{_repository(key)}/git/commits/{_sha(commit)}"
        answer = _ok(_send(self._endpoints, "GET", url, bearer=token))
        tree = answer.get("tree") if isinstance(answer, dict) else None
        return _sha(tree.get("sha") if isinstance(tree, dict) else None)

    def tree(self, token: str, key: str, sha: str, *, recursive: bool) -> list[TreeEntry]:
        """A tree's entries; a cut answer, by GitHub or by the client, is
        `repository_too_large` (design, *Picking files*)."""
        url = f"{self._endpoints.api}/repos/{_repository(key)}/git/trees/{_sha(sha)}"
        answer = _ok(
            _send(self._endpoints, "GET", url + ("?recursive=1" if recursive else ""), bearer=token)
        )
        if not isinstance(answer, dict) or answer.get("truncated") is True:
            raise GitHubFailure("repository_too_large")
        listed = answer.get("tree")
        if not isinstance(listed, list):
            raise GitHubFailure("unexpected")
        entries = []
        for item in listed:
            if not isinstance(item, dict):
                continue
            path, mode, kind, sha_value = (item.get(k) for k in ("path", "mode", "type", "sha"))
            size = item.get("size")
            if isinstance(path, str) and isinstance(mode, str) and isinstance(kind, str):
                entries.append(
                    TreeEntry(
                        path, mode, kind, _sha(sha_value), size if isinstance(size, int) else None
                    )
                )
        return entries

    def blob(self, token: str, key: str, sha: str) -> bytes:
        url = f"{self._endpoints.api}/repos/{_repository(key)}/git/blobs/{_sha(sha)}"
        answer = _ok(_send(self._endpoints, "GET", url, bearer=token))
        content = answer.get("content") if isinstance(answer, dict) else None
        if not isinstance(content, str) or answer.get("encoding") != "base64":
            raise GitHubFailure("unexpected")
        try:
            return base64.b64decode(content, validate=False)
        except ValueError as unreadable:
            raise GitHubFailure("unexpected") from unreadable

    def is_ancestor(self, token: str, key: str, commit: str, default_branch: str) -> bool:
        """Whether `commit` is the default branch's head or behind it (A17, AC12h):
        `ahead` or `identical` from `compare/{commit}...{default}`."""
        url = (
            f"{self._endpoints.api}/repos/{_repository(key)}/compare/"
            f"{_sha(commit)}...{quote(default_branch, safe='')}"
        )
        try:
            answer = _ok(_send(self._endpoints, "GET", url, bearer=token))
        except GitHubFailure as failure:
            if failure.code == "not_found":
                return False
            raise
        return isinstance(answer, dict) and answer.get("status") in ("ahead", "identical")

    # --------------------------------------------------------- people

    def login_of(self, token: str, github_user_id: int) -> str:
        """A linked person's current login, from the id stored (logins can change)."""
        url = f"{self._endpoints.api}/user/{int(github_user_id)}"
        answer = _ok(_send(self._endpoints, "GET", url, bearer=token))
        login = answer.get("login") if isinstance(answer, dict) else None
        if not isinstance(login, str) or not login:
            raise GitHubFailure("unexpected")
        return login

    def permission_of(self, token: str, key: str, login: str) -> str:
        """GitHub's word for a person's access to a repository (D6), or `none`."""
        url = (
            f"{self._endpoints.api}/repos/{_repository(key)}/collaborators/"
            f"{_segment(login)}/permission"
        )
        try:
            answer = _ok(_send(self._endpoints, "GET", url, bearer=token))
        except GitHubFailure as failure:
            if failure.code == "not_found":
                return "none"
            raise
        permission = answer.get("permission") if isinstance(answer, dict) else None
        return permission if isinstance(permission, str) else "none"


def exchange_code(endpoints: FixEndpoints, *, client_id: str, client_secret: str, code: str) -> str:
    """Trade a person's one-time code for their token, held in memory for one job."""
    answer = _ok(
        _send(
            endpoints,
            "POST",
            f"{endpoints.web}/login/oauth/access_token",
            body={"client_id": client_id, "client_secret": client_secret, "code": code},
            form=True,
        )
    )
    token = answer.get("access_token") if isinstance(answer, dict) else None
    if not isinstance(token, str) or not token:
        raise GitHubFailure("refused")
    return token


def user_of_token(endpoints: FixEndpoints, token: str) -> GitHubPerson:
    """Who a person's token belongs to: the id and login kept, never the token."""
    answer = _ok(_send(endpoints, "GET", f"{endpoints.api}/user", bearer=token))
    user_id = answer.get("id") if isinstance(answer, dict) else None
    login = answer.get("login") if isinstance(answer, dict) else None
    if not isinstance(user_id, int) or user_id <= 0 or not isinstance(login, str) or not login:
        raise GitHubFailure("unexpected")
    return GitHubPerson(user_id=user_id, login=login)
