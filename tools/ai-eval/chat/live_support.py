"""What the chat bed's live tests share (F4 plan T4.1, T4.2): the API test environment the
application is first imported under, the stores guard, and a bed with its API served.

**The API's test environment first.** `nexovue_api.main` builds a module-level app when first
imported, from the environment of that moment, and the API's suites import
`apps/api/tests/environment.py` before it. A bed test in the same process loads that same
module, so whichever suite imports the app first, it is built the same way.

These suites fail rather than skip when the stores are absent (`tests/helpers/live`).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from live import require_stack
from nexovue_platform.checks.sealing import ConfiguredKeys, generate_key

from chat import harness, stores

REPOSITORY: Final = Path(__file__).resolve().parents[3]
_API_TEST_ENVIRONMENT: Final = REPOSITORY / "apps" / "api" / "tests" / "environment.py"


def load_api_test_environment() -> None:
    if "environment" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location("environment", _API_TEST_ENVIRONMENT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_API_TEST_ENVIRONMENT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["environment"] = module
    spec.loader.exec_module(module)


def require_stores(suite: str) -> stores.Stores:
    found = stores.Stores.from_environment()
    require_stack(
        reachable=found.reachable(),
        suite=suite,
        remedy="Postgres and ClickHouse are not reachable, and `setup` must have run",
    )
    return found


@asynccontextmanager
async def served_bed(found: stores.Stores) -> AsyncIterator[harness.Bed]:
    """A bed on the stores, its API served, every world it made removed after."""
    keys = ConfiguredKeys(generate_key(), "")
    with stores.serve(found, keys) as url:
        bed = await harness.open_bed(found, url, keys, stores.Connection())
        try:
            yield bed
        finally:
            await bed.close()
