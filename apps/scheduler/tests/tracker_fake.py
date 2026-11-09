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

A route's answer can be replaced (`fail`) to drive each failure the code must survive.
"""

from __future__ import annotations

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
        return not_found

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
        full_name = f"{match['owner']}/{match['name']}"
        found = [each for each in self.repositories if each["full_name"] == full_name]
        if not found:
            return json_reply({"message": "Not Found"}, 404)
        [repository] = found
        reached = minted.repository_ids is None or repository["id"] in minted.repository_ids
        if not reached or repository["id"] in self.removed_repository_ids:
            return json_reply({"message": "Not Found"}, 404)
        reply = handler(request, repository, match)
        if request.method in ("POST", "PATCH") and 200 <= reply.status < 300:
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
