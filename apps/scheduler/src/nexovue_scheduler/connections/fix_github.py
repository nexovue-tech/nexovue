"""The Fix App's GitHub client: one method per route, on a fixed host (F5 design, *GitHub
routes allowed*; spec AC7; ADR-082).

**Reads on `FixGitHub`; writes only on `FixWrites`** (Step 7, T7.2): blobs, a tree, a
commit, the one `nexovue/suggestion-<id>` ref and the draft. `FixWrites` cannot be built
without the founder's recorded go (`write_gate.py`, ADR-097; foundation item 33): while
`write_access_on()` is False its constructor refuses, so no write token is minted and no
write route is called. A write method takes a suggestion's id and builds the ref itself,
so another branch or a tag cannot be expressed (FR23, AC7). `ROUTES` and `WRITE_ROUTES`
are the checked-in lists, and a test fails when a method is added without them.

- Every call goes through the guarded libcurl client, **never follows a redirect**, and
  checks `body_truncated` first: a body cut at the client's 1 MiB is invalid JSON with no
  flag, so a cut tree reads as `repository_too_large`, never as a short listing (AC16a).
- Path segments are percent-encoded; a repository is only ever a strict `owner/name` key.
- Tokens are minted per job, narrowed to one repository and `contents: read`, held in
  memory, and revoked at the end (F22's `GitHubApp`). Never stored, logged or put in a job.
Step 8 adds Run CI's `PATCH` of the same ref (`move_branch`, fast forward only) and the
poller's read of one pull request (`pull`).

- **What GitHub answers is read from its documents, never from the service** (foundation
  item 28): the simulated GitHub (`tests/tracker_fake.py`) replays those shapes, and the
  founder's real run after Phase 2 checks them.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from urllib.parse import quote

from nexovue_platform.connections import is_manifest_code, is_repository_key
from nexovue_platform.fixes.records import fix_branch
from nexovue_platform.fixes.write_gate import WriteGo, write_access_on

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
# The draft job reads with this and writes with the next, narrowed to one repository, since
# whether a narrowed write token can call the permission route is untested (trial).
PULL_REQUEST_READ: Final = {"contents": "read", "metadata": "read", "pull_requests": "read"}
CONTENTS_AND_PULL_REQUESTS_WRITE: Final = {"contents": "write", "pull_requests": "write"}
# What a draft needs the person to hold on GitHub (D6).
WRITE_LEVELS: Final = frozenset({"write", "maintain", "admin"})
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
    ("POST", "/app/installations/{id}/access_tokens", "pull_request_read_token"),
    ("GET", "/repos/{r}/git/ref/heads/nexovue/suggestion-{id}", "fix_branch_head"),
    ("GET", "/repos/{r}/pulls?head={owner}:nexovue/suggestion-{id}&state=all", "pulls_for"),
    ("GET", "/repos/{r}/pulls/{number}", "pull"),
)
# The write routes (Steps 7 and 8), each on `FixWrites` only, behind the founder's go
# (ADR-097): Step 8 adds Run CI's `PATCH` of the same ref.
WRITE_ROUTES: Final = (
    ("POST", "/app/installations/{id}/access_tokens", "token"),
    ("POST", "/repos/{r}/git/blobs", "create_blob"),
    ("POST", "/repos/{r}/git/trees", "create_tree"),
    ("POST", "/repos/{r}/git/commits", "create_commit"),
    ("POST", "/repos/{r}/git/refs", "create_branch"),
    ("DELETE", "/repos/{r}/git/refs/heads/nexovue/suggestion-{id}", "delete_branch"),
    ("POST", "/repos/{r}/pulls", "open_draft"),
    ("PATCH", "/repos/{r}/git/refs/heads/nexovue/suggestion-{id}", "move_branch"),
)
# Why GitHub refused a write, by the words its 422 carries (from GitHub's documents and
# the trial's questions, not yet checked against the service: foundation item 28).
_ALREADY_EXISTS: Final = "already exists"
_DRAFT_WORD: Final = "draft"
_FAST_FORWARD_WORDS: Final = "fast forward"


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
class PullFacts:
    """A pull request as the draft job and the poller need it; never its title or body.
    `ended_at` is GitHub's merge time for a merged one, else its close time; the merge's
    commit is the one a deploy would name (FR30)."""

    number: int
    url: str
    head_sha: str
    state: str
    merged: bool
    draft: bool
    ended_at: datetime | None = None
    merge_commit_sha: str | None = None


@dataclass(frozen=True, slots=True)
class TreeChange:
    """One changed file in the commit's tree: its path, mode and new blob."""

    path: str
    mode: str
    blob_sha: str


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


def _refused_words(answer: _Answer) -> str:
    """A 422's message and its errors' messages, lower case, to tell refusals apart."""
    try:
        parsed = json.loads(answer.body)
    except ValueError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    words = [str(parsed.get("message") or "")]
    for error in parsed.get("errors") or []:
        if isinstance(error, dict):
            words.append(str(error.get("message") or ""))
    return " ".join(words).lower()


def _time(value: Any) -> datetime | None:
    """GitHub's ISO 8601 time, or None when it gives none; anything else is unexpected."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise GitHubFailure("unexpected")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as unreadable:
        raise GitHubFailure("unexpected") from unreadable
    if parsed.tzinfo is None:
        raise GitHubFailure("unexpected")
    return parsed


def _pull_facts(item: Any) -> PullFacts:
    if not isinstance(item, dict):
        raise GitHubFailure("unexpected")
    number, url, head = item.get("number"), item.get("html_url"), item.get("head")
    state, merged, draft = item.get("state"), item.get("merged"), item.get("draft")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(number, int) or not isinstance(url, str) or not isinstance(state, str):
        raise GitHubFailure("unexpected")
    was_merged = merged is True or item.get("merged_at") is not None
    merge_commit = item.get("merge_commit_sha")
    return PullFacts(
        number=number,
        url=url,
        head_sha=_sha(head_sha),
        state=state,
        merged=was_merged,
        draft=draft is True,
        ended_at=_time(item.get("merged_at") if was_merged else item.get("closed_at")),
        merge_commit_sha=_sha(merge_commit) if was_merged and merge_commit is not None else None,
    )


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

    def pull_request_read_token(self, installation_id: int, repository_id: int) -> str:
        """The draft job's read token: contents, metadata and pull requests, read only."""
        try:
            return self._app.token(
                installation_id, dict(PULL_REQUEST_READ), repository_ids=[int(repository_id)]
            )
        except TrackerFailure as failure:
            raise from_tracker(failure) from failure

    def revoke(self, token: str) -> None:
        self._app.revoke(token)

    def writes(self, go: WriteGo | None) -> FixWrites:
        """The write routes, only with the founder's recorded go (ADR-097)."""
        return FixWrites(self._endpoints, self._app, go)

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

    # ------------------------------------------------- the suggestion's branch

    def fix_branch_head(self, token: str, key: str, fix_id: str) -> str | None:
        """The head of `nexovue/suggestion-<id>`, or None when the branch does not exist."""
        url = (
            f"{self._endpoints.api}/repos/{_repository(key)}/git/ref/heads/"
            f"{quote(fix_branch(fix_id), safe='/')}"
        )
        try:
            answer = _ok(_send(self._endpoints, "GET", url, bearer=token))
        except GitHubFailure as failure:
            if failure.code == "not_found":
                return None
            raise
        target = answer.get("object") if isinstance(answer, dict) else None
        return _sha(target.get("sha") if isinstance(target, dict) else None)

    def pulls_for(self, token: str, key: str, fix_id: str) -> list[PullFacts]:
        """Every pull request, in any state, whose head is this suggestion's branch (design,
        step 4: adoption). Needs `pull_requests: read`."""
        owner = key.split("/", 1)[0]
        head = quote(f"{owner}:{fix_branch(fix_id)}", safe="")
        url = f"{self._endpoints.api}/repos/{_repository(key)}/pulls?head={head}&state=all"
        answer = _ok(_send(self._endpoints, "GET", url, bearer=token))
        if not isinstance(answer, list):
            raise GitHubFailure("unexpected")
        return [_pull_facts(item) for item in answer]

    def pull(self, token: str, key: str, number: int) -> PullFacts:
        """One pull request, for the poller (FR27); `not_found` when GitHub says 404, which
        the poller reads as `untracked`. Needs `pull_requests: read`."""
        url = f"{self._endpoints.api}/repos/{_repository(key)}/pulls/{int(number)}"
        return _pull_facts(_ok(_send(self._endpoints, "GET", url, bearer=token)))

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


class WritesOff(Exception):  # noqa: N818 — named for what it reports
    """Write access is off in code until the founder's recorded go (ADR-097)."""


class FixWrites:
    """The write routes of one repository, for one suggestion's branch (FR23, AC7).

    Built only through `FixGitHub.writes(go)`, and refused while `write_access_on(go)` is
    False, before any token is minted. Every method takes the suggestion's id where a ref
    is named and builds `refs/heads/nexovue/suggestion-<id>` itself; `POST /pulls` always
    asks for a draft, a constant, and never falls back to a pull request that is not one.
    """

    def __init__(self, endpoints: FixEndpoints, app: GitHubApp, go: WriteGo | None) -> None:
        if not write_access_on(go):
            raise WritesOff
        self._endpoints = endpoints
        self._app = app

    def token(self, installation_id: int, repository_id: int) -> str:
        """A one-hour token narrowed to one repository, to write its contents and pull
        requests; minted, never stored."""
        try:
            return self._app.token(
                installation_id,
                dict(CONTENTS_AND_PULL_REQUESTS_WRITE),
                repository_ids=[int(repository_id)],
            )
        except TrackerFailure as failure:
            raise from_tracker(failure) from failure

    def _post(self, token: str, url: str, body: dict[str, Any]) -> _Answer:
        return _send(self._endpoints, "POST", url, bearer=token, body=body)

    def create_blob(self, token: str, key: str, content: bytes) -> str:
        url = f"{self._endpoints.api}/repos/{_repository(key)}/git/blobs"
        body = {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"}
        answer = _ok(self._post(token, url, body))
        return _sha(answer.get("sha") if isinstance(answer, dict) else None)

    def create_tree(self, token: str, key: str, base_tree: str, changes: list[TreeChange]) -> str:
        """A tree on `base_tree` with the changed files' new blobs, and nothing else."""
        url = f"{self._endpoints.api}/repos/{_repository(key)}/git/trees"
        body = {
            "base_tree": _sha(base_tree),
            "tree": [
                {"path": each.path, "mode": each.mode, "type": "blob", "sha": _sha(each.blob_sha)}
                for each in changes
            ],
        }
        answer = _ok(self._post(token, url, body))
        return _sha(answer.get("sha") if isinstance(answer, dict) else None)

    def create_commit(self, token: str, key: str, *, message: str, tree: str, parent: str) -> str:
        """One commit, authored by the App (no author named, so GitHub uses the token's)."""
        url = f"{self._endpoints.api}/repos/{_repository(key)}/git/commits"
        body = {"message": message, "tree": _sha(tree), "parents": [_sha(parent)]}
        answer = _ok(self._post(token, url, body))
        return _sha(answer.get("sha") if isinstance(answer, dict) else None)

    def create_branch(self, token: str, key: str, fix_id: str, commit: str) -> None:
        """`refs/heads/nexovue/suggestion-<id>` at `commit`. GitHub's 422 says whether the
        branch already exists (`branch_exists`) or the commit is gone (`commit_missing`)."""
        url = f"{self._endpoints.api}/repos/{_repository(key)}/git/refs"
        body = {"ref": f"refs/heads/{fix_branch(fix_id)}", "sha": _sha(commit)}
        answer = self._post(token, url, body)
        if answer.status == 422:
            exists = _ALREADY_EXISTS in _refused_words(answer)
            raise GitHubFailure("branch_exists" if exists else "commit_missing", 422)
        _ok(answer)

    def delete_branch(self, token: str, key: str, fix_id: str) -> None:
        url = (
            f"{self._endpoints.api}/repos/{_repository(key)}/git/refs/heads/"
            f"{quote(fix_branch(fix_id), safe='/')}"
        )
        answer = _send(self._endpoints, "DELETE", url, bearer=token)
        if answer.status != 204:
            _ok(answer)

    def move_branch(self, token: str, key: str, fix_id: str, commit: str) -> None:
        """Run CI (FR25): `refs/heads/nexovue/suggestion-<id>` to `commit`, a fast forward
        only (`force` is a constant false). GitHub's 422 says the branch moved since it was
        read (`not_fast_forward`), or the commit is gone (`commit_missing`)."""
        url = (
            f"{self._endpoints.api}/repos/{_repository(key)}/git/refs/heads/"
            f"{quote(fix_branch(fix_id), safe='/')}"
        )
        answer = _send(
            self._endpoints,
            "PATCH",
            url,
            bearer=token,
            body={"sha": _sha(commit), "force": False},
        )
        if answer.status == 422:
            moved = _FAST_FORWARD_WORDS in _refused_words(answer)
            raise GitHubFailure("not_fast_forward" if moved else "commit_missing", 422)
        _ok(answer)

    def open_draft(
        self, token: str, key: str, fix_id: str, *, base: str, title: str, body: str
    ) -> PullFacts:
        """`POST /pulls` with `draft: true`. A second pull request for the branch is
        `pull_request_exists` (adopt it); a refused draft is `draft_refused`, never retried
        as a pull request that is not a draft (AC27a)."""
        owner = key.split("/", 1)[0]
        url = f"{self._endpoints.api}/repos/{_repository(key)}/pulls"
        answer = self._post(
            token,
            url,
            {
                "title": title,
                "body": body,
                "head": f"{owner}:{fix_branch(fix_id)}",
                "base": base,
                "draft": True,
            },
        )
        if answer.status == 422:
            words = _refused_words(answer)
            if _ALREADY_EXISTS in words:
                raise GitHubFailure("pull_request_exists", 422)
            if _DRAFT_WORD in words:
                raise GitHubFailure("draft_refused", 422)
        return _pull_facts(_ok(answer))
