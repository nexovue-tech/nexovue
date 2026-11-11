"""The AI role's Postgres account, for the suites that act as it (F3 plan Step 3).

The development password is the one `just migrate-pg` and the dev Compose stack give
`nexovue_insight`; these suites set it again through the real `setup` step, so they hold
however the database was prepared. Never a production value.
"""

from __future__ import annotations

import os
from typing import Final
from urllib.parse import urlsplit, urlunsplit

INSIGHT_ACCOUNT: Final = "nexovue_insight"
DEV_INSIGHT_PASSWORD: Final = os.environ.get(
    "NEXOVUE_INSIGHT_POSTGRES_PASSWORD", "nexovue_insight_dev"
)


def as_account(dsn: str, account: str, password: str) -> str:
    """`dsn` with its user and password replaced: the same server and database."""
    parts = urlsplit(dsn)
    host = parts.hostname or "localhost"
    netloc = f"{account}:{password}@{host}{f':{parts.port}' if parts.port else ''}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def insight_dsn(install_dsn: str) -> str:
    """The AI account's DSN on the install's server and database."""
    return as_account(install_dsn, INSIGHT_ACCOUNT, DEV_INSIGHT_PASSWORD)
