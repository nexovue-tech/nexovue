"""Closing an account.

The organization is taken from the session and never from the request. There is
no id in the path and no body: the only tenant this route can delete is the
caller's own, which is a property of the URL rather than of a check somebody
remembered to write.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from nexovue_api.auth.policy import MayEndTheOrganization
from nexovue_api.billing.dependencies import OptionalDomesticPayments
from nexovue_api.deps import DbSession
from nexovue_api.organizations import service
from nexovue_api.organizations.schemas import DeletionAccepted

router = APIRouter(prefix="/organization", tags=["organization"])


@router.delete(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Close this organization and erase everything it holds",
)
async def delete_organization(
    identity: MayEndTheOrganization,
    db: DbSession,
    payments: OptionalDomesticPayments,
) -> DeletionAccepted:
    """202, not 204: the account is closed and its rows are gone now; its
    telemetry and files are erased by the scheduler's nightly run (ADR-014)."""
    return await service.request_deletion(db, org_id=identity.org_id, payments=payments)
