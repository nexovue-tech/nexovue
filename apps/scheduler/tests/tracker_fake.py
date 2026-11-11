"""A simulated GitHub for the connection suites (F22, design *Test harness for sync*).

The founder decided on 2026-11-08 that no step waits on a real GitHub organization
(foundation item 28): Steps 3 to 6 are built and tested against this fake, and the
founder runs the real checks after Phase 2. So this fake is the only GitHub the suites
know, and **it proves the code against GitHub as its authors read it, not against
GitHub.** Its answers are shaped from `fixtures/github/`, which say where each came
from and that none was checked against the real service.

It is built on `recording_site`, so every request really crosses a socket through the
guarded libcurl client, and it is stateful where GitHub is:

- a manifest code converts once, and only while it is known;
- the App's JSON Web Token is checked: signed RS256 with the key the conversion handed
  out, with `iss`, `iat` and `exp`, and `exp` no more than 10 minutes after `iat`;
- an installation token is minted per request, records the permissions and repositories
  asked for, works until revoked, and is refused on any route but the installation's;
- repositories page by `page` and `per_page`;
- **issues** (Step 4): created by the App's bot, read, closed with a reason, reopened,
  commented on, listed by creator and time, deleted (410), transferred (301 to the new
  address), and hidden when their repository leaves the installation. A repository can
  turn public. A write can be **applied and then dropped** with no reply, or applied and
  then followed by a callback (the harness kills its worker there), and the list can see
  a new issue only after a stated delay (`search_delay`), the model AC44 is proved under.

- **git data and pull requests** (F5, plan T2.10): refs, commits, recursive trees that
  can be cut short (`tree_entry_limit`, as GitHub's `truncated`), blobs, ancestry by
  `compare`, a person's permission on a repository, a user by id, and pull requests
  listed by `?head=` and `state`, opened as drafts, refused with 422 for a second on
  one branch. Objects are hashed as git hashes them (`github_git_data.py`). A token
  minted with named permissions is held to them on these routes, so a read token
  cannot write. Every write can be dropped after it is applied, as issues can, which
  is how a crash at each step of the draft job is driven.

A route's answer can be replaced (`fail`) to drive each failure the code must survive.
F5's routes are compared with recordings in `fixtures/github_fix/` by
`test_fake_github_matches_recordings.py`; those recordings are shaped from GitHub's
documents, not yet recorded from GitHub, until the founder runs `record_github.py`.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from github_git_data import GitData
from recording_site import RecordedRequest, RecordingSite, Reply, json_reply, serve_recording_site

FIXTURES: Final = Path(__file__).parent / "fixtures" / "github"
# GitHub's documented ceiling on an App token's life (its JWT page).
MAX_JWT_SECONDS: Final = 600
# Room for the clock drift the JWT's backdated `iat` allows for.
DRIFT_SECONDS: Final = 60

APP_ID: Final = 1000001
# Zero-entropy markers a test can search for, and no secret scanner mistakes for a secret.
CLIENT_SECRET: Final = "z" * 24
WEBHOOK_SECRET: Final = "w" * 24


def fixture(name: str) -> dict[str, Any]:
    """A fixture's body, without its `_source` note."""
    document: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    document.pop("_source", None)
    return document


@dataclass
class MintedToken:
    installation_id: int
    permissions: dict[str, str] | None
    repository_ids: list[int] | None
    revoked: bool = False


@dataclass
class FakeIssue:
    id: int
    number: int
    repository_id: int
    title: str
    body: str
    labels: list[str]
    creator: str
    created_at: float
    state: str = "open"
    state_reason: str | None = None
    comments: list[str] = field(default_factory=list)
    deleted: bool = False
    # Where a transfer took it: the repository id and the issue there.
    moved_to: FakeIssue | None = None


_REPOSITORY_PATH: Final = re.compile(r"^/repos/(?P<owner>[^/]+)/(?P<name>[^/]+)$")
_ISSUES_PATH: Final = re.compile(r"^/repos/(?P<owner>[^/]+)/(?P<name>[^/]+)/issues$")
_ISSUE_PATH: Final = re.compile(
    r"^/repos/(?P<owner>[^/]+)/(?P<name>[^/]+)/issues/(?P<number>[0-9]+)(?P<comments>/comments)?$"
)
_MOVED_PATH: Final = re.compile(
    r"^/repositories/(?P<repository_id>[0-9]+)/issues/(?P<number>[0-9]+)$"
)
_REPO: Final = r"^/repos/(?P<owner>[^/]+)/(?P<name>[^/]+)"
_GIT_REF_PATH: Final = re.compile(_REPO + r"/git/refs?/heads/(?P<branch>.+)$")
_GIT_REFS_PATH: Final = re.compile(_REPO + r"/git/refs$")
_GIT_OBJECT_PATH: Final = re.compile(
    _REPO + r"/git/(?P<kind>commits|trees|blobs)(?:/(?P<sha>[0-9a-f]{40}))?$"
)
_COMPARE_PATH: Final = re.compile(_REPO + r"/compare/(?P<base>[^.]+)\.\.\.(?P<head>.+)$")
_PERMISSION_PATH: Final = re.compile(_REPO + r"/collaborators/(?P<login>[^/]+)/permission$")
_PULLS_PATH: Final = re.compile(_REPO + r"/pulls(?:/(?P<number>[0-9]+))?$")
_USER_PATH: Final = re.compile(r"^/user/(?P<user_id>[0-9]+)$")
# GitHub's documented cap on a recursive tree (its Git trees page, as summarised by a
# search on 2026-11-11): 100,000 entries or 7 MB, past which `truncated` is true.
TREE_ENTRY_LIMIT: Final = 100_000
_BLOB_LINE: Final = 60
# Each route's needed permission when a token was minted with named permissions.
_NEEDS: Final = {
    "read_ref": ("contents", "read"),
    "read_commit": ("contents", "read"),
    "read_tree": ("contents", "read"),
    "read_blob": ("contents", "read"),
    "compare": ("contents", "read"),
    "permission": ("metadata", "read"),
    "list_pulls": ("pull_requests", "read"),
    "read_pull": ("pull_requests", "read"),
    "create_blob": ("contents", "write"),
    "create_tree": ("contents", "write"),
    "create_commit": ("contents", "write"),
    "create_ref": ("contents", "write"),
    "update_ref": ("contents", "write"),
    "delete_ref": ("contents", "write"),
    "create_pull": ("pull_requests", "write"),
}
_LEVELS: Final = {"read": 1, "write": 2, "admin": 3}


@dataclass
class FakePull:
    id: int
    number: int
    repository_id: int
    head_ref: str
    head_sha: str
    base_ref: str
    title: str
    body: str
    draft: bool
    creator: str
    state: str = "open"
    merged: bool = False


@dataclass
class FakeGitHub:
    site: RecordingSite
    private_key_pem: str
    public_key: Any
    app_id: int = APP_ID
    codes: set[str] = field(default_factory=set)
    installation_ids: list[int] = field(default_factory=list)
    repositories: list[dict[str, Any]] = field(default_factory=list)
    tokens: dict[str, MintedToken] = field(default_factory=dict)
    jwt_claims_seen: list[dict[str, Any]] = field(default_factory=list)
    # Route name to the reply that replaces GitHub's answer there.
    faults: dict[str, Reply] = field(default_factory=dict)
    app_slug: str = "nexovue-acme"
    issues: list[FakeIssue] = field(default_factory=list)
    # Seconds before a new issue appears in the list (AC44's stated model).
    search_delay: float = 0.0
    # Write routes whose change is applied and whose reply is then dropped.
    drop_after_commit: set[str] = field(default_factory=set)
    # Called with the route's name after a write is applied, before its reply.
    after_commit: Callable[[str], None] | None = None
    removed_repository_ids: set[int] = field(default_factory=set)
    # F5's git data, per repository id; people and their permissions; pull requests.
    git: dict[int, GitData] = field(default_factory=dict)
    users: dict[int, str] = field(default_factory=dict)
    permissions: dict[tuple[int, str], str] = field(default_factory=dict)
    pulls: list[FakePull] = field(default_factory=list)
    tree_entry_limit: int = TREE_ENTRY_LIMIT
    # What "now" is for the list's delay: a test's own clock, so a retry it moves forward
    # sees what the tracker would by then.
    clock: Callable[[], float] = time.monotonic

    @property
    def base_url(self) -> str:
        return self.site.url("").rstrip("/")

    # ------------------------------------------------------------ arranging

    def add_code(self) -> str:
        code = secrets.token_hex(10)
        self.codes.add(code)
        self._route(f"/app-manifests/{code}/conversions", "conversion", self._convert)
        return code

    def install(self, installation_id: int = 3000001) -> int:
        self.installation_ids.append(installation_id)
        self._route(
            f"/app/installations/{installation_id}/access_tokens",
            "access_token",
            self._mint,
        )
        return installation_id

    def add_repository(self, repository_id: int, full_name: str, *, private: bool = True) -> None:
        self.repositories.append(
            {
                **fixture("repository"),
                "id": repository_id,
                "full_name": full_name,
                "name": full_name.rsplit("/", 1)[-1],
                "private": private,
            }
        )

    def fail(
        self,
        route: str,
        status: int,
        *,
        delay: float = 0.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.faults[route] = Reply(
            status=status,
            delay=delay,
            body=b'{"message": "fault"}',
            headers={"Content-Type": "application/json", **(headers or {})},
        )

    def heal(self, route: str) -> None:
        self.faults.pop(route, None)

    # --------------------------------------------------- arranging issues

    def repository(self, full_name: str) -> dict[str, Any]:
        [found] = [each for each in self.repositories if each["full_name"] == full_name]
        return found

    def make_public(self, full_name: str) -> None:
        self.repository(full_name)["private"] = False

    def remove_repository(self, full_name: str) -> None:
        self.removed_repository_ids.add(self.repository(full_name)["id"])

    def restore_repository(self, full_name: str) -> None:
        self.removed_repository_ids.discard(self.repository(full_name)["id"])

    def issues_in(self, full_name: str) -> list[FakeIssue]:
        repository_id = self.repository(full_name)["id"]
        return [
            issue
            for issue in self.issues
            if issue.repository_id == repository_id and not issue.deleted and issue.moved_to is None
        ]

    def issue(self, key: str) -> FakeIssue:
        full_name, number = key.split("#")
        repository_id = self.repository(full_name)["id"]
        [found] = [
            issue
            for issue in self.issues
            if issue.repository_id == repository_id and issue.number == int(number)
        ]
        return found

    def close(self, key: str, reason: str = "completed") -> None:
        issue = self.issue(key)
        issue.state, issue.state_reason = "closed", reason

    def reopen(self, key: str) -> None:
        issue = self.issue(key)
        issue.state, issue.state_reason = "open", "reopened"

    def delete(self, key: str) -> None:
        self.issue(key).deleted = True

    def transfer(self, key: str, to_full_name: str) -> FakeIssue:
        issue = self.issue(key)
        moved = self._new_issue(
            self.repository(to_full_name)["id"], issue.title, issue.body, issue.labels
        )
        moved.state, moved.state_reason = issue.state, issue.state_reason
        issue.moved_to = moved
        return moved

    def _new_issue(self, repository_id: int, title: str, body: str, labels: list[str]) -> FakeIssue:
        number = 1 + max(
            (each.number for each in self.issues if each.repository_id == repository_id),
            default=0,
        )
        issue = FakeIssue(
            id=5000000 + len(self.issues) + 1,
            number=number,
            repository_id=repository_id,
            title=title,
            body=body,
            labels=labels,
            creator=f"{self.app_slug}[bot]",
            created_at=self.clock(),
        )
        self.issues.append(issue)
        return issue

    def calls(self, path_prefix: str = "") -> list[RecordedRequest]:
        return [request for request in self.site.requests if request.path.startswith(path_prefix)]

    # ------------------------------------------------------------- routing

    def _route(self, path: str, name: str, handler: Callable[[RecordedRequest], Reply]) -> None:
        def answer(request: RecordedRequest) -> Reply:
            return self.faults.get(name) or handler(request)

        self.site.replies[path] = answer

    def start(self) -> None:
        # GitHub answers a path it does not know, such as a code never issued, with 404.
        self.site.default = json_reply({"message": "Not Found"}, 404)
        self._route("/app", "app", self._app)
        self._route("/app/installations", "installations", self._installations)
        self._route("/installation/repositories", "repositories", self._repositories)
        self._route("/installation/token", "revoke", self._revoke)
        self.site.default = self._dispatch

    # ------------------------------------------------------------- answers

    def _convert(self, request: RecordedRequest) -> Reply:
        code = request.path.split("/")[2]
        if request.method != "POST" or code not in self.codes:
            return json_reply({"message": "Not Found"}, 404)
        self.codes.discard(code)
        return json_reply(
            {
                **fixture("manifest_conversion"),
                "id": self.app_id,
                "pem": self.private_key_pem,
                "client_secret": CLIENT_SECRET,
                "webhook_secret": WEBHOOK_SECRET,
            },
            201,
        )

    def _signed_in_as_app(self, request: RecordedRequest) -> bool:
        scheme, _, presented = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            return False
        try:
            claims = jwt.decode(
                presented,
                self.public_key,
                algorithms=["RS256"],
                options={"require": ["iat", "exp", "iss"]},
                leeway=DRIFT_SECONDS,
            )
        except jwt.PyJWTError:
            return False
        self.jwt_claims_seen.append(claims)
        lasts = int(claims["exp"]) - int(claims["iat"])
        return str(claims["iss"]) == str(self.app_id) and lasts <= MAX_JWT_SECONDS + DRIFT_SECONDS

    def _app(self, request: RecordedRequest) -> Reply:
        if not self._signed_in_as_app(request):
            return json_reply({"message": "Bad credentials"}, 401)
        return json_reply({**fixture("app"), "id": self.app_id})

    def _installations(self, request: RecordedRequest) -> Reply:
        if not self._signed_in_as_app(request):
            return json_reply({"message": "Bad credentials"}, 401)
        return json_reply(
            [
                {**fixture("installation"), "id": installation_id, "app_id": self.app_id}
                for installation_id in self.installation_ids
            ]
        )

    def _mint(self, request: RecordedRequest) -> Reply:
        if request.method != "POST" or not self._signed_in_as_app(request):
            return json_reply({"message": "Bad credentials"}, 401)
        installation_id = int(request.path.split("/")[3])
        if installation_id not in self.installation_ids:
            return json_reply({"message": "Not Found"}, 404)
        asked = json.loads(request.body or b"{}")
        reachable = {each["id"] for each in self.repositories} - self.removed_repository_ids
        if any(wanted not in reachable for wanted in asked.get("repository_ids") or []):
            return json_reply(
                {"message": "There is at least one repository that does not exist"}, 422
            )
        token = f"ghs_fake_{secrets.token_hex(16)}"
        self.tokens[token] = MintedToken(
            installation_id=installation_id,
            permissions=asked.get("permissions"),
            repository_ids=asked.get("repository_ids"),
        )
        return json_reply(
            {**fixture("access_token"), "token": token, "permissions": asked.get("permissions")},
            201,
        )

    def _installation_token(self, request: RecordedRequest) -> MintedToken | None:
        scheme, _, presented = request.headers.get("authorization", "").partition(" ")
        minted = self.tokens.get(presented) if scheme.lower() in {"bearer", "token"} else None
        return None if minted is None or minted.revoked else minted

    def _repositories(self, request: RecordedRequest) -> Reply:
        minted = self._installation_token(request)
        if minted is None or minted.installation_id not in self.installation_ids:
            return json_reply({"message": "Bad credentials"}, 401)
        query = parse_qs(urlsplit(request.path).query)
        per_page = min(int(query.get("per_page", ["30"])[0]), 100)
        page = int(query.get("page", ["1"])[0])
        visible = [
            repository
            for repository in self.repositories
            if minted.repository_ids is None or repository["id"] in minted.repository_ids
        ]
        start = (page - 1) * per_page
        return json_reply(
            {
                "total_count": len(visible),
                "repository_selection": "selected",
                "repositories": visible[start : start + per_page],
            }
        )

    # ------------------------------------------------------- the issue routes

    def _dispatch(self, request: RecordedRequest) -> Reply:
        """Paths that carry ids: `recording_site` routes by exact path, so these come here."""
        path = urlsplit(request.path).path
        not_found = json_reply({"message": "Not Found"}, 404)
        if match := _REPOSITORY_PATH.match(path):
            return self._guarded("repository", request, match, self._repository_answer)
        if match := _ISSUES_PATH.match(path):
            if request.method == "POST":
                return self._guarded("create_issue", request, match, self._create)
            return self._guarded("list_issues", request, match, self._list)
        if match := _ISSUE_PATH.match(path):
            if match["comments"]:
                return self._guarded("comment", request, match, self._comment)
            if request.method == "PATCH":
                return self._guarded("update_issue", request, match, self._update)
            return self._guarded("read_issue", request, match, self._read)
        if match := _MOVED_PATH.match(path):
            return self._moved(request, match)
        if match := _USER_PATH.match(path):
            return self._user(request, match)
        return self._dispatch_git(request, path) or not_found

    def _dispatch_git(self, request: RecordedRequest, path: str) -> Reply | None:
        """F5's routes: git data, compare, a person's permission, pull requests."""
        method = request.method
        if match := _GIT_REF_PATH.match(path):
            name = {"GET": "read_ref", "PATCH": "update_ref", "DELETE": "delete_ref"}.get(method)
            return None if name is None else self._guarded(name, request, match, self._ref)
        if (match := _GIT_REFS_PATH.match(path)) and method == "POST":
            return self._guarded("create_ref", request, match, self._create_ref)
        if match := _GIT_OBJECT_PATH.match(path):
            kind = match["kind"].rstrip("s")
            name = f"read_{kind}" if method == "GET" else f"create_{kind}"
            return self._guarded(name, request, match, self._git_object)
        if match := _COMPARE_PATH.match(path):
            return self._guarded("compare", request, match, self._compare)
        if match := _PERMISSION_PATH.match(path):
            return self._guarded("permission", request, match, self._permission)
        if match := _PULLS_PATH.match(path):
            if match["number"]:
                return self._guarded("read_pull", request, match, self._read_pull)
            name = "create_pull" if method == "POST" else "list_pulls"
            return self._guarded(name, request, match, self._pulls)
        return None

    def _guarded(
        self,
        name: str,
        request: RecordedRequest,
        match: re.Match[str],
        handler: Callable[[RecordedRequest, dict[str, Any], re.Match[str]], Reply],
    ) -> Reply:
        """A repository route, for an installation token that reaches the repository."""
        if name in self.faults:
            return self.faults[name]
        minted = self._installation_token(request)
        if minted is None:
            return json_reply({"message": "Bad credentials"}, 401)
        if not self._permits(minted, name):
            return json_reply({"message": "Resource not accessible by integration"}, 403)
        full_name = f"{match['owner']}/{match['name']}"
        found = [each for each in self.repositories if each["full_name"] == full_name]
        if not found:
            return json_reply({"message": "Not Found"}, 404)
        [repository] = found
        reached = minted.repository_ids is None or repository["id"] in minted.repository_ids
        if not reached or repository["id"] in self.removed_repository_ids:
            return json_reply({"message": "Not Found"}, 404)
        reply = handler(request, repository, match)
        if request.method in ("POST", "PATCH", "DELETE") and 200 <= reply.status < 300:
            if self.after_commit is not None:
                self.after_commit(name)
            if name in self.drop_after_commit:
                return Reply(drop=True)
        return reply

    def _issue_json(self, issue: FakeIssue) -> dict[str, Any]:
        [repository] = [each for each in self.repositories if each["id"] == issue.repository_id]
        return {
            **fixture("issue"),
            "id": issue.id,
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "state": issue.state,
            "state_reason": issue.state_reason,
            "labels": [{"name": label} for label in issue.labels],
            "comments": len(issue.comments),
            "user": {"login": issue.creator, "type": "Bot"},
            "repository_url": f"{self.base_url}/repos/{repository['full_name']}",
            "html_url": f"https://github.com/{repository['full_name']}/issues/{issue.number}",
        }

    def _repository_answer(
        self, request: RecordedRequest, repository: dict[str, Any], _: re.Match[str]
    ) -> Reply:
        return json_reply(repository)

    def _create(
        self, request: RecordedRequest, repository: dict[str, Any], _: re.Match[str]
    ) -> Reply:
        asked = json.loads(request.body or b"{}")
        if not isinstance(asked.get("title"), str):
            return json_reply({"message": "Validation Failed"}, 422)
        issue = self._new_issue(
            repository["id"], asked["title"], asked.get("body") or "", asked.get("labels") or []
        )
        return json_reply(self._issue_json(issue), 201)

    def _list(
        self, request: RecordedRequest, repository: dict[str, Any], _: re.Match[str]
    ) -> Reply:
        query = parse_qs(urlsplit(request.path).query)
        creator = query.get("creator", [None])[0]
        settled = self.clock() - self.search_delay
        return json_reply(
            [
                self._issue_json(issue)
                for issue in self.issues
                if issue.repository_id == repository["id"]
                and not issue.deleted
                and issue.moved_to is None
                and issue.created_at <= settled
                and (creator is None or issue.creator == creator)
            ]
        )

    def _found(self, repository: dict[str, Any], match: re.Match[str]) -> FakeIssue | None:
        numbered = [
            issue
            for issue in self.issues
            if issue.repository_id == repository["id"] and issue.number == int(match["number"])
        ]
        return numbered[0] if numbered else None

    def _read(
        self, request: RecordedRequest, repository: dict[str, Any], match: re.Match[str]
    ) -> Reply:
        issue = self._found(repository, match)
        if issue is None:
            return json_reply({"message": "Not Found"}, 404)
        if issue.deleted:
            return json_reply({"message": "This issue was deleted"}, 410)
        if issue.moved_to is not None:
            moved = issue.moved_to
            return Reply(
                status=301,
                body=json.dumps({"message": "Moved Permanently"}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Location": f"{self.base_url}/repositories/{moved.repository_id}"
                    f"/issues/{moved.number}",
                },
            )
        return json_reply(self._issue_json(issue))

    def _moved(self, request: RecordedRequest, match: re.Match[str]) -> Reply:
        minted = self._installation_token(request)
        if minted is None:
            return json_reply({"message": "Bad credentials"}, 401)
        repository_id = int(match["repository_id"])
        target = [
            issue
            for issue in self.issues
            if issue.repository_id == repository_id and issue.number == int(match["number"])
        ]
        if not target or repository_id in self.removed_repository_ids:
            return json_reply({"message": "Not Found"}, 404)
        return json_reply(self._issue_json(target[0]))

    def _update(
        self, request: RecordedRequest, repository: dict[str, Any], match: re.Match[str]
    ) -> Reply:
        issue = self._found(repository, match)
        if issue is None or issue.deleted:
            return json_reply({"message": "Not Found"}, 404 if issue is None else 410)
        asked = json.loads(request.body or b"{}")
        if asked.get("state") == "closed":
            issue.state, issue.state_reason = "closed", asked.get("state_reason") or "completed"
        elif asked.get("state") == "open":
            issue.state, issue.state_reason = "open", "reopened"
        return json_reply(self._issue_json(issue))

    def _comment(
        self, request: RecordedRequest, repository: dict[str, Any], match: re.Match[str]
    ) -> Reply:
        issue = self._found(repository, match)
        if issue is None or issue.deleted or request.method != "POST":
            return json_reply({"message": "Not Found"}, 404)
        body = json.loads(request.body or b"{}").get("body")
        issue.comments.append(body)
        return json_reply(
            {**fixture("issue_comment"), "id": 6000000 + len(issue.comments), "body": body}, 201
        )

    # --------------------------------------------- F5's git data and pull requests

    def _permits(self, minted: MintedToken, route: str) -> bool:
        """A token minted with named permissions reaches only what they allow."""
        need = _NEEDS.get(route)
        if need is None or minted.permissions is None:
            return True
        held = minted.permissions.get(need[0])
        return held is not None and _LEVELS.get(held, 0) >= _LEVELS[need[1]]

    def git_of(self, full_name: str) -> GitData:
        return self.git.setdefault(self.repository(full_name)["id"], GitData())

    def seed_files(
        self,
        full_name: str,
        files: dict[str, str | tuple[str, str]],
        *,
        branch: str = "main",
        message: str = "seed",
    ) -> str:
        """A commit on `branch` with `files` changed; the branch moves to it."""
        return self.git_of(full_name).commit_files(branch, files, message)

    def add_user(self, user_id: int, login: str) -> None:
        self.users[user_id] = login

    def grant(self, full_name: str, login: str, permission: str) -> None:
        self.permissions[(self.repository(full_name)["id"], login)] = permission

    def pulls_in(self, full_name: str) -> list[FakePull]:
        repository_id = self.repository(full_name)["id"]
        return [pull for pull in self.pulls if pull.repository_id == repository_id]

    def _api(self, repository: dict[str, Any], tail: str) -> str:
        return f"{self.base_url}/repos/{repository['full_name']}/{tail}"

    def _user(self, request: RecordedRequest, match: re.Match[str]) -> Reply:
        if "read_user" in self.faults:
            return self.faults["read_user"]
        if self._installation_token(request) is None:
            return json_reply({"message": "Bad credentials"}, 401)
        user_id = int(match["user_id"])
        login = self.users.get(user_id)
        if login is None:
            return json_reply({"message": "Not Found"}, 404)
        return json_reply({**fixture("user"), "id": user_id, "login": login})

    def _ref_json(self, repository: dict[str, Any], branch: str, sha: str) -> dict[str, Any]:
        return {
            "ref": f"refs/heads/{branch}",
            "node_id": "REF_fake",
            "url": self._api(repository, f"git/refs/heads/{branch}"),
            "object": {
                "sha": sha,
                "type": "commit",
                "url": self._api(repository, f"git/commits/{sha}"),
            },
        }

    def _ref(
        self, request: RecordedRequest, repository: dict[str, Any], match: re.Match[str]
    ) -> Reply:
        data = self.git.setdefault(repository["id"], GitData())
        branch = match["branch"]
        if branch not in data.refs:
            return json_reply({"message": "Not Found"}, 404)
        if request.method == "DELETE":
            del data.refs[branch]
            return Reply(status=204, body=b"")
        if request.method == "PATCH":
            asked = json.loads(request.body or b"{}")
            sha, force = asked.get("sha"), bool(asked.get("force"))
            if sha not in data.commits:
                return json_reply({"message": "Object does not exist"}, 422)
            if not force and data.refs[branch] not in data.ancestors(sha):
                return json_reply({"message": "Update is not a fast forward"}, 422)
            data.refs[branch] = sha
        return json_reply(self._ref_json(repository, branch, data.refs[branch]))

    def _create_ref(
        self, request: RecordedRequest, repository: dict[str, Any], _: re.Match[str]
    ) -> Reply:
        data = self.git.setdefault(repository["id"], GitData())
        asked = json.loads(request.body or b"{}")
        ref, sha = str(asked.get("ref", "")), asked.get("sha")
        if not ref.startswith("refs/heads/") or sha not in data.commits:
            return json_reply({"message": "Reference update failed"}, 422)
        branch = ref.removeprefix("refs/heads/")
        if branch in data.refs:
            return json_reply({"message": "Reference already exists"}, 422)
        data.refs[branch] = sha
        return json_reply(self._ref_json(repository, branch, sha), 201)

    def _tree_json(
        self, repository: dict[str, Any], data: GitData, sha: str, recursive: bool
    ) -> dict[str, Any]:
        listed = list(data.walk(sha)) if recursive else [(e.name, e) for e in data.trees[sha]]
        truncated = len(listed) > self.tree_entry_limit
        entries = []
        for path, entry in listed[: self.tree_entry_limit]:
            item: dict[str, Any] = {
                "path": path,
                "mode": entry.mode,
                "type": entry.kind,
                "sha": entry.sha,
                "url": self._api(repository, f"git/{entry.kind}s/{entry.sha}"),
            }
            if entry.kind == "blob":
                item["size"] = len(data.blobs[entry.sha])
            entries.append(item)
        return {
            "sha": sha,
            "url": self._api(repository, f"git/trees/{sha}"),
            "tree": entries,
            "truncated": truncated,
        }

    def _commit_json(self, repository: dict[str, Any], data: GitData, sha: str) -> dict[str, Any]:
        commit = data.commits[sha]
        person = {
            "name": "Nexovue Fake",
            "email": "fake@example.invalid",
            "date": "2023-11-14T22:13:20Z",
        }
        return {
            "sha": sha,
            "node_id": "C_fake",
            "url": self._api(repository, f"git/commits/{sha}"),
            "author": person,
            "committer": person,
            "message": commit.message,
            "tree": {"sha": commit.tree, "url": self._api(repository, f"git/trees/{commit.tree}")},
            "parents": [
                {"sha": parent, "url": self._api(repository, f"git/commits/{parent}")}
                for parent in commit.parents
            ],
            "verification": {
                "verified": False,
                "reason": "unsigned",
                "signature": None,
                "payload": None,
            },
        }

    def _git_object(
        self, request: RecordedRequest, repository: dict[str, Any], match: re.Match[str]
    ) -> Reply:
        data = self.git.setdefault(repository["id"], GitData())
        kind, sha = match["kind"], match["sha"]
        if request.method == "POST":
            return self._create_object(repository, data, kind, json.loads(request.body or b"{}"))
        if sha is None:
            return json_reply({"message": "Not Found"}, 404)
        if kind == "blobs" and sha in data.blobs:
            raw = base64.b64encode(data.blobs[sha]).decode()
            lines = [raw[i : i + _BLOB_LINE] for i in range(0, len(raw), _BLOB_LINE)]
            return json_reply(
                {
                    "sha": sha,
                    "node_id": "B_fake",
                    "size": len(data.blobs[sha]),
                    "url": self._api(repository, f"git/blobs/{sha}"),
                    "content": "\n".join(lines) + "\n",
                    "encoding": "base64",
                }
            )
        if kind == "trees" and sha in data.trees:
            recursive = "recursive" in parse_qs(urlsplit(request.path).query)
            return json_reply(self._tree_json(repository, data, sha, recursive))
        if kind == "commits" and sha in data.commits:
            return json_reply(self._commit_json(repository, data, sha))
        return json_reply({"message": "Not Found"}, 404)

    def _create_object(
        self, repository: dict[str, Any], data: GitData, kind: str, asked: dict[str, Any]
    ) -> Reply:
        if kind == "blobs":
            content = asked.get("content")
            if not isinstance(content, str):
                return json_reply({"message": "Invalid request"}, 422)
            raw = (
                base64.b64decode(content)
                if asked.get("encoding") == "base64"
                else content.encode("utf-8")
            )
            sha = data.add_blob(raw)
            return json_reply({"sha": sha, "url": self._api(repository, f"git/blobs/{sha}")}, 201)
        if kind == "trees":
            base = asked.get("base_tree")
            if base is not None and base not in data.trees:
                return json_reply({"message": "Invalid tree info"}, 422)
            flat = dict(data.flat(base)) if base else {}
            for entry in asked.get("tree") or []:
                path, mode = entry.get("path"), entry.get("mode", "100644")
                if "content" in entry:
                    flat[path] = (mode, data.add_blob(str(entry["content"]).encode("utf-8")))
                elif entry.get("sha") is None:
                    flat.pop(path, None)
                elif entry["sha"] in data.blobs:
                    flat[path] = (mode, entry["sha"])
                else:
                    return json_reply({"message": "Invalid tree info"}, 422)
            sha = data.tree_from_paths(flat)
            return json_reply(self._tree_json(repository, data, sha, recursive=False), 201)
        tree, parents = asked.get("tree"), tuple(asked.get("parents") or ())
        if tree not in data.trees or any(parent not in data.commits for parent in parents):
            return json_reply({"message": "Invalid request"}, 422)
        sha = data.add_commit(tree, parents, str(asked.get("message", "")))
        return json_reply(self._commit_json(repository, data, sha), 201)

    def _commit_named(self, data: GitData, name: str) -> str | None:
        return data.refs.get(name) or (name if name in data.commits else None)

    def _compare(
        self, request: RecordedRequest, repository: dict[str, Any], match: re.Match[str]
    ) -> Reply:
        data = self.git.setdefault(repository["id"], GitData())
        base = self._commit_named(data, match["base"])
        head = self._commit_named(data, match["head"])
        if base is None or head is None:
            return json_reply({"message": "Not Found"}, 404)
        head_line, base_line = data.ancestors(head), data.ancestors(base)
        merge_base = next((sha for sha in head_line if sha in base_line), None)
        ahead = [sha for sha in head_line if sha not in base_line]
        behind = [sha for sha in base_line if sha not in head_line]
        status = (
            "identical"
            if base == head
            else "ahead"
            if not behind
            else "behind"
            if not ahead
            else "diverged"
        )
        return json_reply(
            {
                "url": self._api(repository, f"compare/{match['base']}...{match['head']}"),
                "status": status,
                "ahead_by": len(ahead),
                "behind_by": len(behind),
                "total_commits": len(ahead),
                "base_commit": self._commit_json(repository, data, base),
                "merge_base_commit": None
                if merge_base is None
                else self._commit_json(repository, data, merge_base),
                "commits": [self._commit_json(repository, data, sha) for sha in reversed(ahead)],
                "files": [],
            }
        )

    def _permission(
        self, request: RecordedRequest, repository: dict[str, Any], match: re.Match[str]
    ) -> Reply:
        login = match["login"]
        if login not in self.users.values():
            return json_reply({"message": "Not Found"}, 404)
        permission = self.permissions.get((repository["id"], login), "none")
        [user_id] = [i for i, name in self.users.items() if name == login]
        return json_reply(
            {
                "permission": permission,
                "role_name": permission,
                "user": {**fixture("user"), "id": user_id, "login": login},
            }
        )

    def _pull_json(self, repository: dict[str, Any], pull: FakePull) -> dict[str, Any]:
        owner = repository["full_name"].split("/")[0]
        return {
            **fixture("pull"),
            "id": pull.id,
            "number": pull.number,
            "state": pull.state,
            "draft": pull.draft,
            "merged": pull.merged,
            "title": pull.title,
            "body": pull.body,
            "user": {"login": pull.creator, "type": "Bot"},
            "head": {
                "ref": pull.head_ref,
                "sha": pull.head_sha,
                "label": f"{owner}:{pull.head_ref}",
            },
            "base": {"ref": pull.base_ref, "label": f"{owner}:{pull.base_ref}"},
            "url": self._api(repository, f"pulls/{pull.number}"),
            "html_url": f"https://github.com/{repository['full_name']}/pull/{pull.number}",
        }

    def _pulls(
        self, request: RecordedRequest, repository: dict[str, Any], _: re.Match[str]
    ) -> Reply:
        data = self.git.setdefault(repository["id"], GitData())
        owner = repository["full_name"].split("/")[0]
        mine = [pull for pull in self.pulls if pull.repository_id == repository["id"]]
        if request.method == "POST":
            asked = json.loads(request.body or b"{}")
            head, base = str(asked.get("head", "")), str(asked.get("base", ""))
            head_ref = head.removeprefix(f"{owner}:")
            if head_ref not in data.refs or base not in data.refs:
                return json_reply({"message": "Validation Failed"}, 422)
            if any(
                p.head_ref == head_ref and p.base_ref == base and p.state == "open" for p in mine
            ):
                return json_reply(
                    {
                        "message": "Validation Failed",
                        "errors": [
                            {
                                "resource": "PullRequest",
                                "code": "custom",
                                "message": f"A pull request already exists for {owner}:{head_ref}.",
                            }
                        ],
                    },
                    422,
                )
            pull = FakePull(
                id=7000000 + len(self.pulls) + 1,
                number=1 + max((p.number for p in mine), default=0),
                repository_id=repository["id"],
                head_ref=head_ref,
                head_sha=data.refs[head_ref],
                base_ref=base,
                title=str(asked.get("title", "")),
                body=str(asked.get("body") or ""),
                draft=bool(asked.get("draft")),
                creator=f"{self.app_slug}[bot]",
            )
            self.pulls.append(pull)
            return json_reply(self._pull_json(repository, pull), 201)
        query = parse_qs(urlsplit(request.path).query)
        head_filter = query.get("head", [None])[0]
        state = query.get("state", ["open"])[0]
        listed = [
            pull
            for pull in mine
            if (head_filter is None or f"{owner}:{pull.head_ref}" == head_filter)
            and (state == "all" or pull.state == state)
        ]
        return json_reply([self._pull_json(repository, pull) for pull in listed])

    def _read_pull(
        self, request: RecordedRequest, repository: dict[str, Any], match: re.Match[str]
    ) -> Reply:
        found = [
            p for p in self.pulls_in(repository["full_name"]) if p.number == int(match["number"])
        ]
        if not found:
            return json_reply({"message": "Not Found"}, 404)
        return json_reply(self._pull_json(repository, found[0]))

    def _revoke(self, request: RecordedRequest) -> Reply:
        minted = self._installation_token(request)
        if request.method != "DELETE" or minted is None:
            return json_reply({"message": "Bad credentials"}, 401)
        minted.revoked = True
        return Reply(status=204, body=b"")


@contextmanager
def serve_fake_github() -> Iterator[FakeGitHub]:
    """A fresh fake with its own App key, on this machine's private address."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    with serve_recording_site() as site:
        fake = FakeGitHub(site=site, private_key_pem=pem, public_key=key.public_key())
        fake.start()
        yield fake
