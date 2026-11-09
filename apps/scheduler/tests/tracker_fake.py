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
- repositories page by `page` and `per_page`.

A route's answer can be replaced (`fail`) to drive each failure the code must survive.
"""

from __future__ import annotations

import json
import secrets
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

    def fail(self, route: str, status: int, *, delay: float = 0.0) -> None:
        self.faults[route] = Reply(status=status, delay=delay, body=b'{"message": "fault"}')

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
