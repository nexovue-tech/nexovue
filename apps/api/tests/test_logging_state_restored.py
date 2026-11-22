"""A test that configures logging in-process leaves none of it to the tests after it.

Alembic's `env.py` calls `logging.config.fileConfig` on `alembic.ini`, as the CLI should,
so `setup` or a migration run in-process replaces the root logger's handlers with one
writing to that moment's `sys.stderr` (under capture, the test's own stream, closed when
the test ends) and disables every logger that already existed. Every later test in the
same worker then logged into a closed stream, or not at all.

The guard runs two tests in order in a pytest of its own, under this repository's own
configuration, so whatever plugin the repository registers is what is tested: the first
configures logging as `env.py` does, the second must still log through open handlers and
enabled loggers.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

REPOSITORY: Final = Path(__file__).resolve().parents[3]
ALEMBIC_INI: Final = REPOSITORY / "apps" / "api" / "alembic.ini"
# The inner run starts a Python and imports pytest's plugins: well inside this.
INNER_RUN_SECONDS: Final = 120

_TESTS_IN_ORDER: Final = """
import logging
import logging.config

# A logger that exists before the migration runs, as every imported module's does.
EXISTING = logging.getLogger("nexovue.logging-guard")


def test_a_migration_configures_logging_as_env_py_does(capsys):
    logging.config.fileConfig({ini!r})


def test_a_later_test_logs_through_open_handlers_and_enabled_loggers(caplog):
    closed = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(getattr(handler, "stream", None), "closed", False)
    ]
    assert closed == [], "a handler still writes to an earlier test's closed stream"
    assert not EXISTING.disabled, "a logger that existed before the migration is disabled"
    with caplog.at_level(logging.WARNING):
        EXISTING.warning("still heard")
    assert caplog.messages == ["still heard"]
"""


def test_a_test_that_configures_logging_leaves_the_next_test_its_logging(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "test_in_order.py"
    tests.write_text(_TESTS_IN_ORDER.format(ini=str(ALEMBIC_INI)), encoding="utf-8")

    inner = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(REPOSITORY / "pyproject.toml"),
            "--rootdir",
            str(REPOSITORY),
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "-n",
            "0",
            "-q",
            str(tests),
        ],
        capture_output=True,
        text=True,
        timeout=INNER_RUN_SECONDS,
        check=False,
        cwd=REPOSITORY,
    )

    assert inner.returncode == 0, inner.stdout + inner.stderr
    assert "2 passed" in inner.stdout, inner.stdout
