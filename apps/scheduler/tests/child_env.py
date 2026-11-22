"""A worker's children read the environment their forkserver started with.

`multiprocessing`'s forkserver is one process per pytest process, started by the first child
anyone makes and never again, so a test's `monkeypatch.setenv` reaches a child only when that
test makes the process's first child. After an earlier test has started it, the child keeps the
earlier environment: under `-n 6`, `test_bed_live.py`'s children read a ClickHouse address that
was not the one it set, and its 100 gathers failed with a refused connection. A test that sets
what its children read stops the forkserver before and after, so the next child starts one that
sees it.
"""

from __future__ import annotations

import multiprocessing.forkserver as forkserver
from collections.abc import Iterator

import pytest


def stop_forkserver() -> None:
    """Stop this process's forkserver, if one is running; the next child starts a new one.

    `_stop` is private, but it is the only way to stop it: CPython's own tests call it for this."""
    forkserver._forkserver._stop()  # type: ignore[attr-defined]  # not in the typeshed stubs


@pytest.fixture
def children_read_this_tests_environment() -> Iterator[None]:
    stop_forkserver()
    yield
    stop_forkserver()
