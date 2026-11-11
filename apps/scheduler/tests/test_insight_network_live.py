"""What the AI role can reach, tried from inside its running container (F3 plan T3.10, AC42).

Runs only in `just insight-fence`, against the kit it installed under its own Compose project
(`NEXOVUE_FENCE_PROJECT`): CI's dev stack starts `docker-compose.yaml`, which would prove the
wrong file. Every probe is a plain TCP connect from Python in the container, which has no curl.

Kafka, ClickHouse and the object store must be unreachable by name, by their addresses on the
default network, and through the `ai` network's gateway; Postgres must be reachable by name;
and a recording site on the Docker host, outside Compose, must be reachable at the gateway, as
a provider on the operator's own network would be. A cloud metadata address is tried and its
answer only printed: Compose is not an outbound firewall, so the host's rules decide it
(`docs/product/setup/ai.md`, plan T4.8).
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
import warnings
from typing import Final

import pytest
from recording_site import serve_recording_site

pytestmark = pytest.mark.insight_fence

PROJECT: Final = os.environ.get("NEXOVUE_FENCE_PROJECT", "")
CONNECT_SECONDS: Final = 3
# What the role must not reach, by service and port, in the self-hosted layout.
CLOSED: Final = {"kafka": 9092, "clickhouse": 8123, "seaweedfs": 8333}
METADATA: Final = ("169.254.169.254", 80)


def _require_install() -> None:
    if not PROJECT:
        raise RuntimeError(
            "NEXOVUE_FENCE_PROJECT is not set: run this through `just insight-fence`, which "
            "installs a kit and names its Compose project. Not skipped: a skip here would read "
            "as a fence that held."
        )


def _container(service: str) -> str:
    return f"{PROJECT}-{service}-1"


def _docker(*arguments: str) -> str:
    finished = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, timeout=60, check=False
    )
    assert finished.returncode == 0, finished.stderr
    return finished.stdout


def _connects_from_insight(host: str, port: int) -> str:
    """`open`, or why not, for one TCP connect made inside `scheduler-insight`."""
    probe = textwrap.dedent(
        f"""
        import socket
        try:
            socket.create_connection(({host!r}, {port}), {CONNECT_SECONDS}).close()
            print("open")
        except OSError as error:
            print(type(error).__name__)
        """
    )
    return _docker("exec", _container("scheduler-insight"), "python", "-c", probe).strip()


def _address_on(service: str, network: str) -> str:
    networks = json.loads(
        _docker("inspect", "--format", "{{json .NetworkSettings.Networks}}", _container(service))
    )
    return str(networks[f"{PROJECT}_{network}"]["IPAddress"])


def _gateway_of(network: str) -> str:
    return _docker(
        "network",
        "inspect",
        "--format",
        "{{(index .IPAM.Config 0).Gateway}}",
        f"{PROJECT}_{network}",
    ).strip()


def test_the_stores_it_must_not_touch_are_closed_to_it() -> None:
    _require_install()
    gateway = _gateway_of("ai")
    for service, port in CLOSED.items():
        address = _address_on(service, "default")
        for target in (service, address, gateway):
            assert _connects_from_insight(target, port) != "open", (service, target)


def test_postgres_is_open_to_it_by_name() -> None:
    _require_install()
    assert _connects_from_insight("postgres", 5432) == "open"


def test_a_peer_outside_compose_is_open_at_the_gateway() -> None:
    """Where an operator's own model would be: on the host, outside Compose (AC42)."""
    _require_install()
    gateway = _gateway_of("ai")
    with serve_recording_site() as site:
        assert _connects_from_insight(gateway, site.port) == "open"


def test_the_metadata_address_is_tried_and_recorded() -> None:
    """Recorded honestly, never asserted: only the host's firewall can close it."""
    _require_install()
    answer = _connects_from_insight(*METADATA)
    # In the run's warnings summary, for the fence's transcript.
    warnings.warn(
        f"AC42: metadata address {METADATA[0]}:{METADATA[1]} from scheduler-insight: {answer}",
        stacklevel=1,
    )
