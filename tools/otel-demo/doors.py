#!/usr/bin/env python3
"""Open the doors the demo cannot open for itself.

`just otel-demo up` starts twenty containers that report telemetry. Three of our
doors need something *created* before they have anything to report through:
door 2 needs check definitions, and door 4a needs a signed deploy event. Neither
is something a container can do on its own.

**Credentials come off the product, never out of a constant.** This logs in as
the seeded user and asks the API for what it needs, which is the pattern the e2e
suite follows. It matters most for door 4a: the signature is the only thing
separating a real deploy notification from a forged one, so a fixed secret in
`seed.py` would be strictly worse than a discovered one.

Standard library only, and deliberately: this runs from a bash script beside a
docker compose invocation, and a tool that needed `uv` or `jq` to be present
would be one more thing to install before the demo works.

Idempotent. `up` is run repeatedly and must not mint a second check each time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from typing import Any, Final

API = os.environ.get("NEXOVUE_API_URL", "http://localhost:18000").rstrip("/")
EMAIL = os.environ.get("NEXOVUE_DEV_EMAIL", "nexovue@nexovue.com")
PASSWORD = os.environ.get("NEXOVUE_DEV_PASSWORD", "nexovue-dev")

# The site key the storefront's pages carry, and the two origins it must allow.
# Asserted rather than created: `seed.py` owns the key, and a mismatch here means
# a database seeded before those origins existed.
SITE_KEY: Final = os.environ.get("NEXOVUE_SITE_KEY", "nvp_live_devlocaldevlocaldevloc")
REQUIRED_ORIGINS: Final = ("http://localhost:8080", "http://frontend-proxy:8080")

# The release the storefront reports as `service.version`. The same string is
# substituted into the injected RUM snippet by the Envoy template, which is what
# makes a browser error and its deploy event name one build.
RELEASE: Final = os.environ.get("NEXOVUE_RUM_RELEASE", "unknown")

# The storefront, as the probe workers reach it. Not `localhost`: the workers are
# containers, and their localhost is themselves.
STOREFRONT: Final = os.environ.get("NEXOVUE_DEMO_STOREFRONT", "http://host.docker.internal:8080/")

CHECK_INTERVAL_SECONDS: Final = 60

# The storefront's own product API, which the catalogue page reads. Listing a
# product is the shallowest check that fails when the catalogue is down — the
# page itself returns 200 around an empty list.
PRODUCTS_API: Final = STOREFRONT.rstrip("/") + "/api/products"

STATUS_BELOW_400: Final = {"source": "status_code", "comparison": "less_than", "target": "400"}


class DoorsError(Exception):
    """Something the person running the demo has to fix, phrased for them."""


def main() -> int:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    try:
        sign_in(opener)
        project_id = only_project(opener)
        assert_site_key_allows_the_storefront(opener, project_id)
        ensure_checks(opener, project_id)
        announce_deploys(opener, project_id)
    except DoorsError as failure:
        print(f"doors: {failure}", file=sys.stderr)
        return 1
    except urllib.error.URLError as failure:
        print(f"doors: could not reach the API at {API}: {failure}", file=sys.stderr)
        print("  is `just dev` up?", file=sys.stderr)
        return 1
    return 0


# ----------------------------------------------------------------- the product


def call(
    opener: urllib.request.OpenerDirector,
    method: str,
    path: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """One API call, as the product's own client would make it."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(f"{API}{path}", data=data, method=method)
    request.add_header("content-type", "application/json")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    with opener.open(request, timeout=30) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def sign_in(opener: urllib.request.OpenerDirector) -> None:
    try:
        call(opener, "POST", "/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})
    except urllib.error.HTTPError as failure:
        raise DoorsError(
            f"could not sign in as {EMAIL} ({failure.code}). Has the database been seeded? "
            "Run `just seed`."
        ) from failure


def only_project(opener: urllib.request.OpenerDirector) -> str:
    projects = call(opener, "GET", "/api/v1/projects")["projects"]
    if not projects:
        raise DoorsError("the seeded organization has no project, which a seed always creates")
    return str(projects[0]["id"])


def assert_site_key_allows_the_storefront(
    opener: urllib.request.OpenerDirector, project_id: str
) -> None:
    """Fail loudly on a stale database rather than quietly on an empty screen.

    A site key whose origins do not include the storefront refuses 100% of its
    traffic with a 403, and that reads exactly like the SDK being broken. It is
    the one thing here worth checking before anything else is created.
    """
    keys = call(opener, "GET", f"/api/v1/site-keys?project_id={project_id}")["site_keys"]
    key = next((entry for entry in keys if entry["public_key"] == SITE_KEY), None)
    if key is None:
        raise DoorsError(
            f"no site key {SITE_KEY[:16]}… on this project. The storefront's pages carry it, "
            "so nothing browser-side can report. Run `just seed`."
        )

    missing = [origin for origin in REQUIRED_ORIGINS if origin not in key["allowed_origins"]]
    if missing:
        raise DoorsError(
            f"site key {SITE_KEY[:16]}… does not allow {', '.join(missing)}, so the storefront's "
            "telemetry will be refused with a 403 that looks like a broken SDK. "
            "Run `just seed` — it reconciles this one field."
        )
    print(f"  site key allows the storefront ({len(key['allowed_origins'])} origins)")


# ---------------------------------------------------------------- door 2


def ensure_checks(opener: urllib.request.OpenerDirector, project_id: str) -> None:
    """The demo's checks, created once by name and left alone on every later run."""
    existing = {
        check["name"]
        for check in call(opener, "GET", f"/api/v1/checks?project_id={project_id}")["checks"]
    }

    wanted: list[dict[str, Any]] = [
        {
            "project_id": project_id,
            "name": "Storefront",
            "interval_seconds": CHECK_INTERVAL_SECONDS,
            "config": {
                "type": "api",
                "request": {"method": "GET", "url": STOREFRONT, "assertions": [STATUS_BELOW_400]},
            },
        },
        {
            "project_id": project_id,
            "name": "Catalogue lists a product",
            "interval_seconds": CHECK_INTERVAL_SECONDS,
            "degraded_ms": 1500,
            "config": {
                "type": "multistep",
                "steps": [
                    {
                        "name": "open the storefront",
                        "url": STOREFRONT,
                        "assertions": [STATUS_BELOW_400],
                    },
                    {
                        "name": "a product is listed",
                        "url": PRODUCTS_API,
                        "assertions": [
                            {"source": "status_code", "comparison": "equals", "target": "200"},
                            {
                                "source": "json_path",
                                "property": "$[0].id",
                                "comparison": "is_not_empty",
                            },
                        ],
                    },
                ],
            },
        },
    ]

    for check in wanted:
        if check["name"] in existing:
            print(f"  check already defined: {check['name']}")
            continue
        try:
            created = call(opener, "POST", "/api/v1/checks", check)
        except urllib.error.HTTPError as failure:
            raise DoorsError(
                f"the API refused the check {check['name']!r} "
                f"({failure.code}): {failure.read().decode('utf-8', 'replace')[:300]}"
            ) from failure
        print(f"  check created: {check['name']} ({created['id']})")


# ---------------------------------------------------------------- door 4a


def announce_deploys(opener: urllib.request.OpenerDirector, project_id: str) -> None:
    """Tell the product this demo was just deployed, signed as a real CI job would.

    Two services in one batch (F28's envelope), so the list is a list rather than a
    single row, and both carry the release the storefront's pages report — which is
    what lets an error and the build it came from be the same build.

    A secret is shown once (F28 D7), so this mints one for the run and withdraws it
    afterwards, as a person rotating would, rather than leaving one per `up` behind.
    """
    secret_id, secret, path, signature_header = mint_webhook_secret(opener, project_id)
    try:
        # One delivery id per build and service: `up` is run repeatedly, and announcing
        # the same build again is a repeat the webhook answers without storing.
        now = datetime.now(UTC).isoformat()
        body = json.dumps(
            {
                "deploys": [
                    {
                        "delivery_id": f"otel-demo-{service_name}-{RELEASE}",
                        "service_name": service_name,
                        "version": RELEASE,
                        "deployed_at": now,
                        "source": "otel-demo",
                        "metadata": {"upstream_ref": os.environ.get("NEXOVUE_UPSTREAM_REF", "")},
                    }
                    for service_name in ("frontend-web", "frontend")
                ]
            }
        ).encode("utf-8")
        # Over the exact bytes sent, which is the whole point: re-serialising
        # would sign something the server never sees.
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

        request = urllib.request.Request(f"{API}{path}", data=body, method="POST")
        request.add_header("content-type", "application/json")
        request.add_header(signature_header, f"sha256={digest}")
        try:
            with opener.open(request, timeout=30) as response:
                answer = json.loads(response.read())
        except urllib.error.HTTPError as failure:
            raise DoorsError(
                f"the deploy webhook refused a signed batch "
                f"({failure.code}): {failure.read().decode('utf-8', 'replace')[:200]}"
            ) from failure
    finally:
        call(opener, "DELETE", f"/api/v1/deploys/webhook-secrets/{secret_id}")

    # The answer's own count, item by item: a repeat says so rather than storing again.
    for item in answer["deploys"]:
        print(
            f"  {item['delivery_id']}: "
            + ("already announced" if item["repeated"] else f"recorded as {item['deploy_id']}")
        )


def mint_webhook_secret(
    opener: urllib.request.OpenerDirector, project_id: str
) -> tuple[str, str, str, str]:
    """A new secret, with its id, the path and the header to sign with.

    All come back from the API rather than being spelled here, so a change to any is
    a change in one place.
    """
    minted = call(opener, "POST", f"/api/v1/deploys/webhook-secrets?project_id={project_id}")
    return (
        minted["minted"]["id"],
        minted["minted"]["secret"],
        minted["webhook_path"],
        minted["signature_header"],
    )


if __name__ == "__main__":
    sys.exit(main())
