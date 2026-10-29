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
    # The suites exercise the hosted product — billing, the operator console; the
    # self-hosted install is test_deployment_mode.py's.
    "NEXOVUE_DEPLOYMENT": "hosted",
}

# What a deployed install must hold (readiness blocker 17): a suite that builds
# production settings for another reason passes these rather than a real key.
DEPLOYED_KEYS: Final[dict[str, str]] = {
    "CHECK_SECRET_KEY": "qPYHqC1y6hD0s6knf12GgLTcvJsKkPdzaDgvE8sAFA4=",
    "CREDENTIAL_ENCRYPTION_KEY": "1tru1EuwOqD39XZm1z-sb2mrRHEFmbD_vreXaw2YZE8=",
}

for name, value in TEST_ENVIRONMENT.items():
    os.environ.setdefault(name, value)
