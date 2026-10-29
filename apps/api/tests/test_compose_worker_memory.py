"""A worker's heap ceiling matches the box it runs in (`docs/changes/one-worker-again/`).

Go's collector cannot see a cgroup limit — as of Go 1.26 the proposal to derive
one is still open — so a Go service in a container with no `GOMEMLIMIT` grows its
heap until the kernel kills the process. Measured on 2026-09-19: the worker was
OOM-killed at 1017 MB RSS against a 1 GiB cap, and every pipeline in it stopped.

Profiles were moved into a process of their own that day and back out of it on
2026-09-20, by the founder's decision. What protected spans was never really the
second process: it was the budget. One process holding every pipeline needs the
budget of both, and `GOMEMLIMIT` has to be raised with the limit or the extra
memory is unusable.

So this is what the deleted `test_compose_signal_isolation.py` is replaced by: the
two numbers are one setting written in two places, and they must agree — in every
target, for every Go service that declares a limit.

Read from the compose files, as `test_compose_dependencies.py` reads them: the
defect is in the declaration.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest
import yaml

import nexovue_api

DEPLOYMENTS: Final = (
    "docker-compose.yaml",
    "nexovue.yaml",
)

# The share of the container the heap may claim. The rest is stacks, the runtime's
# own allocations and, in development, the compiler the dev images host.
HEAP_SHARE: Final = 0.85
# Judged against the share rather than exactly: 85% of 1536M is 1305.6MiB, and a
# declaration rounded to 1300MiB is the same decision.
TOLERANCE: Final = 0.06

_SIZE: Final = re.compile(r"^(\d+(?:\.\d+)?)\s*(K|M|G|Ki|Mi|Gi)?B?$", re.IGNORECASE)
_UNITS: Final = {
    "": 1,
    "k": 1_000,
    "m": 1_000_000,
    "g": 1_000_000_000,
    "ki": 1024,
    "mi": 1024**2,
    "gi": 1024**3,
}


class Worker(NamedTuple):
    service: str
    limit_bytes: int
    heap_bytes: int


def _bytes(value: str) -> int | None:
    """A compose size or a `GOMEMLIMIT`, in bytes. None when it is not a literal."""
    match = _SIZE.match(value.strip())
    if match is None:
        return None
    amount, unit = match.groups()
    return int(float(amount) * _UNITS[(unit or "").lower()])


def _default(value: str) -> str:
    """`${WORKER_MEMORY:-1280M}` → `1280M`. A value with no default stays as it is.

    A deployment may raise either number through its environment; what is checked
    here is the pair this repository ships, which is what an install that sets
    nothing gets.
    """
    inner = value.strip()
    if inner.startswith("${") and inner.endswith("}"):
        _, _, fallback = inner[2:-1].partition(":-")
        return fallback
    return inner


def _go_services(file_name: str) -> list[Worker]:
    """Every service that sets both a memory limit and a heap ceiling."""
    path = Path(nexovue_api.__file__).parents[4] / "deploy" / "compose" / file_name
    services: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))["services"]

    found: list[Worker] = []
    for name, definition in services.items():
        environment = definition.get("environment") or {}
        raw_heap = environment.get("GOMEMLIMIT")
        raw_limit = (
            ((definition.get("deploy") or {}).get("resources") or {})
            .get("limits", {})
            .get("memory")
        )
        if raw_heap is None or raw_limit is None:
            continue
        heap = _bytes(_default(str(raw_heap)))
        limit = _bytes(_default(str(raw_limit)))
        if heap is None or limit is None:
            continue
        found.append(Worker(service=name, limit_bytes=limit, heap_bytes=heap))
    return found


@pytest.mark.parametrize("file_name", DEPLOYMENTS)
def test_every_go_service_holds_its_heap_under_its_container(file_name: str) -> None:
    services = _go_services(file_name)

    assert services, f"{file_name}: no service declares both a memory limit and GOMEMLIMIT"
    for service in services:
        share = service.heap_bytes / service.limit_bytes
        assert abs(share - HEAP_SHARE) <= TOLERANCE, (
            f"{file_name}: {service.service} gives its heap {share:.0%} of its container "
            f"({service.heap_bytes:,} of {service.limit_bytes:,} bytes). Too high and the "
            f"kernel kills it before the collector reacts; too low and the memory is bought "
            f"and unusable. Keep it near {HEAP_SHARE:.0%}."
        )


@pytest.mark.parametrize("file_name", DEPLOYMENTS)
def test_the_worker_declares_a_ceiling_at_all(file_name: str) -> None:
    """The on-premise file shipped without one until 2026-09-20: a limit with no
    ceiling under it is the September incident waiting for a busy day."""
    assert any(service.service == "worker" for service in _go_services(file_name)), (
        f"{file_name}: `worker` declares no GOMEMLIMIT beside its memory limit"
    )
