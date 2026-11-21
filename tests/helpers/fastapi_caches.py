"""FastAPI's dependency caches, emptied after every test.

`fastapi.dependencies.models` classifies each dependency callable (generator, async
generator, coroutine) through three module-level `lru_cache`s of 4096 entries, keyed by
the callable itself, so every callable it has resolved stays alive until evicted. An app
built once has a fixed set of them. The tests build an app per request and override its
dependencies with closures over the test's session, so each kept closure kept that
session, its connection, its engine and the engine's compiled statements. Measured
2026-11-21 (docs/changes/test-workers-hold-memory/): ten finished tests' engines alive
after a 13-test file, and a worker's live objects growing from 0.47 to 1.65 million over
eight API suites.

Emptying the caches between tests costs one classification per callable per test.
Every `lru_cache` in the module is emptied, so a cache FastAPI adds later is covered.

Registered for every suite with `-p fastapi_caches` (pyproject.toml).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from functools import _lru_cache_wrapper
from typing import Final

import pytest

DEPENDENCY_MODULE: Final = "fastapi.dependencies.models"


def clear_dependency_caches() -> None:
    """Empty FastAPI's dependency caches, if a test has imported FastAPI."""
    module = sys.modules.get(DEPENDENCY_MODULE)
    if module is None:
        return
    for value in vars(module).values():
        if isinstance(value, _lru_cache_wrapper):
            value.cache_clear()


@pytest.fixture(autouse=True)
def _forget_dependency_callables() -> Iterator[None]:
    yield
    clear_dependency_caches()
