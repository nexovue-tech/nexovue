"""`nexovue <role>` (ADR-019; ready-to-deploy, T3): each role names exactly the process it runs."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexovue_api import roles


def test_each_role_runs_the_process_the_compose_files_ran() -> None:
    assert roles.command_for("api", []) == [
        "uvicorn", "nexovue_api.main:app", "--host", "0.0.0.0", "--port", "8000",
    ]  # fmt: skip
    assert roles.command_for("scheduler-beat", []) == ["python", "-m", "nexovue_scheduler", "beat"]
    assert roles.command_for("scheduler-deliveries", [])[-4:] == [
        "--queues", "deliveries", "--concurrency", "4",
    ]  # fmt: skip
    assert roles.command_for("setup", []) == ["python", "-m", "nexovue_api.setup"]


def test_extra_arguments_reach_the_role() -> None:
    assert roles.command_for("scheduler-light", ["--concurrency", "2"])[-2:] == [
        "--concurrency",
        "2",
    ]


def test_an_unknown_role_is_refused_with_the_list() -> None:
    with pytest.raises(SystemExit, match="unknown role 'worker'"):
        roles.command_for("worker", [])


def test_the_console_replaces_whatever_the_edge_served_before(tmp_path: Path) -> None:
    built = tmp_path / "built"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text("new")
    (built / "assets" / "app-2.js").write_text("new")
    served = tmp_path / "served"
    (served / "assets").mkdir(parents=True)
    (served / "assets" / "app-1.js").write_text("a release ago")

    copied = roles.copy_console(served, source=built)

    assert copied == 2
    assert sorted(p.name for p in served.rglob("*") if p.is_file()) == ["app-2.js", "index.html"]


def test_the_backup_role_runs_the_backup_command() -> None:
    assert roles.command_for("backup", ["list"]) == ["python", "-m", "nexovue_api.backups", "list"]
