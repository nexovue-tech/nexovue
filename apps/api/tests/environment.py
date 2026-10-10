"""Test environment.

`main.py` builds a module-level `app` because that is what uvicorn is pointed
at, so importing it reads configuration. Importing this module first puts those
values in place. No test connects to any of them — the pools are replaced by
dependency overrides.

Not named `conftest.py`: another test package already has one, and two modules
of that name are ambiguous to mypy.
"""

from __future__ import annotations

import os
from typing import Final

TEST_ENVIRONMENT: Final[dict[str, str]] = {
    "ENV": "development",
    "LOG_LEVEL": "warn",
    "POSTGRES_DSN": "postgresql://nexovue:secret@localhost:5432/nexovue",
    "CLICKHOUSE_DSN": "clickhouse://nexovue_reader:secret@localhost:8123/nexovue",
    "CLICKHOUSE_WRITER_DSN": "clickhouse://nexovue:secret@localhost:8123/nexovue",
    "REDIS_URL": "redis://localhost:6379/0",
}

for name, value in TEST_ENVIRONMENT.items():
    os.environ.setdefault(name, value)
