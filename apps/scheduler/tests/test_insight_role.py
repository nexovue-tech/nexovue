"""The AI role loads only its own tasks and reaches the queue only through its functions
(F3 plan T3.6, design *The role*; FD1, FD10).

`scheduler-insight` opens provider keys, so its forkserver must hold no store client: a
running child is read, from a forkserver chosen for its set, as F22's suite does for
`scheduler-connections`. And its account has no right on `jobs`, so its worker must take
the function statements, and refuse a queue those cannot claim.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import typing
from pathlib import Path
from typing import Final

import pytest
from nexovue_platform.evidence import INSIGHT_QUEUE
from nexovue_platform.import_walk import ai_imports_reachable_from
from sqlalchemy.ext.asyncio import create_async_engine

from nexovue_scheduler import __main__ as scheduler_main
from nexovue_scheduler.jobs import queue, registry, worker
from nexovue_scheduler.settings import INSIGHT_ROLE, SchedulerRole, get_settings

STORE_CLIENTS: Final = ("clickhouse_connect", "confluent_kafka", "boto3")
_TESTS: Final = Path(__file__).parent


def test_the_insight_set_is_the_insight_package_alone() -> None:
    modules = registry.TASK_SETS[INSIGHT_ROLE]
    assert modules
    assert all(name.startswith("nexovue_scheduler.insight") for name in modules)
    assert registry.PRELOAD_OF[INSIGHT_ROLE] == "nexovue_scheduler.jobs.preload_insight"


def test_no_other_set_loads_an_insight_task() -> None:
    """A task runs only where its keys are: `all` and `connections` never load AI tasks."""
    for name, modules in registry.TASK_SETS.items():
        if name != INSIGHT_ROLE:
            assert not [module for module in modules if ".insight" in module], name


def test_what_the_other_roles_load_reaches_no_ai_module() -> None:
    """The control for the opposite direction: the AI package exists now, and nothing the
    probe, alert or detection workers load may import it."""
    modules = [*registry.TASK_SETS["all"], registry.PRELOAD_OF["all"]]
    reached = [line for line in ai_imports_reachable_from(modules) if "insight" in line]
    assert reached == [], "\n".join(reached)


def test_every_role_has_its_queue_statements() -> None:
    assert set(queue.STATEMENTS_OF_ROLE) == set(typing.get_args(SchedulerRole))
    assert queue.STATEMENTS_OF_ROLE[INSIGHT_ROLE] is queue.INSIGHT_STATEMENTS
    assert queue.STATEMENTS_OF_ROLE["full"] is queue.SHARED_STATEMENTS


def test_the_insight_statements_call_the_functions_and_name_no_table() -> None:
    statements = queue.INSIGHT_STATEMENTS
    texts = [
        str(statement) for statement in (statements.claim, statements.retry, statements.finish)
    ]
    assert [text.split("(")[0].split()[-1] for text in texts] == [
        "insight_claim_jobs",
        "insight_retry_job",
        "insight_finish_job",
    ]
    assert not [text for text in texts if "jobs " in text.replace("_jobs(", "(")]
    assert statements.queues == frozenset({INSIGHT_QUEUE})


async def test_an_insight_worker_refuses_a_queue_its_functions_cannot_claim() -> None:
    engine = create_async_engine("postgresql+asyncpg://unused:unused@127.0.0.1:1/unused")
    try:
        with pytest.raises(ValueError, match="claim only"):
            worker.Worker(
                engine,
                listen_dsn="postgresql://unused@127.0.0.1:1/unused",
                queues=["light"],
                concurrency=1,
                statements=queue.INSIGHT_STATEMENTS,
            )
    finally:
        await engine.dispose()


def test_the_insight_child_keeps_less_than_a_full_worker_s() -> None:
    """Four children fit `scheduler-insight`'s 384 MiB beside the parent (design, *Memory*)."""
    assert worker.child_memory_limit_of(INSIGHT_ROLE) == 48 * 2**20
    assert worker.child_memory_limit_of("all") == worker.MAX_CHILD_PRIVATE_BYTES


@pytest.mark.parametrize(
    ("role", "tasks"), [("full", "insight"), ("insight", "all"), ("connections", "insight")]
)
def test_the_insight_tasks_run_only_as_the_insight_role(
    monkeypatch: pytest.MonkeyPatch, role: str, tasks: str
) -> None:
    """Its tasks need its key, and its account can claim nothing else: a mismatch stops the
    process before any slot starts."""
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused")
    monkeypatch.setenv("KAFKA_BROKERS", "localhost:1")
    monkeypatch.setenv("SCHEDULER_ROLE", role)
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit, match="SCHEDULER_ROLE=insight"):
            scheduler_main.main(["worker", "--queues", "insight", "--tasks", tasks])
    finally:
        get_settings.cache_clear()


def test_a_running_insight_child_holds_its_set_and_no_store_client(tmp_path: Path) -> None:
    """A fresh process, because a forkserver keeps the preload it started with: in this
    test process another suite may already have started one for `all`."""
    record_path = tmp_path / "insight-child-modules.json"
    probe = textwrap.dedent(
        f"""
        import json, sys
        from nexovue_scheduler.jobs import worker
        from nexovue_scheduler.jobs.queue import ClaimedJob
        modules = worker.use_task_set("insight") + ("job_fixtures",)
        slot = worker.Slot(modules)
        try:
            import asyncio, uuid
            job = ClaimedJob(
                id=uuid.uuid4(), task="jobs-test.loaded", kwargs={{"path": {str(record_path)!r}}},
                attempts=1, max_attempts=1, soft_limit_seconds=20, hard_limit_seconds=30,
            )
            outcome = asyncio.run(slot.run(job))
            print(json.dumps([*outcome, worker.private_bytes(slot._process.pid)]))
        finally:
            slot.stop()
        """
    )
    environment = {
        **os.environ,
        "ENV": "development",
        "POSTGRES_DSN": "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        "SCHEDULER_ROLE": INSIGHT_ROLE,
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
    record = json.loads(record_path.read_text())
    loaded = set(record["modules"])
    # The child came from the insight preload: it inherited that import frozen.
    assert record["frozen"] > 0
    assert set(registry.TASK_SETS[INSIGHT_ROLE]) <= loaded
    assert not [name for name in loaded if name.split(".")[0] in STORE_CLIENTS]
    assert not [name for name in loaded if name.startswith("nexovue_scheduler.detection")]
    assert not [name for name in loaded if name.startswith("nexovue_scheduler.evidence")]
    # NFR3: the child's own memory after a job, beyond what it shares with its forkserver,
    # is well under the role's per-child limit, so four fit its 384 MiB.
    own = outcome[-1]
    assert own is not None
    assert own < worker.INSIGHT_CHILD_PRIVATE_BYTES, f"{own / 2**20:.1f} MiB of its own"
