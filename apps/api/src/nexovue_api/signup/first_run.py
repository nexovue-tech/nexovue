"""The first organization on a self-hosted install, created from the environment.

**The mirror image of `superadmin/seed.py`.** That one creates the account
*nexovue* administers the platform with, and it is absent on a self-hosted
install because there is no nexovue there to administer it. This one creates the
account the person who *installed* nexovue administers their own organization
with, and it is absent on the hosted service because customers there arrive
through signup.

Both exist for the same reason: an account nothing can reach cannot be created by
anything a request can reach. A self-hoster has nobody to invite them — they are
the first person in the system — so the environment is the only way in that does
not mean leaving signup open to the internet.

**It runs once.** Unlike the operator's password, which is rewritten every boot
so that rotation is a restart, this is read on the first run and never again.
After that the account is the customer's: they change the password in the
console, and a restart that reset it to whatever is in a compose file would take
their own account away from them. This is the convention a self-hoster expects —
Grafana's `GF_SECURITY_ADMIN_PASSWORD` behaves the same way.

It provisions through `provision_tenant`, the path a real signup takes, so the
first organization gets exactly what every customer gets: a `production` project,
an ingest key, a deploy webhook secret, the owners channel and the default alert
rule. A bespoke path here would be the one tenant in the system whose defaults
nobody tested.

**The ingest key is not logged.** It is a live credential, and a secret written
to a log file is a secret. The owner signs in and mints one under Settings →
Ingest keys, which is what that screen is for.
"""

from __future__ import annotations

from typing import Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.credentials import normalize_email
from nexovue_api.auth.schemas import PASSWORD_MIN_LENGTH
from nexovue_api.data.postgres.models import Organization
from nexovue_api.errors.exceptions import ConflictError
from nexovue_api.settings import Settings
from nexovue_api.signup.service import provision_tenant
from nexovue_api.superadmin.seed import PLATFORM_ORG_ID

_log: Final = structlog.get_logger(__name__)


async def already_in_use(session: AsyncSession) -> bool:
    """Whether any customer organization exists, so this is not a first run.

    The platform organization does not count: it holds nexovue's own operator and
    no customer, so an install that has only that one has still never been used.

    Deleted organizations *do* count. A customer who deleted theirs and restarted
    should not have it silently recreated from a compose file they wrote months
    ago — that would be the installer overruling a decision the customer made.
    """
    counted = await session.scalar(
        select(func.count()).select_from(Organization).where(Organization.id != PLATFORM_ORG_ID)
    )
    return bool(counted)


async def seed_first_organization(session: AsyncSession, settings: Settings) -> Organization | None:
    """Create the first organization and its owner. Returns None when it did not.

    None covers three different "no", and each is the right answer:
    the settings are unset (the hosted service), the install already has a
    customer organization (not a first run), or the address is taken.
    """
    email = settings.first_owner_email
    password = settings.first_owner_password.get_secret_value()
    if not email or not password:
        return None

    # **The policy applies to the environment too.** `provision_tenant` takes a
    # plain string, so nothing between a compose file and the hash enforces what
    # the signup form enforces — and this is the *only* account on the install
    # it creates. A five-character password here would be the one nobody could
    # have typed through a screen.
    if len(password) < PASSWORD_MIN_LENGTH:
        _log.warning(
            "the first organization was not created: the password is shorter than the "
            "minimum the sign-up form enforces",
            minimum=PASSWORD_MIN_LENGTH,
        )
        return None

    if await already_in_use(session):
        return None

    address = normalize_email(email)
    try:
        tenant = await provision_tenant(
            session,
            organization_name=settings.first_organization_name,
            email=address,
            password=password,
            name=None,
        )
    except ConflictError:
        # The operator's address, if one was seeded, is already a `users` row —
        # an install naming the same address for both reaches this. Caught rather
        # than pre-checked: `provision_tenant` owns the uniqueness rule, and a
        # second query here would be a copy of it that could drift, and one the
        # tenancy guard would rightly refuse for naming no tenant. Refusing at
        # boot is louder than a setting somebody can simply correct deserves.
        #
        # `provision_tenant` checks the address before it writes anything, so the
        # session is clean here and the caller's transaction commits nothing. The
        # other way to reach this error — losing a race on the unique constraint
        # at flush time — would leave the session needing a rollback, and the
        # caller's commit would then fail and be logged. That is the safe
        # direction, and a race for the first account on a first boot needs two
        # API instances starting at once against an empty database.
        _log.warning(
            "the first organization was not created: that address already signs in here",
            email=address,
        )
        return None
    # Verified on sight, for the reason the operator's address is: it came from
    # the environment rather than from somebody typing it, so there is nothing to
    # confirm — and a self-hosted install may have no mail provider at all, which
    # would leave the only account permanently unverified and alertless.
    tenant.user.email_verified_at = tenant.organization.created_at
    await session.flush()

    _log.info(
        "first organization created from the environment",
        organization=tenant.organization.name,
        slug=tenant.organization.slug,
        owner=address,
        # Deliberately not the ingest key: sign in and mint one.
        next_step="sign in, then create an ingest key under Settings → Ingest keys",
    )
    return tenant.organization
