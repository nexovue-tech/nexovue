"""The deploy recipes on the setup page are run, and the API accepts what they send
(F28 FR14, FR41, AC48, AC49).

A recipe is code a customer pastes into their pipeline, so it is tested as code: each
of the three on `docs/product/setup/deploys.md` is extracted, given what its CI would
give it, and run with `bash` against a local server that records the request. The
request is then handed to the webhook's own service, with the secret sealed as the API
seals one, which must accept it.

What this cannot prove is GitHub's or GitLab's own environment; a hosted runner is
tried once from a reachable address (plan T2.7).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import socket
import subprocess
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from typing import Any, Final

import pytest
import yaml
from nexovue_platform.checks.sealing import (
    DEPLOY_WEBHOOK_PURPOSE,
    Keyring,
    SealedBox,
    decode_key,
    deploy_webhook_owner,
    generate_key,
)

from nexovue_api.deploys import queries, service, signatures
from nexovue_api.ratelimit.limiter import Decision
from nexovue_api.ratelimit.policy import DEPLOYS_ACCEPTED

REPOSITORY: Final = Path(__file__).resolve().parents[3]
SETUP: Final = REPOSITORY / "docs" / "product" / "setup"
DEPLOYS_PAGE: Final = SETUP / "deploys.md"
ONBOARDING: Final = REPOSITORY / "apps" / "web" / "src" / "features" / "onboarding"
# The address the page writes in place of the project's own.
PAGE_URL: Final = "https://nexovue.example.com/api/v1/deploys/webhook/PROJECT_ID"
PROJECT_ID: Final = uuid.uuid4()
ORG_ID: Final = uuid.uuid4()
SECRET_ID: Final = uuid.uuid4()
SECRET: Final = signatures.new_secret()
COMMIT: Final = "3f9c2a1b7e" * 4
# Every recipe finishes well inside this, even retrying a closed port.
RUN_TIMEOUT_SECONDS: Final = 60
SENT_FIELDS: Final = {
    "delivery_id",
    "service_name",
    "version",
    "deployed_at",
    "commit_sha",
    "url",
}


# ------------------------------------------------------------ AC48: versions

# `service.version=<value>` or `serviceVersion: <value>`, wherever a snippet sets one.
_VERSION_SET: Final = re.compile(r"(?:service\.version\s*=|serviceVersion\s*:)\s*\"?([^\s,\"'`)]+)")
# A value read from the build: a shell, Kubernetes or Helm reference, or a bundler's.
_FROM_THE_BUILD: Final = ("$", "{{", "import.meta.env.")


def literal_versions(text: str) -> list[str]:
    """Every `service.version` a snippet sets to a fixed value."""
    return [value for value in _VERSION_SET.findall(text) if not value.startswith(_FROM_THE_BUILD)]


def _setup_sources() -> list[Path]:
    return sorted([*SETUP.rglob("*.md"), *ONBOARDING.glob("*.tsx"), *ONBOARDING.glob("*.ts")])


def test_the_check_finds_a_fixed_version() -> None:
    """Guards the check itself."""
    assert literal_versions("OTEL_RESOURCE_ATTRIBUTES=service.version=1.4.0,a=b") == ["1.4.0"]
    assert literal_versions('serviceVersion: "latest",') == ["latest"]
    assert literal_versions("service.version=$(SERVICE_VERSION)") == []


@pytest.mark.parametrize("path", _setup_sources(), ids=lambda path: path.name)
def test_no_setup_snippet_sets_a_fixed_version(path: Path) -> None:
    """AC48: a fixed value reads as one release that never ends (FR41)."""
    assert literal_versions(path.read_text(encoding="utf-8")) == [], path


def test_the_pages_that_teach_a_version_are_the_ones_expected() -> None:
    """The language pages and onboarding teach it now; a page losing it is a regression."""
    teaching = {path.name for path in _setup_sources() if "service.version" in path.read_text()}

    assert {
        "deploys.md",
        "kubernetes.md",
        "aws-ec2-and-bare-metal.md",
        "go.md",
        "java.md",
        "nodejs.md",
        "python.md",
        "SetupGuides.tsx",
    } <= teaching


# ------------------------------------------------------------- the recipes


def _block_after(heading: str, language: str) -> str:
    page = DEPLOYS_PAGE.read_text(encoding="utf-8")
    after = page[page.index(f"\n{heading}\n") :]
    found = re.search(rf"```{language}\n(.*?)\n```", after, re.DOTALL)
    assert found is not None, f"no {language} block under {heading!r}"
    return found.group(1)


@dataclass(frozen=True)
class Recipe:
    """A recipe as its CI would run it: a script and its environment."""

    script: str
    environment: dict[str, str]


_GITHUB_EXPRESSION: Final = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")


def github_recipe(attempt: int) -> Recipe:
    (step,) = yaml.safe_load(_block_after("### GitHub Actions", "yaml"))
    context = {
        "secrets.NEXOVUE_DEPLOY_SECRET": SECRET,
        "github.sha": COMMIT,
        "github.run_id": "9876543210",
        "github.run_attempt": str(attempt),
        "github.server_url": "https://github.com",
        "github.repository": "acme/checkout",
    }

    def evaluate(text: str) -> str:
        return _GITHUB_EXPRESSION.sub(lambda match: context[match.group(1)], text)

    return Recipe(
        script=evaluate(step["run"]),
        environment={name: evaluate(str(value)) for name, value in step["env"].items()},
    )


def gitlab_recipe(job_id: int) -> Recipe:
    job = yaml.safe_load(_block_after("### GitLab CI", "yaml"))["report-deploy"]
    predefined = {
        "CI_COMMIT_SHA": COMMIT,
        "CI_JOB_ID": str(job_id),
        "CI_PROJECT_URL": "https://gitlab.com/acme/checkout",
    }
    # GitLab expands its predefined variables inside `variables:`; the secret is a masked
    # CI/CD variable, handed to the job as an environment variable.
    environment = {
        name: Template(str(value)).substitute(predefined)
        for name, value in job["variables"].items()
    }
    return Recipe(
        script=job["script"][0],
        environment={**predefined, **environment, "NEXOVUE_DEPLOY_SECRET": SECRET},
    )


def curl_recipe(attempt: int) -> Recipe:
    return Recipe(
        script=_block_after("### curl", "bash"),
        environment={
            "NEXOVUE_DEPLOY_SECRET": SECRET,
            "BUILD_VERSION": COMMIT,
            "BUILD_ID": "4242",
            "BUILD_ATTEMPT": str(attempt),
            "GIT_COMMIT": COMMIT,
        },
    )


RECIPES: Final = {"github": github_recipe, "gitlab": gitlab_recipe, "curl": curl_recipe}


@dataclass
class Received:
    path: str
    signature: str | None
    body: bytes


@dataclass
class Endpoint:
    """A local stand-in for the API that records what arrives."""

    url: str
    answer: int
    received: list[Received] = field(default_factory=list)


@pytest.fixture
def endpoint() -> Iterator[Endpoint]:
    found = Endpoint(url="", answer=200)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            found.received.append(
                Received(
                    path=self.path,
                    signature=self.headers.get(signatures.SIGNATURE_HEADER),
                    body=self.rfile.read(length),
                )
            )
            self.send_response(found.answer)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    found.url = f"http://127.0.0.1:{server.server_address[1]}/api/v1/deploys/webhook/{PROJECT_ID}"
    try:
        yield found
    finally:
        server.shutdown()
        server.server_close()


def _closed_port_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/api/v1/deploys/webhook/{PROJECT_ID}"


def run(recipe: Recipe, url: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    """As a CI runs a step: `bash -e -o pipefail`, GitHub's and GitLab's default."""
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", recipe.script.replace(PAGE_URL, url)],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", **recipe.environment, **overrides},
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )


# ------------------------------------------------- accepted by the API


BOX: Final = SealedBox(Keyring(current=decode_key(generate_key())), purpose=DEPLOY_WEBHOOK_PURPOSE)


@dataclass
class _Secret:
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    sealed_secret: bytes


class _Session:
    async def commit(self) -> None:
        return None


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The webhook service over stubbed reads, with one secret sealed as minted."""
    checks: list[str] = []
    sealed = _Secret(
        id=SECRET_ID,
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        sealed_secret=BOX.seal(SECRET, owner=deploy_webhook_owner(str(PROJECT_ID), str(SECRET_ID))),
    )

    async def live(session: Any, *, project_id: uuid.UUID) -> list[Any]:
        return [sealed]

    async def nothing(session: Any, **_: Any) -> None:
        return None

    async def check(session: Any, **_: Any) -> None:
        checks.append("checked")

    async def none_known(session: Any, **_: Any) -> set[str]:
        return set()

    async def none_seen(session: Any, **_: Any) -> dict[str, list[str]]:
        return {}

    monkeypatch.setattr(queries, "live_webhook_secrets", live)
    monkeypatch.setattr(queries, "mark_secret_used", nothing)
    monkeypatch.setattr(queries, "record_check", check)
    monkeypatch.setattr(queries, "known_services", none_known)
    monkeypatch.setattr(queries, "seen_versions", none_seen)
    return checks


async def _accept(received: Received) -> Any:
    async def charge(cost: int) -> Decision:
        return Decision(allowed=True, quota=DEPLOYS_ACCEPTED, remaining=0, reset_seconds=1)

    return await service.record_from_webhook(
        _Session(),  # type: ignore[arg-type]
        project_id=PROJECT_ID,
        body=received.body,
        signature=received.signature,
        box=BOX,
        charge=charge,
    )


@pytest.mark.parametrize("kind", sorted(RECIPES))
async def test_each_recipes_dry_run_is_accepted_by_the_api(
    kind: str, endpoint: Endpoint, api: list[str]
) -> None:
    """AC49: the signing step, as pasted, signs a body the webhook accepts; the dry run is
    checked and stores nothing, as the setup page tells a person to start."""
    finished = run(RECIPES[kind](1), endpoint.url)

    assert finished.returncode == 0, finished.stderr
    (received,) = endpoint.received
    assert received.path == f"/api/v1/deploys/webhook/{PROJECT_ID}"
    answer = await _accept(received)
    assert answer.dry_run is True
    assert api == ["checked"]
    (item,) = answer.deploys
    assert item.delivery_id.startswith("checkout-")


@pytest.mark.parametrize("kind", sorted(RECIPES))
def test_without_the_dry_run_each_recipe_sends_a_real_deploy(kind: str, endpoint: Endpoint) -> None:
    """Removing the dry-run line, as the page says, sends a deploy that passes every check,
    signed by the secret, carrying the commit and its link."""
    recipe = RECIPES[kind](1)
    script = "\n".join(
        line for line in recipe.script.splitlines() if "NEXOVUE_DRY_RUN=" not in line
    )
    environment = {
        key: value for key, value in recipe.environment.items() if key != "NEXOVUE_DRY_RUN"
    }

    finished = run(Recipe(script, environment), endpoint.url)

    assert finished.returncode == 0, finished.stderr
    (received,) = endpoint.received
    batch = service._parse(received.body, now=_now())
    assert batch.dry_run is False
    (item,) = batch.deploys
    assert (item.version, item.commit_sha) == (COMMIT, COMMIT)
    assert item.url is not None and item.url.endswith(f"/commit/{COMMIT}")
    assert (
        signatures.matching_secret(
            body=received.body, header_value=received.signature, secrets=[SECRET]
        )
        == 0
    )


@pytest.mark.parametrize("kind", sorted(RECIPES))
def test_each_recipe_sends_no_actor_or_context_fields(kind: str, endpoint: Endpoint) -> None:
    """FR14, AC49: only the deploy's own facts; nothing naming who ran it."""
    finished = run(RECIPES[kind](1), endpoint.url)

    assert finished.returncode == 0
    (received,) = endpoint.received
    (item,) = json.loads(received.body)["deploys"]
    assert set(item) == SENT_FIELDS


@pytest.mark.parametrize("kind", sorted(RECIPES))
def test_each_recipe_reads_the_secret_from_the_environment_never_as_an_argument(
    kind: str,
) -> None:
    """A process argument is readable by every process on the runner (FR14)."""
    script = RECIPES[kind](1).script

    assert 'os.environ["NEXOVUE_DEPLOY_SECRET"]' in script
    assert "$NEXOVUE_DEPLOY_SECRET" not in script
    assert "${NEXOVUE_DEPLOY_SECRET" not in script
    assert SECRET not in script


def test_the_github_recipe_takes_the_secret_from_its_secret_store() -> None:
    (step,) = yaml.safe_load(_block_after("### GitHub Actions", "yaml"))

    assert step["env"]["NEXOVUE_DEPLOY_SECRET"] == "${{ secrets.NEXOVUE_DEPLOY_SECRET }}"
    assert "actor" not in json.dumps(step) and "toJson" not in json.dumps(step)


@pytest.mark.parametrize("kind", sorted(RECIPES))
def test_each_recipe_exits_0_with_a_warning_when_the_endpoint_is_down(kind: str) -> None:
    """BR13: nexovue must not be able to break a customer's deploy."""
    finished = run(RECIPES[kind](1), _closed_port_url())

    assert finished.returncode == 0
    assert "did not record this deploy (HTTP 000)" in finished.stdout


@pytest.mark.parametrize("kind", sorted(RECIPES))
def test_each_recipe_exits_0_with_a_warning_on_a_refused_item(
    kind: str, endpoint: Endpoint
) -> None:
    endpoint.answer = 422

    finished = run(RECIPES[kind](1), endpoint.url)

    assert finished.returncode == 0
    assert "did not record this deploy (HTTP 422)" in finished.stdout


@pytest.mark.parametrize("kind", sorted(RECIPES))
def test_each_recipe_still_exits_0_when_signing_cannot_run(kind: str, endpoint: Endpoint) -> None:
    """A runner without python3 sends an unsigned request, which is refused, and warns."""
    endpoint.answer = 401

    finished = run(RECIPES[kind](1), endpoint.url, PATH="/nonexistent:/usr/bin:/bin")

    assert finished.returncode == 0
    assert "did not record this deploy" in finished.stdout


@pytest.mark.parametrize("kind", sorted(RECIPES))
def test_a_re_run_sends_a_new_delivery_id(kind: str, endpoint: Endpoint) -> None:
    """FR14: stable across one step's retries, new on a re-run, which the run id alone
    would treat as a repeat."""
    run(RECIPES[kind](1), endpoint.url)
    run(RECIPES[kind](2), endpoint.url)

    first, second = (json.loads(one.body)["deploys"][0]["delivery_id"] for one in endpoint.received)
    assert first != second


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)


def test_the_page_signs_the_way_the_api_verifies() -> None:
    """The scheme the page names is the one `signatures.py` reads."""
    page = DEPLOYS_PAGE.read_text(encoding="utf-8")
    body = b'{"deploys":[]}'
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    assert signatures.SIGNATURE_HEADER in page
    assert (
        signatures.matching_secret(body=body, header_value=f"sha256={expected}", secrets=[SECRET])
        == 0
    )
