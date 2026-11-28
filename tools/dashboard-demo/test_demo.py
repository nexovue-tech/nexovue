"""The demo's plan: seed first, fault second, and only the tools that refuse a non-local host."""

from __future__ import annotations

from demo import FAULT, FAULTED_SERVICE, SEED, main, plan


def test_history_comes_before_the_fault() -> None:
    steps = plan()
    assert steps[0][1] == str(SEED)
    assert steps[1][1] == str(FAULT)
    assert steps[1][-1] == FAULTED_SERVICE


def test_a_store_url_reaches_both_steps() -> None:
    steps = plan(["--clickhouse", "http://localhost:1/x"])
    assert all("http://localhost:1/x" in step for step in steps)


def test_the_tools_it_runs_refuse_other_hosts() -> None:
    for tool in (SEED, FAULT):
        assert "require_local" in tool.read_text(encoding="utf-8")


def test_printing_runs_nothing(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main([]) == 0
    assert "seed.py" in capsys.readouterr().out
