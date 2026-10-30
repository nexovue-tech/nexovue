"""What the assistant server offers, and what it cannot do (F36, AC34, AC35).

No stack needed: this reads the registered tools and the bounding code. What it
proves is the pair of promises the MCP surface rests on — every tool is a read,
and no answer can grow without limit.

The read-only claim is checked structurally rather than by calling each tool: a
tool's *name* is a promise to a model, and what would break it is somebody
wiring a service function that writes. So this asserts the tool set against a
checked-in list, and asserts that each one calls only read-side service
functions, the way `test_read_surface_structure.py` asserts the HTTP surface.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Final

import pytest
from environment import TEST_ENVIRONMENT

from nexovue_api import assistant
from nexovue_api.assistant.bounds import MAX_CHARACTERS, MAX_ROWS, NARROW_BY, bounded
from nexovue_api.assistant.server import MCP_PATH, SERVER_INSTRUCTIONS, allowed_hosts
from nexovue_api.settings import Settings

TOOLS_SOURCE: Final = Path(assistant.__file__).with_name("tools.py")

# Every tool the server offers. Each is a read of the documented read surface
# (FR32); adding one is a change to this list in the same review.
THE_TOOLS: Final = {
    "list_projects",
    "list_services",
    "get_service",
    "search_logs",
    "list_error_groups",
    "list_incidents",
    "list_checks",
    "list_deploys",
    "list_traces",
}

# Service functions a tool may call. Every one is what a `GET` route calls; a
# tool reaching anything else is the defect this list exists to catch.
READS: Final = {
    "list_for_organization",  # projects
    "list_services",
    "get_service",
    "search_logs",
    "list_error_groups",
    "list_incidents",
    "list_for_project",  # checks, deploys
    "list_traces",
    "require_readable_project",
    # A filter object a read takes, not a call that does anything.
    "LogFilters",
    # The module's own helpers.
    "bounded",
    "window",
    "opened",
    "reader_of",
}


def _tool_functions() -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(TOOLS_SOURCE.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(ast.unparse(decorator) == "tool" for decorator in node.decorator_list)
    }


def test_the_server_offers_exactly_the_documented_tools() -> None:
    assert set(_tool_functions()) == THE_TOOLS


@pytest.mark.parametrize("name", sorted(THE_TOOLS))
def test_every_tool_is_a_read(name: str) -> None:
    """AC34. A tool that called a write would be a tool that could change a
    customer's configuration from a model's guess."""
    called = {
        node.func.attr
        for node in ast.walk(_tool_functions()[name])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    writes = called - READS - {"model_dump", "session_factory", "request_context"}
    assert not writes, f"{name} calls {sorted(writes)}, which is not on the read list"


@pytest.mark.parametrize("name", sorted(THE_TOOLS))
def test_every_tool_describes_itself_to_a_model(name: str) -> None:
    """The description is the whole of what a model has to choose by (FR32)."""
    described = ast.get_docstring(_tool_functions()[name])
    assert described and len(described) > 40, f"{name} has no usable description"


def test_a_short_answer_is_returned_whole() -> None:
    rows = [{"service": "checkout", "errors": 3}]

    answer = json.loads(bounded(rows, total=1, subject="services"))

    assert answer == {"services": rows}
    assert "cut" not in answer


def test_too_many_rows_are_cut_and_the_answer_says_so() -> None:
    """AC35: a silently truncated answer is one a model reasons from as whole."""
    rows = [{"service": f"service-{index}"} for index in range(MAX_ROWS * 3)]

    answer = json.loads(bounded(rows, total=len(rows), subject="services"))

    assert len(answer["services"]) == MAX_ROWS
    assert str(len(rows)) in answer["cut"]
    assert NARROW_BY in answer["cut"]


def test_a_few_enormous_rows_are_cut_by_size() -> None:
    """The two caps are a pair: ten rows of a megabyte each is not a small answer."""
    rows = [{"body": "x" * 20_000} for _ in range(10)]

    rendered = bounded(rows, total=len(rows), subject="logs")

    assert len(rendered) <= MAX_CHARACTERS + len(NARROW_BY) + 200
    answer = json.loads(rendered)
    assert 0 < len(answer["logs"]) < len(rows)
    assert "cut" in answer


def test_a_cut_answer_is_still_valid_json() -> None:
    """Cut by dropping rows, never by truncating text: a model cannot parse half
    a token, and half an answer that looks whole is worse than a short one."""
    rows = [{"body": "y" * 5_000} for _ in range(50)]

    json.loads(bounded(rows, subject="logs"))  # raises if it is not


def test_the_instructions_say_it_reads_and_nothing_else() -> None:
    assert "read-only" in SERVER_INSTRUCTIONS.lower()
    assert MCP_PATH == "/api/mcp"


def test_transport_security_names_the_api_host_not_localhost() -> None:
    """Without this the SDK arms a localhost-only allowlist and answers 421 to
    every request behind a real hostname, before any of this code runs.

    The API's own host, not the console's: this endpoint is served on the API
    hostname, and `app_base_url` is deliberately the other one of the pair.
    """
    settings = Settings(
        **{
            **TEST_ENVIRONMENT,
            "APP_BASE_URL": "https://app.nexovue.com",
            "API_PUBLIC_URL": "https://api.nexovue.com",
        }  # type: ignore[arg-type]
    )

    security = allowed_hosts(settings)

    assert security.enable_dns_rebinding_protection
    # The console's host is kept too: one deployment may serve both from one name.
    assert {"api.nexovue.com", "api.nexovue.com:*", "app.nexovue.com"} <= set(
        security.allowed_hosts
    )
    assert "https://api.nexovue.com" in set(security.allowed_origins)


def test_development_answers_on_localhost_with_neither_url_set() -> None:
    security = allowed_hosts(Settings(**{**TEST_ENVIRONMENT, "APP_BASE_URL": ""}))  # type: ignore[arg-type]

    assert "localhost" in security.allowed_hosts
    assert "localhost:*" in security.allowed_hosts
