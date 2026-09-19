"""Creating staff accounts and signing staff in (F31, FR21, ADR-016).

**No staff account exists without a confirmed second factor.** `create_account`
takes the code the new staff member's authenticator shows and refuses to write
the row unless it matches — the terminal command offers the secret, reads the
code back, and only then calls this.

**Sign-in takes all three at once** — address, password, code — and answers every
failure identically, so a guess learns nothing about which part was wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.credentials import hash_password, normalize_email, verify_password
from nexovue_api.auth.encryption import CredentialBox
from nexovue_api.auth.totp import SecondFactorStore, code_matches
from nexovue_api.data.postgres.models import StaffAccount
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, UnauthenticatedError

SIGN_IN_REFUSED: Final = "That address, password and code do not sign anyone in."
CODE_DID_NOT_MATCH: Final = (
    "That code does not match the secret offered. Nothing was created — check the "
    "authenticator's clock and run the command again."
)
ALREADY_STAFF: Final = "There is already a staff account for that address."


def _owner(email: str) -> str:
    """What the sealed secret is bound to. The address, because the row does not
    exist yet when the secret is sealed; a staff address does not change."""
    return f"staff:{email}"


async def create_account(
    db: AsyncSession,
    box: CredentialBox,
    *,
    email: str,
    name: str,
    password: str,
    secret: str,
    code: str,
) -> StaffAccount:
    address = normalize_email(email)
    if not code_matches(secret, code):
        raise InvalidRequestError(CODE_DID_NOT_MATCH)
    account = StaffAccount(
        email=address,
        name=name.strip(),
        password_hash=hash_password(password),
        totp_secret=box.seal(secret, owner_id=_owner(address)),
    )
    db.add(account)
    try:
        await db.flush()
    except IntegrityError as taken:
        raise ConflictError(ALREADY_STAFF) from taken
    return account


async def sign_in(
    db: AsyncSession,
    box: CredentialBox,
    factors: SecondFactorStore,
    *,
    email: str,
    password: str,
    code: str,
) -> StaffAccount:
    address = normalize_email(email)
    account = await db.scalar(select(StaffAccount).where(StaffAccount.email == address))
    # `verify_password` always runs the hash, so a missing account costs the same.
    password_ok = verify_password(account.password_hash if account else None, password)
    if account is None or not password_ok or account.disabled_at is not None:
        raise UnauthenticatedError(SIGN_IN_REFUSED)
    secret = box.open(account.totp_secret, owner_id=_owner(account.email))
    if not code_matches(secret, code):
        raise UnauthenticatedError(SIGN_IN_REFUSED)
    # A code is good once: a shoulder-surfed code cannot open a second session.
    if not await factors.claim_step(user_id=f"staff:{account.id}", code=code):
        raise UnauthenticatedError(SIGN_IN_REFUSED)
    return account


async def disable(db: AsyncSession, *, email: str) -> StaffAccount | None:
    account = await db.scalar(
        select(StaffAccount).where(StaffAccount.email == normalize_email(email))
    )
    if account is not None and account.disabled_at is None:
        account.disabled_at = datetime.now(UTC)
    return account
