"""Erasing one tenant's telemetry from ClickHouse.

Nothing calls this yet. P1-77 — account and organization deletion — is what
will, once it has somewhere durable to record the obligation.
"""

from nexovue_api.purge.exceptions import (
    InvalidTenantError,
    PurgeFailedError,
    PurgeUnfinishedError,
)
from nexovue_api.purge.service import MutationProgress, TenantPurge
from nexovue_api.purge.tables import EXCLUDED_TABLES, PURGED_TABLES

__all__ = [
    "EXCLUDED_TABLES",
    "PURGED_TABLES",
    "InvalidTenantError",
    "MutationProgress",
    "PurgeFailedError",
    "PurgeUnfinishedError",
    "TenantPurge",
]
