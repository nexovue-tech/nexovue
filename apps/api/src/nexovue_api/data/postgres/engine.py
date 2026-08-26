"""Where the control-plane connection string comes from.

Alembic's `env.py` and the seed script both run outside the FastAPI app, so
neither can take the DSN from a request-scoped dependency. This is the one
place that reads it.
"""

import os
from typing import Final

# The same variable the API itself reads, so a developer sets one thing.
DSN_ENV_VAR: Final = "POSTGRES_DSN"


def postgres_dsn() -> str:
    """The async SQLAlchemy URL for the control-plane database.

    No default: the DSN carries a password, and a credential that falls back
    to something working locally is a credential that is wrong everywhere else.
    """
    dsn = os.environ.get(DSN_ENV_VAR)
    if not dsn:
        raise RuntimeError(
            f"{DSN_ENV_VAR} is not set. Copy .env.example, or run through "
            f"`just dev`, which supplies it."
        )
    return dsn
