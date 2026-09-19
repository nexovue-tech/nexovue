"""Profiles run in a worker process of their own (F17, NFR1, AC15).

Every pipeline of one worker shares its memory. Measured on 2026-09-19: a profile
flood from one organization held more decoded samples than the worker's 1 GiB cap,
the kernel killed the process, and spans, logs and metrics stopped with it — for
every tenant — until the flood had drained. A process of its own is what makes
"profiles cannot delay spans" true: a profile worker killed by a flood restarts
alone.

Read from the compose files, as `test_compose_dependencies.py` reads them: the
defect is in the declaration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
import yaml

import nexovue_api

PROFILES: Final = "profile-ingest"
DEPLOYMENTS: Final = (
    "docker-compose.yaml",
    "docker-compose.app.yaml",
    "docker-compose.onprem.yaml",
)


def _workers(file_name: str) -> dict[str, list[str]]:
    """Each service that sets WORKER_PIPELINES, and the pipelines it names."""
    path = Path(nexovue_api.__file__).parents[4] / "deploy" / "compose" / file_name
    services: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))["services"]
    workers: dict[str, list[str]] = {}
    for name, definition in services.items():
        raw = (definition.get("environment") or {}).get("WORKER_PIPELINES")
        if raw is None:
            continue
        text = str(raw)
        # Fixed, not overridable: an operator's `.env` still carrying the old `all`
        # would silently put profiles back in the span worker.
        assert "${" not in text, f"{file_name}: {name}'s WORKER_PIPELINES can be overridden"
        workers[name] = [pipeline.strip() for pipeline in text.split(",") if pipeline.strip()]
    return workers


@pytest.mark.parametrize("file_name", DEPLOYMENTS)
def test_profiles_are_ingested_by_a_worker_that_runs_nothing_else(file_name: str) -> None:
    workers = _workers(file_name)
    running = {name: pipelines for name, pipelines in workers.items() if PROFILES in pipelines}

    assert len(running) == 1, f"{file_name}: profile-ingest runs in {sorted(running)}"
    [(name, pipelines)] = running.items()
    assert pipelines == [PROFILES], f"{file_name}: {name} also runs {pipelines}"
    for other, others in workers.items():
        assert "all" not in others, (
            f"{file_name}: {other} runs `all`, which includes profile-ingest and puts "
            "profiles back in the same process as spans"
        )
