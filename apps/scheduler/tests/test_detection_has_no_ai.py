"""No AI in the code that judges (F6 AC27; F19 FR33, AC47, AC96).

No `nexovue_scheduler.detection` module may import an AI module, directly or through any
module of this repository it imports. Nor may F19's three roots,
`nexovue_scheduler.slos`, `nexovue_platform.slo` and `nexovue_api.slos`, with every
subfolder walked, and with no exception in the walk: every API route's authorization
reaches `nexovue_api.lifespan`, which types the MCP server with a small Protocol rather
than importing the SDK (D55). F19's packages also import no network client themselves.

The walk, and what counts as an AI module, are `nexovue_platform.import_walk`'s.
"""

from __future__ import annotations

from pathlib import Path

from nexovue_platform.import_walk import (
    F3_AI_MODULES,
    NETWORK_CLIENTS,
    REPOSITORY_AI_MODULES,
    ai_imports_reachable_from,
    denied,
    direct_imports_matching,
    imports_of,
    module_file,
    package_modules,
)

DETECTION = "nexovue_scheduler.detection"
# F3 (AC5, plan T1.8): the evidence job, which gathers facts for every finding, AI on or off.
EVIDENCE = "nexovue_scheduler.evidence"
EVIDENCE_CONTRACT = "nexovue_platform.evidence"
# F19's roots (C4): the scheduler's jobs, the shared arithmetic, and the API's routes.
SLO_ROOTS = ("nexovue_scheduler.slos", "nexovue_platform.slo", "nexovue_api.slos")


def _modules(package: str) -> list[str]:
    modules = package_modules(package) or ([package] if module_file(package) else [])
    assert modules, f"{package} has moved: this test must follow it"
    return modules


def test_no_detection_module_imports_an_ai_module_even_through_others() -> None:
    modules = _modules(DETECTION)
    assert f"{DETECTION}.method" in modules and f"{DETECTION}.project_job" in modules
    reached = ai_imports_reachable_from(modules)
    assert reached == [], "detection reaches AI code:\n" + "\n".join(reached)


def test_no_slo_module_imports_an_ai_module_even_through_others() -> None:
    """AC47: with Step 1's API routes in place, and no exception in the walk."""
    modules = [name for root in SLO_ROOTS for name in _modules(root)]
    assert "nexovue_api.slos.router" in modules and "nexovue_scheduler.slos.job" in modules
    reached = ai_imports_reachable_from(modules)
    assert reached == [], "reliability targets reach AI code:\n" + "\n".join(reached)


def test_no_slo_module_imports_a_network_client() -> None:
    """AC96's rule: the walk cannot see a provider reached by plain HTTP, so F19's own
    modules may not hold a client that could reach one."""
    modules = [name for root in SLO_ROOTS for name in _modules(root)]
    assert direct_imports_matching(modules, NETWORK_CLIENTS) == []


def test_the_walk_finds_the_ai_import_the_repository_does_have() -> None:
    """The control: the same walk, from the MCP server, must find its SDK. A walk that saw no
    imports at all would pass the tests above for the wrong reason."""
    for name in REPOSITORY_AI_MODULES:
        assert module_file(name) is not None, f"{name} has moved: update the deny-list"
    reached = ai_imports_reachable_from(["nexovue_api.assistant.server"])
    assert "mcp.server <- nexovue_api.assistant.server" in reached


def test_the_network_rule_sees_a_direct_client() -> None:
    """The network rule's control: the probes hold the libcurl client."""
    found = direct_imports_matching(["nexovue_scheduler.probes.http_check"], NETWORK_CLIENTS)
    assert any(line.startswith("pycurl") for line in found)


def test_relative_and_dynamic_imports_are_resolved(tmp_path: Path) -> None:
    source = tmp_path / "job.py"
    source.write_text(
        "from . import method\nfrom ..alerts import tasks\nimport importlib\n"
        "def later():\n    importlib.import_module('openai')\n"
    )
    found = imports_of("nexovue_scheduler.detection.job", source)
    assert {
        "nexovue_scheduler.detection.method",
        "nexovue_scheduler.alerts.tasks",
        "openai",
    } <= found


def test_no_evidence_module_imports_an_ai_module_even_through_others() -> None:
    """F3 AC5: the evidence job and its contract reach no AI code, F3's own AI packages
    included before they exist, so gather decides every wording state without them."""
    modules = [*_modules(EVIDENCE), *_modules(EVIDENCE_CONTRACT)]
    assert f"{EVIDENCE}.gather" in modules and f"{EVIDENCE_CONTRACT}.model_facts" in modules
    reached = ai_imports_reachable_from(modules)
    assert reached == [], "the evidence job reaches AI code:\n" + "\n".join(reached)


def test_f3_s_ai_packages_are_denied_before_they_exist() -> None:
    """F3 T1.8: `nexovue_platform.ai` and `nexovue_scheduler.insight` are on the list now,
    so the first import of either, or of anything inside them, is caught by the walk."""
    assert {"nexovue_platform.ai", "nexovue_scheduler.insight"} <= F3_AI_MODULES
    for name in (
        "nexovue_platform.ai",
        "nexovue_platform.ai.validate",
        "nexovue_scheduler.insight.word",
    ):
        assert denied(name), name
    assert not denied("nexovue_platform.evidence")


def test_no_evidence_module_imports_the_probes_http_client() -> None:
    """F3 AC5: no evidence module may import `probes.http_check`, nor a network client."""
    modules = _modules(EVIDENCE)
    imported = {name for module in modules for name in imports_of(module, module_file(module))}  # type: ignore[arg-type]
    assert not any(name.startswith("nexovue_scheduler.probes") for name in imported)
    assert direct_imports_matching(modules, NETWORK_CLIENTS) == []
