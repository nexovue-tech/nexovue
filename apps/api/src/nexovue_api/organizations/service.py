"""Deleting an account: what happens now, and what happens at night.

**Two phases, because the stores differ.** The request is one Postgres
transaction and it is the whole customer-visible stop: the charges are stopped,
every row naming a person or a project is deleted, and `organizations.deleted_at`
is set. `load_identity` and the receiver's allowance gate both read that column,
so sessions and ingest are refused at once. Deleting the rows here rather than
later is what frees the owner's email (unique across nexovue) for a new signup
the same day.

The telemetry and the uploaded files are not erased here. Erasing a tenant from
ClickHouse is a mutation that rewrites most of every table (ADR-014), so the
scheduler erases every deleted organization together, once a night
(`nexovue_scheduler.erasure`).

**Where the obligation lives, without a table for it.** `organizations` with
`deleted_at` set *is* the record: the id the ClickHouse erasure and the object
prefixes are both named from. Completion is a tenantless `audit_log` entry, the
one row about this tenant nothing cascades away; the scheduler writes it.

**The organization row is scrubbed and kept, not deleted.** The receiver refuses
a deleted organization by reading `deleted_at` off this row; when no row answers,
its gate reads that as "cannot find" and *admits* — the bug `allowance/postgres.go`
records having fixed. The row also holds the id the nightly erasure needs, and
a deletion has to be provable afterwards. `slug` and `name` are replaced, so what
remains is an id, a plan and two timestamps.

**There is no undo.** The rows are gone when the request commits.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.billing import ConfiguredProcessor
from nexovue_api.billing import service as billing
from nexovue_api.data.postgres.models import Organization
from nexovue_api.errors.exceptions import ConflictError, NotFoundError
from nexovue_api.organizations import queries
from nexovue_api.organizations.schemas import DeletionAccepted
from nexovue_api.organizations.tables import DELETED_DIRECTLY

# What replaces the customer's own words on the tombstone. The slug stays unique
# and stays inside `SLUG_MAX_LENGTH`; more usefully, it frees the name the
# customer chose, so the same company can sign up again.
_SCRUBBED_SLUG_PREFIX: Final = "deleted-"
_SCRUBBED_NAME: Final = "(deleted organization)"

ALREADY_DELETED: Final = "This organization has already been deleted."


async def request_deletion(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    payments: ConfiguredProcessor | None,
) -> DeletionAccepted:
    """Close the account now, delete its rows, and leave its telemetry owed.

    One transaction: stopping the charges, the rows, the mark, the scrub and the
    audit entry. Nothing that can run for minutes belongs in the request a person
    is waiting on, which is why the telemetry is the night's.

    **The charges stop first** (readiness blocker 20). Closing used to delete the
    subscription rows and never tell the processor, which kept debiting with no
    record left to refund from. If the processor cannot be reached the closing
    is refused and nothing is scrubbed.
    """
    organization = await queries.lock_organization(session, org_id)
    if organization is None:
        raise NotFoundError("No such organization.")
    if organization.deleted_at is not None:
        raise ConflictError(ALREADY_DELETED)

    await billing.stop_every_charge(session, configured=payments, org_id=org_id)

    for table in DELETED_DIRECTLY:
        await queries.DELETE_TENANT_ROWS[table](session, org_id)

    deleted_at = datetime.now(UTC)
    _scrub(organization, deleted_at=deleted_at)
    audit.record_organization_deletion_requested(session, org_id=org_id)
    return DeletionAccepted(organization_id=org_id, deleted_at=deleted_at)


def _scrub(organization: Organization, *, deleted_at: datetime) -> None:
    """Replace what the customer wrote and mark what is owed, on the one row."""
    organization.deleted_at = deleted_at
    organization.slug = f"{_SCRUBBED_SLUG_PREFIX}{organization.id.hex}"
    organization.name = _SCRUBBED_NAME
