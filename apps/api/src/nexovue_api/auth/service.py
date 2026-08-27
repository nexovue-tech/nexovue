"""Signing in, signing out, and answering who is signed in.

No HTTP and no SQL text: the router owns the cookie, `queries.py` owns the
lookups, and what is left here is the decision.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.credentials import normalize_email, verify_password
from nexovue_api.auth.queries import find_active_user_by_email
from nexovue_api.auth.schemas import IdentityResponse, OrganizationSummary, UserSummary
from nexovue_api.data.postgres.models import Organization, User
from nexovue_api.errors.exceptions import UnauthenticatedError

# One string for both failures. An unknown email and a wrong password must be
# indistinguishable, or the endpoint tells an attacker which addresses exist.
INVALID_CREDENTIALS_DETAIL: Final = "That email and password do not match an account."


async def authenticate(
    session: AsyncSession, *, email: str, password: str
) -> tuple[User, Organization]:
    """The user behind these credentials, or a 401 that reveals nothing."""
    found = await find_active_user_by_email(session, normalize_email(email))
    stored_hash = found[0].password_hash if found is not None else None
    # Always verify, even with no user: see `credentials.verify_password`.
    if not verify_password(stored_hash, password) or found is None:
        raise UnauthenticatedError(INVALID_CREDENTIALS_DETAIL)
    return found


def describe_identity(user: User, organization: Organization) -> IdentityResponse:
    """The shared body of login, signup's session, and `/auth/me`."""
    return IdentityResponse(
        user=UserSummary(id=user.id, email=user.email, name=user.name, role=user.role),
        organization=OrganizationSummary(
            id=organization.id, slug=organization.slug, name=organization.name
        ),
    )
