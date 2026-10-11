"""What a deployed install must have, read from both deployment files (readiness group 6).

Each case is a way a deployed install was found broken on 2026-10-10 that no
running test could catch, because nothing runs these files: the API exiting at
boot for want of `OTLP_ENDPOINT`, every customer sharing the proxy's login
throttle, the API and the console published without TLS or headers, a second
replica silently doubling every rate limit, and the erasure holding the
administrator's account.

Files only — no Docker — as `test_compose_dependencies.py` reads them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

import nexovue_api

DEPLOYED: Final = ("docker-compose.app.yaml", "docker-compose.onprem.yaml")

# The edge's upstreams and the port each service listens on inside its container.
LISTENS_ON: Final[dict[str, int]] = {"api": 8000, "receiver": 4318}
UPSTREAM: Final = re.compile(r"dynamic a (\S+) (\d+)")


def _repo() -> Path:
    return Path(nexovue_api.__file__).parents[4]


def _services(file_name: str) -> dict[str, Any]:
    path = _repo() / "deploy" / "compose" / file_name
    return dict(yaml.safe_load(path.read_text(encoding="utf-8"))["services"])


def _environment(file_name: str, service: str) -> dict[str, str]:
    return {
        name: str(value)
        for name, value in (_services(file_name)[service].get("environment") or {}).items()
    }


@pytest.mark.parametrize("file_name", DEPLOYED)
def test_only_the_edge_is_reachable_from_outside(file_name: str) -> None:
    published = sorted(name for name, service in _services(file_name).items() if "ports" in service)
    assert published == ["proxy"], (
        f"{file_name}: {published} publish ports. Everything goes through the edge, which "
        "carries TLS and the security headers; a published API or console bypasses both."
    )


@pytest.mark.parametrize("file_name", DEPLOYED)
def test_the_console_is_served_by_the_edge_not_a_container(file_name: str) -> None:
    assert "web" not in _services(file_name), (
        f"{file_name}: the console's files are served by the edge (ADR-019 point 3)."
    )


@pytest.mark.parametrize("file_name", DEPLOYED)
def test_the_api_hands_customers_the_edge_s_ingest_name(file_name: str) -> None:
    endpoint = _environment(file_name, "api").get("OTLP_ENDPOINT", "")
    assert endpoint.startswith("https://${NEXOVUE_INGEST_HOST"), (
        f"{file_name}: the API's OTLP_ENDPOINT is {endpoint!r}. Outside development the API "
        "refuses to start without a reachable one, and it must be the name the edge serves."
    )


@pytest.mark.parametrize("file_name", DEPLOYED)
@pytest.mark.parametrize("service", ["api", "scheduler-light"])
def test_links_point_at_the_edge_s_app_name(file_name: str, service: str) -> None:
    base = _environment(file_name, service).get("APP_BASE_URL", "")
    assert base.startswith("https://${NEXOVUE_APP_HOST"), (
        f"{file_name}: {service}'s APP_BASE_URL is {base!r}; emailed links must land on the "
        "name the edge serves the console on."
    )


@pytest.mark.parametrize("file_name", DEPLOYED)
def test_the_api_trusts_the_edge_as_its_one_proxy(file_name: str) -> None:
    count = _environment(file_name, "api").get("TRUSTED_PROXY_COUNT")
    assert count == "1", (
        f"{file_name}: TRUSTED_PROXY_COUNT is {count!r}. Behind the edge, 0 makes every "
        "customer the proxy's address, so all of them share one login throttle."
    )


@pytest.mark.parametrize("file_name", DEPLOYED)
@pytest.mark.parametrize(
    ("service", "variable"), [("api", "API_INSTANCES"), ("receiver", "RECEIVER_INSTANCES")]
)
def test_the_instance_count_is_the_replica_count(
    file_name: str, service: str, variable: str
) -> None:
    definition = _services(file_name)[service]
    told = str((definition.get("environment") or {}).get(variable, ""))
    runs = str((definition.get("deploy") or {}).get("replicas", ""))
    assert told.startswith(f"${{{variable}") and runs == told, (
        f"{file_name}: {service} is told {variable}={told!r} and runs replicas={runs!r}. "
        "Each copy enforces its limits divided by the first, so the two must be one setting."
    )


@pytest.mark.parametrize("file_name", DEPLOYED)
def test_every_edge_upstream_is_a_service_on_its_port(file_name: str) -> None:
    caddyfile = (_repo() / "deploy" / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    upstreams = {(name, int(port)) for name, port in UPSTREAM.findall(caddyfile)}
    assert upstreams == set(LISTENS_ON.items()), upstreams
    missing = sorted(name for name, _ in upstreams if name not in _services(file_name))
    assert not missing, f"{file_name}: the edge proxies to {missing}, which it does not run."


def test_the_hosted_edge_allows_checkout_and_self_hosted_does_not() -> None:
    hosted = _environment("docker-compose.app.yaml", "proxy").get("NEXOVUE_CHECKOUT_ORIGINS", "")
    onprem = _environment("docker-compose.onprem.yaml", "proxy").get("NEXOVUE_CHECKOUT_ORIGINS", "")
    assert "razorpay.com" in hosted, "the hosted console takes payments through Razorpay"
    assert onprem == "", "a self-hosted install loads no payment script"


@pytest.mark.parametrize("file_name", [*DEPLOYED, "docker-compose.yaml"])
def test_the_erasure_connects_as_the_account_that_can_only_delete(file_name: str) -> None:
    dsn = _environment(file_name, "scheduler-light").get("CLICKHOUSE_ERASER_DSN", "")
    assert "nexovue_eraser:" in dsn, (
        f"{file_name}: the erasure connects as {dsn.split('@')[0]!r}; it needs SELECT and "
        "ALTER DELETE, not the administrator's DROP, FILE, URL and S3."
    )
