"""Creating a tenant: one organization, one owner, one project, one key.

These four rows are the whole of what it means for a customer to exist, and
they are created in a single transaction — a half-provisioned tenant is an
account nobody can sign into or send data to, and there is no repair path for
one. `seed.py` calls straight into here so local development and a real signup
cannot drift apart.

The caller owns the transaction: this flushes to obtain ids and never commits,
so the request-scoped session dependency stays the one place a commit happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.credentials import hash_password, is_valid_email, normalize_email
from nexovue_api.data.postgres.models import (
    API_KEY_PREFIX_LENGTH,
    ApiKey,
    Organization,
    Project,
    ProjectWebhookSecret,
    User,
)
from nexovue_api.deploys.signatures import new_secret
from nexovue_api.errors.exceptions import ConflictError, InternalError, InvalidRequestError
from nexovue_api.signup import queries
from nexovue_api.signup.apikeys import generate_api_key, hash_api_key
from nexovue_api.signup.slugs import disambiguate, slugify

# The first project every tenant gets. Named for the environment it stands for,
# because a customer's first spans come from something already running.
DEFAULT_PROJECT_SLUG: Final = "production"
DEFAULT_PROJECT_NAME: Final = "Production"
DEFAULT_API_KEY_NAME: Final = "default"

# Whoever creates the organization owns it; there is nobody else to grant it to.
OWNER_ROLE: Final = "owner"

EMAIL_TAKEN_DETAIL: Final = "An account already exists for that email address."
EMAIL_INVALID_DETAIL: Final = "That email address is not valid."

# Bounded so a pathological name or an exhausted keyspace fails loudly instead
# of spinning. Both bounds are far beyond what a real signup ever needs.
MAX_SLUG_ATTEMPTS: Final = 100
MAX_KEY_ATTEMPTS: Final = 10


@dataclass(frozen=True, slots=True)
class ProvisionedTenant:
    """The four rows, plus the one plaintext key that is never stored."""

    organization: Organization
    user: User
    project: Project
    api_key: str


async def provision_tenant(
    session: AsyncSession,
    *,
    organization_name: str,
    email: str,
    password: str,
    name: str | None = None,
    organization_slug: str | None = None,
    api_key: str | None = None,
    api_key_name: str = DEFAULT_API_KEY_NAME,
) -> ProvisionedTenant:
    """Create a tenant. `organization_slug` and `api_key` are for the seed only."""
    address = normalize_email(email)
    if not is_valid_email(address):
        raise InvalidRequestError(EMAIL_INVALID_DETAIL)
    if await queries.email_is_taken(session, address):
        raise ConflictError(EMAIL_TAKEN_DETAIL)

    slug = organization_slug or await _available_slug(session, slugify(organization_name))
    plaintext_key = api_key or await _unused_api_key(session)

    organization = Organization(slug=slug, name=organization_name)
    session.add(organization)
    # Flush rather than commit: the ids are wanted, the transaction is not done.
    await _flush(session)

    user = User(
        org_id=organization.id,
        email=address,
        name=name,
        password_hash=hash_password(password),
        role=OWNER_ROLE,
    )
    project = Project(org_id=organization.id, slug=DEFAULT_PROJECT_SLUG, name=DEFAULT_PROJECT_NAME)
    session.add_all([user, project])
    await _flush(session)

    minted_key = ApiKey(
        org_id=organization.id,
        project_id=project.id,
        name=api_key_name,
        key_prefix=plaintext_key[:API_KEY_PREFIX_LENGTH],
        key_hash=hash_api_key(plaintext_key),
    )
    # A project can receive deploy notifications from the moment it exists.
    # Without this the webhook would be configured but reject everything, which
    # reads as broken rather than as unconfigured.
    webhook_secret = ProjectWebhookSecret(
        org_id=organization.id, project_id=project.id, secret=new_secret()
    )
    session.add_all([minted_key, webhook_secret])
    await _flush(session)

    # Written here rather than in the router, so the seed and any later caller
    # record the same two events, in the transaction that creates the tenant.
    audit.record_organization_created(session, organization=organization, owner=user)
    audit.record_api_key_created(session, api_key=minted_key, actor_user_id=user.id)
    await _flush(session)

    return ProvisionedTenant(
        organization=organization, user=user, project=project, api_key=plaintext_key
    )


async def _flush(session: AsyncSession) -> None:
    """Flush, turning a lost race on a unique constraint into a 409."""
    try:
        await session.flush()
    except IntegrityError as error:
        raise ConflictError(_conflict_detail(error)) from error


def _conflict_detail(error: IntegrityError) -> str:
    """Name what collided, without echoing a driver message to the caller."""
    constraint = str(getattr(error.orig, "constraint_name", "") or "")
    if "users" in constraint and "email" in constraint:
        return EMAIL_TAKEN_DETAIL
    return "That signup collided with a concurrent one. Try again."


async def _available_slug(session: AsyncSession, base: str) -> str:
    """The first free form of `base`, suffixed only when it has to be."""
    taken = await queries.taken_slugs(session, base)
    for attempt in range(MAX_SLUG_ATTEMPTS):
        candidate = disambiguate(base, attempt)
        if candidate not in taken:
            return candidate
    raise ConflictError("Too many organizations share that name. Choose another.")


async def _unused_api_key(session: AsyncSession) -> str:
    """A key whose lookup prefix is not already claimed.

    `api_keys.key_prefix` is unique over only twelve characters, so a fresh key
    can genuinely collide; this retries rather than surfacing that to a customer.
    """
    for _ in range(MAX_KEY_ATTEMPTS):
        candidate = generate_api_key()
        if not await queries.key_prefix_is_taken(session, candidate[:API_KEY_PREFIX_LENGTH]):
            return candidate
    raise InternalError("Could not allocate an API key. Report this with the correlation id.")
