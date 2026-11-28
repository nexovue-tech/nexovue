"""Write access stays off in code until the founder's recorded go (F5 plan T6.5, Steps 7 and
8; foundation item 33; ADR-097).

The go and `status.md` move together: a go set in code with the status still reading
"not given", or the reverse, fails here, so neither can change alone. And **no code path can
turn it on but the recorded go** (Step 7): read from the product's own sources, nothing builds
a `WriteGo`, nothing but `write_gate.py` assigns the constant, every `go` the product passes
is that constant read at call time, no function defaults one, the gate reads no setting,
environment or store, and every write path asks it. Step 8 adds that each write entry point
(Run CI's click and job among them) asks it before anything that could reach GitHub or add a
job, that only the writer builds the write routes, and that the pull request poller only reads.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from nexovue_platform.fixes import write_gate
from nexovue_platform.fixes.write_gate import WRITE_ACCESS_GO, WriteGo, write_access_on

STATUS = Path(__file__).resolve().parents[3] / "docs" / "features" / "ai-solution" / "status.md"
NOT_GIVEN = "**T6.5, the founder's go on write access: not given.**"


def _go(**overrides: Any) -> WriteGo:
    values: dict[str, Any] = {
        "given_on": date(2027, 1, 4),
        "safety_gates_passed_on": ("A named option",),
        "buyers_answer": "status.md, the buyer round",
        "terms": "docs/legal/no-warranty.md",
        "drafts_from_stack_alone": False,
        "enterprise_server_before_step_7": False,
    }
    return WriteGo(**(values | overrides))


def test_write_access_is_on_by_the_founders_recorded_go() -> None:
    """T6.5, given 2026-11-28: AC21 passed on Claude Sonnet 5.5
    (`docs/research/f5-evaluation-2026-11.md`); the founder's decisions of 2026-11-27 stand in
    for the buyer round; drafts from a stack alone and GitHub Enterprise Server stay no."""
    assert WRITE_ACCESS_GO is not None
    assert WRITE_ACCESS_GO.safety_gates_passed_on == ("Claude Sonnet 5.5",)
    assert WRITE_ACCESS_GO.terms == "docs/legal/suggested-fixes.md"
    assert (ROOT / WRITE_ACCESS_GO.terms).is_file()
    assert WRITE_ACCESS_GO.drafts_from_stack_alone is False
    assert WRITE_ACCESS_GO.enterprise_server_before_step_7 is False
    assert write_access_on() is True
    assert write_access_on(None) is False


def test_a_recorded_go_turns_it_on() -> None:
    assert write_access_on(_go()) is True


@pytest.mark.parametrize(
    "missing",
    [
        {"safety_gates_passed_on": ()},
        {"safety_gates_passed_on": ("  ",)},
        {"buyers_answer": " "},
        {"terms": ""},
    ],
    ids=["no_option", "blank_option", "no_buyers_answer", "no_terms"],
)
def test_a_go_cannot_be_written_without_what_t6_5_requires(missing: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="a go names"):
        _go(**missing)


def test_status_records_the_gate_as_the_code_holds_it() -> None:
    recorded_not_given = NOT_GIVEN in STATUS.read_text(encoding="utf-8")

    assert recorded_not_given == (write_gate.WRITE_ACCESS_GO is None), (
        "write_gate.WRITE_ACCESS_GO and status.md's T6.5 line must change together"
    )


# ------------------------------------------- no path turns it on but the recorded go

ROOT = Path(__file__).resolve().parents[3]
PRODUCT_SOURCES = sorted(
    path
    for tree in ("apps/api/src", "apps/scheduler/src", "packages/py-platform/src")
    for path in (ROOT / tree).rglob("*.py")
)
GATE = Path(write_gate.__file__).resolve()


def _trees() -> list[tuple[Path, ast.Module]]:
    return [(path, ast.parse(path.read_text(encoding="utf-8"))) for path in PRODUCT_SOURCES]


def _name(node: ast.AST) -> str:
    return ast.unparse(node)


def test_the_product_never_builds_a_go() -> None:
    """Only a reviewed commit to `write_gate.py` can write the founder's go: it is the one
    file that may build one."""
    built = [
        f"{path.relative_to(ROOT)}:{node.lineno}"
        for path, tree in _trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _name(node.func).split(".")[-1] == "WriteGo"
        and path.resolve() != GATE
    ]
    assert built == []


def test_nothing_but_the_gate_assigns_the_go() -> None:
    assigned = [
        f"{path.relative_to(ROOT)}:{node.lineno}"
        for path, tree in _trees()
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign)
            and "WRITE_ACCESS_GO" in _name(node).split("=")[0]
            and path.resolve() != GATE
        )
        or (
            isinstance(node, ast.Call)
            and _name(node.func) in {"setattr", "monkeypatch.setattr"}
            and "WRITE_ACCESS_GO" in _name(node)
        )
    ]
    assert assigned == []


def test_every_go_the_product_passes_is_the_recorded_one() -> None:
    """A `go=` argument is the module's constant read at call time, or a function's own
    `go` handed on; `write_access_on` is called with nothing, or with that `go`."""
    # `context.go` is `DraftContext.go`, built from the read's own `go` (fix_service).
    allowed = {"write_gate.WRITE_ACCESS_GO", "go", "self._go", "context.go"}
    passed = [
        (f"{path.relative_to(ROOT)}:{node.lineno}", _name(keyword.value))
        for path, tree in _trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "go" and _name(keyword.value) not in allowed
    ]
    asked = [
        (f"{path.relative_to(ROOT)}:{node.lineno}", _name(node))
        for path, tree in _trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _name(node.func).endswith("write_access_on")
        and not all(_name(arg) in allowed for arg in node.args)
    ]
    assert passed == [] and asked == []


def test_a_function_that_takes_a_go_has_no_default_for_it() -> None:
    """So no caller can forget it and quietly get some other answer."""
    defaulted = []
    for path, tree in _trees():
        if path.resolve() == GATE:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            arguments = node.args
            keyword_defaults = dict(
                zip((a.arg for a in arguments.kwonlyargs), arguments.kw_defaults, strict=True)
            )
            positional = arguments.posonlyargs + arguments.args
            positional_defaults = dict(
                zip(
                    (a.arg for a in positional[len(positional) - len(arguments.defaults) :]),
                    arguments.defaults,
                    strict=True,
                )
            )
            if keyword_defaults.get("go") is not None or "go" in positional_defaults:
                defaulted.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert defaulted == []


def test_the_gate_reads_no_setting_environment_or_store() -> None:
    """ADR-097: no setting, environment variable or row can turn it on."""
    tree = ast.parse(GATE.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported <= {"__future__", "dataclasses", "datetime", "typing"}
    source = GATE.read_text(encoding="utf-8")
    assert "os.environ" not in source and "getenv" not in source


def test_every_write_path_asks_the_gate() -> None:
    """Steps 7's and 8's write paths each ask `write_access_on` themselves (ADR-097)."""
    writers = {
        "apps/api/src/nexovue_api/findings/fix_draft.py",
        "apps/api/src/nexovue_api/findings/fix_checks.py",
        "apps/scheduler/src/nexovue_scheduler/connections/fix_writer.py",
        "apps/scheduler/src/nexovue_scheduler/connections/fix_check.py",
        "apps/scheduler/src/nexovue_scheduler/connections/fix_github.py",
    }
    for relative in writers:
        assert "write_access_on(" in (ROOT / relative).read_text(encoding="utf-8"), relative


# Every entry point of a write path, by module and function: the draft click, Delete this
# branch and Run CI in the API; the draft job, the branch deletion and Run CI's job in the
# scheduler (Steps 7 and 8).
WRITE_ENTRY_POINTS = {
    "apps/api/src/nexovue_api/findings/fix_draft.py": {"open_draft", "delete_branch", "request_ci"},
    "apps/scheduler/src/nexovue_scheduler/connections/fix_writer.py": {
        "open_pull_request",
        "delete_fix_branch",
        "run_ci",
    },
}
# What reaches GitHub, or hands work to something that will: the Fix App's client, its write
# routes, the row a job writes from, a queued job.
_REACHES_OUT = ("fix_client", "writes", "load_fix_row", "queue_suggestion", "FixWrites")
_ASKS = ("write_access_on", "_refuse_while_off")


def _function(path: Path, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    [found] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    ]
    return found


def _first_call(function: ast.AST, names: tuple[str, ...]) -> int | None:
    lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _name(node.func).split(".")[-1] in names
    ]
    return min(lines, default=None)


def test_every_write_entry_point_asks_the_gate_before_it_reaches_out() -> None:
    """Step 8: in each entry point the gate is asked, on a line before anything that could
    reach GitHub or add a job, so "off" holds before the first request (ADR-097, ADR-101)."""
    for relative, names in WRITE_ENTRY_POINTS.items():
        for name in names:
            function = _function(ROOT / relative, name)
            asked = _first_call(function, _ASKS)
            reached = _first_call(function, _REACHES_OUT)
            assert asked is not None, f"{relative}:{name} never asks the gate"
            assert reached is None or asked < reached, f"{relative}:{name}"


def test_every_go_taking_entry_point_is_listed() -> None:
    """A new write path in these modules joins the list above, so the check covers it."""
    for relative, names in WRITE_ENTRY_POINTS.items():
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        taking = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and not node.name.startswith("_")
            and "go" in {a.arg for a in node.args.kwonlyargs + node.args.args}
        }
        assert taking == names, relative


def test_only_the_writer_builds_the_write_routes() -> None:
    """`FixGitHub.writes(go)` is called only by the scheduler's writer: no other module,
    the pull request poller included, can reach a write route (Step 8)."""
    builders = sorted(
        str(path.relative_to(ROOT))
        for path, tree in _trees()
        if any(
            isinstance(node, ast.Call)
            and (_name(node.func).endswith(".writes") or _name(node.func) == "FixWrites")
            for node in ast.walk(tree)
        )
    )
    assert builders == [
        "apps/scheduler/src/nexovue_scheduler/connections/fix_github.py",
        "apps/scheduler/src/nexovue_scheduler/connections/fix_writer.py",
    ]


def _routes(name: str) -> tuple[tuple[str, str, str], ...]:
    """`fix_github.py`'s checked-in route list, read from its source: this package's tests
    import no app."""
    client = ROOT / "apps/scheduler/src/nexovue_scheduler/connections/fix_github.py"
    tree = ast.parse(client.read_text(encoding="utf-8"))
    [value] = [
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and _name(node.target) == name and node.value
    ]
    routes: tuple[tuple[str, str, str], ...] = ast.literal_eval(value)
    return routes


def test_the_pull_request_poller_only_reads() -> None:
    """Step 8: the poller follows a draft with read routes only, so it needs no go; it
    imports no write route and calls only methods on `fix_github.ROUTES`' read list."""
    poller = ROOT / "apps/scheduler/src/nexovue_scheduler/connections/fix_poller.py"
    tree = ast.parse(poller.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not imported & {"FixWrites", "WriteGo", "write_access_on", "WRITE_ACCESS_GO"}
    reads = {name for method, _, name in _routes("ROUTES") if method == "GET"}
    writes = {name for _, _, name in _routes("WRITE_ROUTES")}
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called & {"pull", "pulls_for"} == {"pull", "pulls_for"}
    assert not called & writes and not called & {"writes", "token"}
    assert called & (reads | writes) <= reads
