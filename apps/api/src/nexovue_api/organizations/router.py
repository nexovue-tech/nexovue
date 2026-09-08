"""Closing an account.

The organization is taken from the session and never from the request. There is
no id in the path and no body: the only tenant this route can delete is the
caller's own, which is a property of the URL rather than of a check somebody
remembered to write.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, status

from nexovue_api.auth.policy import MayEndTheOrganization
from nexovue_api.deps import DbSession
from nexovue_api.organizations import service
from nexovue_api.organizations.dependencies import ApplicationResources, TenantErasure
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
    resources: ApplicationResources,
    erasure: TenantErasure,
    background: BackgroundTasks,
) -> DeletionAccepted:
    """202, not 204: the account is closed now and the erasure has not finished.

    The background task is a head start, not the guarantee. It runs in this
    process and dies with it; what makes the erasure certain is the obligation
    the transaction below commits, which `service.erasures_still_owed` finds
    again afterwards.
    """
    accepted = await service.request_deletion(
        db, org_id=identity.org_id, actor_user_id=identity.user.id
    )
    background.add_task(
        service.erase_after_response,
        resources.session_factory,
        erasure,
        resources.object_store,
    )
    return accepted
