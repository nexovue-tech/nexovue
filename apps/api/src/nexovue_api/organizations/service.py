"""Deleting an account: what happens now, and what happens afterwards.

**Two phases, because two stores.** Marking `organizations.deleted_at` is one
Postgres write and it is the whole of the customer-visible stop: `load_identity`
joins on it, so every session in the tenant is refused on its next request, and
the receiver's allowance gate reads the same column, so ingest is refused within
its cache TTL. That is immediate and it is what the endpoint promises. Erasing
what is stored is not: a ClickHouse mutation over thirteen tables runs for
minutes and the object store is a separate system again. So the request records
an obligation and returns 202, and `erase` discharges it.

**Where the obligation lives, without a table for it.** `organizations` with
`deleted_at` set *is* the record — the row a tenant deletion cannot finish
without, holding the id the ClickHouse purge and the object prefix are both
named from. A job table would hold the same id and the same timestamp, and it
would have to be kept in step with this column. Completion is recorded as its
opposite: an `audit_log` entry with a null `org_id`, which is the one row about
this tenant that its own erasure cannot cascade away. Together they answer
"which erasures are owed" and "which have been proved", with no schema change.

**The organization row is scrubbed and kept, not deleted.** Three reasons, and
the first is the one that matters. The receiver refuses a deleted organization
by reading `deleted_at` off this row; when no row answers, its gate reads that
as "cannot find" and *admits* — the bug `allowance/postgres.go` records having
fixed. Deleting the row would reintroduce it and rest the refusal entirely on
the keys having gone. Second, an erasure can be overtaken by ingest: records
already on the broker land after the mutation has passed, and re-purging needs
the id, which is on this row. Third, a deletion has to be provable afterwards,
and a row that no longer exists proves nothing. What is erasable is erased —
`slug` and `name` are replaced, and every row naming a person is deleted — so
what remains is an id, a plan and two timestamps.

**There is no undo.** Not a policy choice: `ALTER TABLE ... DELETE` cannot be
recalled once submitted, so "cancel" could only ever mean "we had not started
yet", which is a delay before phase two rather than a feature of this endpoint.
Adding a grace period later is one constant in front of `erase`, and the phases
are already separate to make room for it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexovue_api.audit import service as audit
from nexovue_api.checkfiles.service import tenant_prefix as check_files_prefix
from nexovue_api.data.objectstore import ObjectStore
from nexovue_api.data.postgres.models import Organization
from nexovue_api.errors.exceptions import ConflictError, NotFoundError
from nexovue_api.organizations import queries
from nexovue_api.organizations.schemas import DeletionAccepted
from nexovue_api.organizations.tables import DELETED_DIRECTLY
from nexovue_api.purge import PurgeUnfinishedError, TenantPurge
from nexovue_api.sourcemaps.service import tenant_prefix as source_maps_prefix

logger = get_logger(__name__)

# What replaces the customer's own words on the tombstone. The slug stays unique
# and stays inside `SLUG_MAX_LENGTH`; more usefully, it frees the name the
# customer chose, so the same company can sign up again.
_SCRUBBED_SLUG_PREFIX: Final = "deleted-"
_SCRUBBED_NAME: Final = "(deleted organization)"

ALREADY_DELETED: Final = "This organization has already been deleted."
NOT_MARKED_FOR_DELETION: Final = "erase was called on an organization nobody asked to delete"

# How long `erase` waits for ClickHouse when the API itself runs it after a
# request. Long enough that a small tenant is certified in the same breath as
# the request, short enough not to hold a worker while a large one rewrites
# parts — the obligation outlives the wait, so giving up early costs nothing but
# a later re-run.
BACKGROUND_WAIT_SECONDS: Final = 120


@dataclass(frozen=True, slots=True)
class ErasureOutcome:
    """What one pass of `erase` removed, and what it could not yet prove gone."""

    organization_id: uuid.UUID
    rows_deleted: dict[str, int]
    objects_deleted: int
    telemetry_remaining: dict[str, int]

    @property
    def is_complete(self) -> bool:
        """Whether this pass finished the obligation. Asked of the store, not a flag."""
        return not self.telemetry_remaining


async def request_deletion(
    session: AsyncSession, *, org_id: uuid.UUID, actor_user_id: uuid.UUID
) -> DeletionAccepted:
    """Close the account now and record that its data is owed an erasure.

    Everything here is one transaction, and it is deliberately small: a mark, a
    scrub and an audit entry. Nothing that can run for minutes belongs in the
    request that a person is waiting on.
    """
    organization = await queries.lock_organization(session, org_id)
    if organization is None:
        raise NotFoundError("No such organization.")
    if organization.deleted_at is not None:
        raise ConflictError(ALREADY_DELETED)

    deleted_at = datetime.now(UTC)
    _scrub(organization, deleted_at=deleted_at)
    audit.record_organization_deletion_requested(
        session, org_id=org_id, actor_user_id=actor_user_id
    )
    return DeletionAccepted(organization_id=org_id, deleted_at=deleted_at)


async def erase(
    session: AsyncSession,
    purge: TenantPurge,
    object_store: ObjectStore | None,
    *,
    wait_seconds: float,
) -> ErasureOutcome:
    """Remove everything the tenant holds, in all three stores.

    Safe to run again, and meant to be: every step either removes rows or finds
    none. A pass that cannot prove the telemetry gone leaves the obligation
    standing and says what is left, rather than certifying an erasure it did not
    watch finish.
    """
    org_id = uuid.UUID(purge.tenant_id)
    await _require_marked_for_deletion(session, org_id)

    # Submitted first, so ClickHouse is rewriting parts while Postgres and the
    # object store are dealt with. It returns as soon as the store holds the
    # obligation durably.
    await purge.submit()

    # Before the rows, so a store that refuses leaves `source_maps` naming what
    # is still there. The prefix is what erases an object whose row was never
    # written; deleting by row could not.
    objects_deleted = await _erase_objects(object_store, org_id)

    rows_deleted = {}
    for table in DELETED_DIRECTLY:
        rows_deleted[table] = await queries.DELETE_TENANT_ROWS[table](session, org_id)
    await session.flush()

    telemetry_remaining = await _telemetry_left_after_waiting(purge, wait_seconds)
    if not telemetry_remaining:
        audit.record_organization_erased(session, org_id=org_id)

    outcome = ErasureOutcome(
        organization_id=org_id,
        rows_deleted=rows_deleted,
        objects_deleted=objects_deleted,
        telemetry_remaining=telemetry_remaining,
    )
    logger.info(
        "organization erased" if outcome.is_complete else "organization erasure still owed",
        org_id=str(org_id),
        rows_deleted=rows_deleted,
        objects_deleted=objects_deleted,
        telemetry_remaining=telemetry_remaining,
    )
    return outcome


async def erasures_still_owed(session: AsyncSession) -> list[uuid.UUID]:
    """Tombstoned organizations with no certificate: the work a sweep re-runs.

    Read from the two rows that record the obligation rather than from a job
    table, so it is also the answer after a restart, a crash mid-erasure, or an
    erasure somebody else started.
    """
    owed = []
    for org_id in await queries.tombstoned_organization_ids(session):
        certified = await queries.erasure_is_certified(
            session,
            action=audit.ORGANIZATION_ERASED,
            target=audit.erasure_certificate_target(org_id),
        )
        if not certified:
            owed.append(org_id)
    return owed


async def erase_after_response(
    session_factory: async_sessionmaker[AsyncSession],
    purge: TenantPurge,
    object_store: ObjectStore | None,
) -> None:
    """Start the erasure once the request that asked for it has been answered.

    Its own session, because the request's was committed and closed with the
    response. A failure here is logged and not raised: nothing is left to answer
    to, and the obligation is already durable — `erasures_still_owed` finds it
    again.
    """
    try:
        async with session_factory() as session:
            await erase(session, purge, object_store, wait_seconds=BACKGROUND_WAIT_SECONDS)
            await session.commit()
    except Exception:
        logger.exception(
            "organization erasure did not finish; it stays owed", org_id=purge.tenant_id
        )


async def _require_marked_for_deletion(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Refuse to erase a tenant nobody asked to delete.

    The one guard between this function and every row of a live customer. The
    mark is made in its own committed transaction, so by the time an erasure
    runs it is always already there.
    """
    organization = await queries.lock_organization(session, org_id)
    if organization is None or organization.deleted_at is None:
        raise NotFoundError(NOT_MARKED_FOR_DELETION)


async def _erase_objects(object_store: ObjectStore | None, org_id: uuid.UUID) -> int:
    """Every object under this tenant's prefixes: source maps and check files.

    None means the deployment configured no store, in which case no upload was
    ever accepted — both uploads refuse without one — so there is nothing to
    erase rather than something unreachable.
    """
    if object_store is None:
        return 0
    erased = 0
    for prefix in (source_maps_prefix(org_id), check_files_prefix(org_id)):
        erased += await object_store.delete_prefix(prefix)
    return erased


async def _telemetry_left_after_waiting(purge: TenantPurge, wait_seconds: float) -> dict[str, int]:
    """Wait for the mutations, and report what is still there if they outlast it.

    A deadline that passes is not a failure. The mutations are recorded on the
    server and go on running; what it means is that this pass may not certify.
    """
    try:
        await purge.wait_until_erased(deadline_seconds=wait_seconds)
    except PurgeUnfinishedError as unfinished:
        return unfinished.remaining
    return {}


def _scrub(organization: Organization, *, deleted_at: datetime) -> None:
    """Replace what the customer wrote and mark what is owed, on the one row."""
    organization.deleted_at = deleted_at
    organization.slug = f"{_SCRUBBED_SLUG_PREFIX}{organization.id.hex}"
    organization.name = _SCRUBBED_NAME
