"""Every ClickHouse-backed route is on the tier that exists to protect ClickHouse.

`policy.py` states the reasoning: *"a columnar scan costs orders of magnitude
more than a Postgres row read, and ten concurrent ones will hurt the cluster long
before a per-minute counter notices."* The concurrency cap is the part that
matters, and only the telemetry tier has one.

**The defect this exists for is a rule that protects nothing.** `TELEMETRY_PATHS`
listed `/errors` and `/database`; the routers are mounted at `/error-groups` and
`/dbstats`. Neither entry matched a single request, both readers ran on the
default tier at 300 a minute with no cap, and nothing failed — a prefix that
matches no path is silent. Two renames were all it took.

Structural, like `test_tenant_chokepoint_structure.py`: it reads the routers
rather than the documentation about them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

import pytest

import nexovue_api
from nexovue_api.ratelimit.policy import TELEMETRY_TIER, tier_for_path
from nexovue_api.settings import API_PREFIX

_PREFIX = re.compile(r'APIRouter\(\s*prefix="(?P<prefix>[^"]+)"')

# What a ClickHouse read is named in a router: the request-scoped repository, or
# the type behind it. A router that touches neither is a control-plane route.
_CLICKHOUSE_NAMES: Final = ("TenantRepository", "TenantScopedRepository")


def _router_files() -> list[Path]:
    # The source root rather than one package: a router is judged by where it
    # reads from, never by which package it happens to sit in today.
    return sorted((Path(nexovue_api.__file__).parents[1]).rglob("router.py"))


def _prefixes(source: str) -> list[str]:
    return [match.group("prefix") for match in _PREFIX.finditer(source)]


def _reads_clickhouse(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _CLICKHOUSE_NAMES:
            return True
    return False


_ROUTERS: Final = [
    (path.parent.name, prefix, _reads_clickhouse(path.read_text(encoding="utf-8")))
    for path in _router_files()
    for prefix in _prefixes(path.read_text(encoding="utf-8"))
]


def test_the_routers_were_actually_found() -> None:
    """A structural test that parsed nothing passes for the wrong reason."""
    assert len(_ROUTERS) > 15, f"only {len(_ROUTERS)} routers parsed; the pattern has drifted"
    assert any(reads for _, _, reads in _ROUTERS), "no router appears to read ClickHouse"


@pytest.mark.parametrize(
    ("module", "prefix"),
    [(module, prefix) for module, prefix, reads in _ROUTERS if reads],
)
def test_every_clickhouse_router_is_on_the_telemetry_tier(module: str, prefix: str) -> None:
    tier = tier_for_path(f"{API_PREFIX}{prefix}")

    assert tier is TELEMETRY_TIER, (
        f"{module}'s router at {prefix} reads ClickHouse but falls on the "
        f"{tier.quota.name if tier else 'exempt'} tier, which has no concurrency cap. "
        f"Add {prefix} to TELEMETRY_PATHS."
    )


def test_no_telemetry_rule_protects_a_path_that_does_not_exist() -> None:
    """The other half, and the half that let this happen.

    A rule naming a prefix nothing is mounted at is indistinguishable from a rule
    that works, because neither ever raises. Both `/errors` and `/database` sat
    here looking correct while the routers they named lived elsewhere.
    """
    from nexovue_api.ratelimit.policy import TELEMETRY_PATHS

    mounted = {prefix for _, prefix, _ in _ROUTERS}
    unmatched = [
        rule
        for rule in TELEMETRY_PATHS
        if not any(prefix.startswith(rule.removeprefix(API_PREFIX)) for prefix in mounted)
    ]

    assert not unmatched, (
        f"{unmatched} match no router. A rate-limit rule that matches nothing protects "
        "nothing and reports nothing."
    )


def test_the_concurrency_cap_clears_what_one_screen_asks_for() -> None:
    """A cap under legitimate use is a defect that looks like protection.

    At four, opening one service refused one of its five reads and the screen
    rendered with a panel missing — a 429 nobody would read as a rate limit.
    The cap has to sit above one screen's fan-out and below the concurrency the
    tier exists to prevent.
    """
    from nexovue_api.ratelimit.policy import LARGEST_SCREEN_FANOUT

    assert TELEMETRY_TIER.max_concurrent is not None
    assert TELEMETRY_TIER.max_concurrent > LARGEST_SCREEN_FANOUT, (
        f"the cap is {TELEMETRY_TIER.max_concurrent} and one screen asks for "
        f"{LARGEST_SCREEN_FANOUT} reads at once, so that screen loads incomplete"
    )
