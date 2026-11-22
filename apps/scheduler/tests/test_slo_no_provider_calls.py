"""F19 T6.7's named check: no reliability-target module reaches a provider by plain HTTP.

`test_detection_has_no_ai.py` stops a model SDK, and a direct network client in an SLO module.
It cannot see a client reached through another module of ours, such as the probes' libcurl
wrapper or the mail provider's HTTP client. This walks every module `slos/`, the platform's
`slo` and the API's `slos/` import, through our own code, and fails if one reaches a module
holding a network client.

Four shared modules are the only doors, and are named, because every process reads settings
and every route reads the API's wiring: the two settings modules (which hold the mail and probe
settings), and `nexovue_api.deps` and `nexovue_api.lifespan` (which hold the object store).
They are infrastructure an SLO module does not call through, and the control below shows the
walk finds the clients behind them when it is let through, so a pass is not blindness.
"""

from __future__ import annotations

from collections import deque
from typing import Final

from nexovue_platform.import_walk import (
    NETWORK_CLIENTS,
    imports_of,
    module_file,
    package_modules,
)

SLO_ROOTS: Final = ("nexovue_scheduler.slos", "nexovue_platform.slo", "nexovue_api.slos")
SHARED_DOORS: Final = frozenset(
    {
        "nexovue_scheduler.settings",
        "nexovue_api.settings",
        "nexovue_api.deps",
        "nexovue_api.lifespan",
    }
)


def _parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:end]) for end in range(1, len(parts) + 1)]


def clients_reached(start: list[str], *, through: frozenset[str] = frozenset()) -> list[str]:
    """Each network client an import chain from `start` reaches, as the chain. Modules in
    `SHARED_DOORS` are not entered unless named in `through`."""
    came_from: dict[str, str | None] = dict.fromkeys(start)
    queue = deque(start)
    reached: list[str] = []
    while queue:
        module = queue.popleft()
        path = module_file(module)
        if path is None:
            continue
        for imported in sorted(imports_of(module, path)):
            if any(imported == name or imported.startswith(f"{name}.") for name in NETWORK_CLIENTS):
                chain = [imported, module]
                while (parent := came_from[chain[-1]]) is not None:
                    chain.append(parent)
                reached.append(" <- ".join(chain))
                continue
            for step in _parents(imported):
                shut = step in SHARED_DOORS and step not in through
                if step not in came_from and module_file(step) is not None and not shut:
                    came_from[step] = module
                    queue.append(step)
    return reached


def _slo_modules() -> list[str]:
    modules = [name for root in SLO_ROOTS for name in package_modules(root)]
    assert "nexovue_scheduler.slos.job" in modules and "nexovue_api.slos.router" in modules
    return modules


def test_no_slo_module_reaches_a_network_client_through_our_own_code() -> None:
    assert clients_reached(_slo_modules()) == []


def test_the_walk_finds_the_clients_behind_the_two_shared_doors() -> None:
    """The control: let the walk through the doors and it finds the mail provider's client
    and the object store's, so an empty answer above is not a walk that sees nothing."""
    found = clients_reached(_slo_modules(), through=SHARED_DOORS)
    assert any(line.startswith("httpx") for line in found), found
    assert any(line.startswith("boto3") for line in found), found


def test_the_walk_finds_a_client_planted_one_module_away() -> None:
    """A second control, by a module that really holds one: the probes' HTTP check."""
    assert any(
        line.startswith("pycurl")
        for line in clients_reached(["nexovue_scheduler.probes.http_check"])
    )
