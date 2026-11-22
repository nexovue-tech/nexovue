"""The tracker role's Postgres account, for the suites that act as it (F22 plan T8.7).

The development password is the one `just migrate-pg` and the dev Compose stack give
`nexovue_connections`; the suites set it again through the real `setup` step, so they hold
however the database was prepared. Never a production value.
"""

from __future__ import annotations

import os
from typing import Final

from insight_login import as_account

CONNECTIONS_ACCOUNT: Final = "nexovue_connections"
DEV_CONNECTIONS_PASSWORD: Final = os.environ.get(
    "NEXOVUE_CONNECTIONS_POSTGRES_PASSWORD", "nexovue_connections_dev"
)


def connections_dsn(install_dsn: str) -> str:
    """The tracker account's DSN on the install's server and database."""
    return as_account(install_dsn, CONNECTIONS_ACCOUNT, DEV_CONNECTIONS_PASSWORD)
