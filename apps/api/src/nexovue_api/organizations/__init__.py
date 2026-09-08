"""Account and organization deletion (P1-77).

Postgres and the object store are erased here; the telemetry store is erased by
`nexovue_api.purge`, which this is the caller of.
"""

from nexovue_api.organizations.service import ErasureOutcome, erase, erasures_still_owed
from nexovue_api.organizations.tables import (
    DELETED_DIRECTLY,
    EXCLUDED_TABLES,
    REMOVED_BY_CASCADE,
    TOMBSTONED_TABLE,
)

__all__ = [
    "DELETED_DIRECTLY",
    "EXCLUDED_TABLES",
    "REMOVED_BY_CASCADE",
    "TOMBSTONED_TABLE",
    "ErasureOutcome",
    "erase",
    "erasures_still_owed",
]
