"""Where the control-plane connection string comes from.

Alembic's `env.py` and the seed script both run outside the FastAPI app, so
neither can take the DSN from a request-scoped dependency. This is the one
place that reads it.
"""

import os
from typing import Final

# The same variable the API itself reads, so a developer sets one thing.
DSN_ENV_VAR: Final = "POSTGRES_DSN"

# Matches the development target in deploy/compose/docker-compose.yaml, so a
# fresh clone migrates and seeds with no environment set up.
DEFAULT_DSN: Final = "postgresql+asyncpg://nexovue:nexovue_dev@localhost:5432/nexovue"


def postgres_dsn() -> str:
    """The async SQLAlchemy URL for the control-plane database."""
    return os.environ.get(DSN_ENV_VAR, DEFAULT_DSN)
