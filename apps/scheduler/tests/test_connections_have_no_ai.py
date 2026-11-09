"""The process holding tracker keys runs no model, and loads only its own tasks
(F22 AC34, FD1, D80).

`scheduler-connections` opens repository access, so it must import no AI module,
directly or through anything it imports, starting from the modules it actually loads:
`TASK_SETS["connections"]`. A running child is checked too, from a forkserver chosen
for that set, so a module pulled in at run time and not in the walk is still caught,
and so is a heavy store client the narrow set exists to keep out.

**Its stated limit:** a provider reached by plain HTTP is not visible to an import
walk. The role's HTTP goes through the guarded client to the tracker hosts it names.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Final

from nexovue_platform.connections import (
    CONNECTIONS_INTERACTIVE_QUEUE,
    CONNECTIONS_QUEUE,
)
from nexovue_platform.import_walk import REPOSITORY_AI_MODULES, ai_imports_reachable_from

from nexovue_scheduler.__main__ import worker_queues
from nexovue_scheduler.jobs import registry

CONNECTIONS: Final = "connections"
# What the narrow role exists to keep out of the process holding repository access.
STORE_CLIENTS: Final = ("clickhouse_connect", "confluent_kafka", "boto3")
_TESTS: Final = Path(__file__).parent


def test_the_connections_set_is_the_connections_package_alone() -> None:
    modules = registry.TASK_SETS[CONNECTIONS]
    assert modules
    assert all(name.startswith("nexovue_scheduler.connections.") for name in modules)


def test_no_other_role_runs_a_connection_task() -> None:
    """A task runs only where its keys are: `full` never registers a connection task."""
    assert not set(registry.TASK_SETS["all"]) & set(registry.TASK_SETS[CONNECTIONS])


def test_every_task_set_has_a_preload_of_its_own() -> None:
    assert set(registry.PRELOAD_OF) == set(registry.TASK_SETS)


def test_nothing_the_connections_tasks_load_reaches_an_ai_module() -> None:
    modules = [*registry.TASK_SETS[CONNECTIONS], registry.PRELOAD_OF[CONNECTIONS]]
    reached = ai_imports_reachable_from(modules)
    assert reached == [], "the connections role reaches AI code:\n" + "\n".join(reached)


def test_the_walk_still_sees_an_ai_import() -> None:
    """The control: a walk that saw no imports would pass the test above for nothing."""
    assert REPOSITORY_AI_MODULES
    reached = ai_imports_reachable_from(["nexovue_api.assistant.server"])
    assert any(line.startswith("mcp") for line in reached)


def test_the_role_names_its_two_queues_and_no_other() -> None:
    from nexovue_api.roles import command_for

    argv = command_for("scheduler-connections", [])
    queues = worker_queues(argv[argv.index("--queues") + 1], region="local")
    assert queues == [CONNECTIONS_QUEUE, CONNECTIONS_INTERACTIVE_QUEUE]
    assert argv[argv.index("--tasks") + 1] == CONNECTIONS


def test_a_running_child_holds_the_set_and_no_store_client() -> None:
    """A fresh process, because a forkserver keeps the preload it started with: in this
    test process another suite may already have started one for `all`."""
    probe = textwrap.dedent(
        """
        import json, sys
        from nexovue_scheduler.jobs import worker
        from nexovue_scheduler.jobs.queue import ClaimedJob
        modules = worker.use_task_set("connections") + ("job_fixtures",)
        slot = worker.Slot(modules)
        try:
            import asyncio, uuid
            job = ClaimedJob(
                id=uuid.uuid4(), task="jobs-test.loaded", kwargs={}, attempts=1,
                max_attempts=1, soft_limit_seconds=20, hard_limit_seconds=30,
            )
            print(json.dumps(asyncio.run(slot.run(job))))
        finally:
            slot.stop()
        """
    )
    environment = {
        **os.environ,
        "ENV": "development",
        "POSTGRES_DSN": "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        "SCHEDULER_ROLE": CONNECTIONS,
        "PYTHONPATH": f"{_TESTS}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }
    environment.pop("KAFKA_BROKERS", None)

    finished = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    outcome = json.loads(finished.stdout.strip().splitlines()[-1])
    assert outcome[0] == "done", outcome
    record = json.loads(Path(outcome_path()).read_text())
    loaded = set(record["modules"])
    # The child came from the connections preload: it inherited that import frozen.
    assert record["frozen"] > 0
    assert set(registry.TASK_SETS[CONNECTIONS]) <= loaded
    assert not [name for name in loaded if name.split(".")[0] in STORE_CLIENTS]
    assert not [name for name in loaded if name.startswith("nexovue_scheduler.detection")]
    assert not [
        name
        for name in loaded
        if any(name == ai or name.startswith(f"{ai}.") for ai in REPOSITORY_AI_MODULES)
    ]


def outcome_path() -> str:
    """Where `jobs-test.loaded` writes the child's `sys.modules`."""
    return str(Path(tempfile.gettempdir()) / "nexovue-connections-child-modules.json")
