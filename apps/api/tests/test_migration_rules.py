"""Rules every Alembic migration is held to (group 9, C6).

- **A lock timeout.** A migration waiting on a lock behind a long transaction
  holds its own lock request at the head of the queue, and every write to that
  table waits behind it — a write outage that looks like a hang. With
  `lock_timeout` it fails instead, and can be run again.
- **The billing downgrades that would drop billing records refuse.** 0020, 0022
  and 0026 drop subscriptions, the committed ceiling and the charged amounts; a
  downgrade past them is a restore, never `alembic downgrade`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

VERSIONS: Final = Path(__file__).resolve().parents[1] / "migrations" / "versions"
ENV: Final = VERSIONS.parent / "env.py"
ROLL_FORWARD_ONLY: Final = ("0020", "0022", "0026")


def _revision(number: str) -> ModuleType:
    [path] = VERSIONS.glob(f"{number}_*.py")
    spec = importlib.util.spec_from_file_location(f"revision_{number}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_migration_runs_with_a_lock_timeout() -> None:
    source = ENV.read_text(encoding="utf-8")

    assert "SET lock_timeout" in source


@pytest.mark.parametrize("number", ROLL_FORWARD_ONLY)
def test_a_downgrade_that_drops_billing_records_refuses(number: str) -> None:
    with pytest.raises(RuntimeError, match="roll forward only"):
        _revision(number).downgrade()
