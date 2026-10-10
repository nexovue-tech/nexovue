"""What the API and the scheduler must agree on to erase a deleted organization.

The API deletes the rows and marks the organization; the scheduler erases its
telemetry and files at night and writes the certificate (ADR-014). Both read
these, so the object layout an upload writes is the one an erasure deletes, and
the certificate the scheduler writes is the one the API can find.
"""

from __future__ import annotations

from typing import Final

# Where uploads live in the object store: `<root>/<org_id>/...`.
SOURCE_MAPS_ROOT: Final = "source-maps"
CHECK_FILES_ROOT: Final = "synthetics"

# The tenantless `audit_log` entries that bracket a deletion.
DELETION_REQUESTED_ACTION: Final = "organization.deletion_requested"
ERASED_ACTION: Final = "organization.erased"


def tenant_prefixes(org_id: object) -> tuple[str, ...]:
    """Every object-store prefix one organization's uploads live under."""
    return tuple(f"{root}/{org_id}/" for root in (SOURCE_MAPS_ROOT, CHECK_FILES_ROOT))


def organization_target(org_id: object) -> str:
    """The `target` both entries carry."""
    return f"organization:{org_id}"
