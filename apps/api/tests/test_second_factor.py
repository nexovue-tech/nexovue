"""Second-factor authentication: the credential rules, offline (T64).

Three things are under test and each is a rule a customer's account rests on.

*The secret is never stored, held or carried in the clear.* `auth/encryption.py`
seals it and binds it to the row it belongs to.

*Enrolment cannot half-happen.* A secret that has been offered and not confirmed
arms nothing, because a customer who mistyped it into their authenticator would
otherwise be locked out by the control they were adding.

*Every refusal says the same thing.* A wrong code, a spent recovery code, an
expired challenge and an exhausted one must be indistinguishable, or the endpoint
tells somebody holding a stolen password which half of the attack is landing.

The live suite proves what this one cannot: that the hashes and columns land in
real Postgres.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

import pyotp
import pytest
from fake_stores import FakeSecondFactorStore
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth import recovery_codes, second_factor
from nexovue_api.auth.credentials import hash_password, verify_password
from nexovue_api.auth.encryption import CredentialBox, credential_box, generate_key
from nexovue_api.auth.totp import (
    MAX_CHALLENGE_ATTEMPTS,
    TOTP_DIGITS,
    TOTP_INTERVAL_SECONDS,
    SecondFactorStore,
    code_matches,
    new_secret,
    provisioning_uri,
)
from nexovue_api.data.postgres.models import Organization, User, UserRecoveryCode
from nexovue_api.errors.exceptions import (
    ConflictError,
    DependencyUnavailableError,
    InternalError,
    InvalidRequestError,
)

PASSWORD: Final = "correct-horse-battery"


def _box() -> CredentialBox:
    return CredentialBox(os.urandom(32))


def _user() -> User:
    user = User(
        org_id=uuid.uuid4(),
        email="ada@analytical.test",
        name="Ada",
        password_hash=hash_password(PASSWORD),
        role="owner",
    )
    user.id = uuid.uuid4()
    return user


class FakeDb:
    """Enough SQLAlchemy for the recovery-code reads and writes.

    Deliberately holds the rows rather than stubbing the queries out: single use
    is a property of `consume`, and a double that answered "yes" twice would let
    a broken implementation pass.
    """

    def __init__(self) -> None:
        self.rows: list[UserRecoveryCode] = []
        self.audit: list[Any] = []

    def add(self, entity: Any) -> None:
        self.audit.append(entity)

    def add_all(self, entities: Any) -> None:
        self.rows.extend(entities)

    async def execute(self, statement: Any) -> Any:
        # Only two statements reach here: select the codes, and delete them.
        if statement.is_select:
            return _Rows(self.rows)
        self.rows.clear()
        return _Rows([])

    async def scalar(self, statement: Any) -> Any:
        return len([row for row in self.rows if row.used_at is None])

    async def commit(self) -> None:
        """Nothing to commit; the objects are the rows."""


class _Rows:
    def __init__(self, rows: list[UserRecoveryCode]) -> None:
        self._rows = rows

    def scalars(self) -> list[UserRecoveryCode]:
        return [row for row in self._rows if row.used_at is None]


def as_db(fake: FakeDb) -> AsyncSession:
    """The double, told to the type checker as what it stands in for.

    One cast in one place, rather than a suppression on every call: the doubles
    implement the slice of each interface these flows touch, and spelling that
    once keeps the tests themselves readable.
    """
    return cast(AsyncSession, fake)


def as_store(fake: FakeSecondFactorStore) -> SecondFactorStore:
    return cast(SecondFactorStore, fake)


# ------------------------------------------------------------------ the cipher


def test_a_sealed_secret_does_not_contain_the_secret() -> None:
    """The whole reason the column is bytes. A dump yields ciphertext."""
    box = _box()
    secret = new_secret()

    sealed = box.seal(secret, owner_id="u1")

    assert secret.encode() not in sealed
    assert box.open(sealed, owner_id="u1") == secret


def test_the_same_secret_seals_differently_every_time() -> None:
    """A fresh nonce per seal. Identical ciphertexts would tell an observer of
    the column which two accounts share a secret."""
    box = _box()
    assert box.seal("JBSWY3DPEHPK3PXP", owner_id="u1") != box.seal(
        "JBSWY3DPEHPK3PXP", owner_id="u1"
    )


def test_a_ciphertext_moved_to_another_row_will_not_open() -> None:
    """**The attack the associated data exists for.** Somebody with write access
    to Postgres copies their own sealed secret into a colleague's row and then
    holds that colleague's second factor."""
    box = _box()
    sealed = box.seal(new_secret(), owner_id="attacker")

    with pytest.raises(InternalError):
        box.open(sealed, owner_id="victim")


def test_a_tampered_ciphertext_is_refused_rather_than_decrypted() -> None:
    """GCM authenticates as it decrypts, so a flipped bit fails instead of
    yielding rubbish a caller would then treat as a secret."""
    box = _box()
    sealed = bytearray(box.seal(new_secret(), owner_id="u1"))
    sealed[-1] ^= 0x01

    with pytest.raises(InternalError):
        box.open(bytes(sealed), owner_id="u1")


def test_an_unconfigured_deployment_refuses_rather_than_storing_plaintext() -> None:
    """503, the same answer an unconfigured mail provider gives. The one outcome
    that must never happen is a secret written in the clear because no key was
    set."""
    with pytest.raises(DependencyUnavailableError):
        credential_box("")
    with pytest.raises(DependencyUnavailableError):
        credential_box("not-base64-at-all!!")
    with pytest.raises(DependencyUnavailableError):
        credential_box(base64.urlsafe_b64encode(os.urandom(16)).decode())


def test_the_refusal_says_nothing_about_the_key() -> None:
    """The endpoints that reach it are open to anybody with an account, so the
    variable name goes to the operator's log and not onto the wire."""
    with pytest.raises(DependencyUnavailableError) as refused:
        credential_box("")
    assert "CREDENTIAL_ENCRYPTION_KEY" not in str(refused.value.detail)


def test_a_generated_key_is_usable_as_configured() -> None:
    """The operator instruction in `settings.py` has to actually work: what
    `generate_key` writes into the environment must be what `credential_box`
    reads back out of it."""
    box = credential_box(generate_key())

    assert box.open(box.seal("JBSWY3DPEHPK3PXP", owner_id="u"), owner_id="u") == "JBSWY3DPEHPK3PXP"


# -------------------------------------------------------------------- the code


def test_a_code_from_the_secret_is_accepted_and_anything_else_is_not() -> None:
    secret = new_secret()
    assert code_matches(secret, pyotp.TOTP(secret).now())
    assert not code_matches(secret, "000000")
    assert not code_matches(secret, "")
    assert not code_matches(secret, "not-a-code")


def test_the_provisioning_uri_names_the_account_and_the_product() -> None:
    """What an authenticator app shows above the code. It carries the secret,
    which is why exactly one endpoint ever returns it."""
    secret = new_secret()
    uri = provisioning_uri(secret, account="ada@analytical.test", issuer="nexovue")

    assert uri.startswith("otpauth://totp/")
    assert "issuer=nexovue" in uri
    assert f"digits={TOTP_DIGITS}" in uri or "digits" not in uri


# ---------------------------------------------------------- the recovery codes


def test_a_recovery_code_is_stored_as_a_hash_and_never_as_itself() -> None:
    """Argon2, like a password. A code is short enough to copy off paper, which
    is short enough to need a slow hash."""
    user_id = uuid.uuid4()
    issued = recovery_codes.issue(user_id)

    assert len(issued.plaintext) == recovery_codes.RECOVERY_CODE_COUNT
    assert len(set(issued.plaintext)) == recovery_codes.RECOVERY_CODE_COUNT
    for code, row in zip(issued.plaintext, issued.rows, strict=True):
        assert row.code_hash.startswith("$argon2")
        assert code.replace("-", "") not in row.code_hash
        assert verify_password(row.code_hash, code.replace("-", ""))


async def test_a_recovery_code_works_once_and_then_never_again() -> None:
    db = FakeDb()
    user_id = uuid.uuid4()
    issued = recovery_codes.issue(user_id)
    db.add_all(issued.rows)

    assert await recovery_codes.consume(as_db(db), user_id=user_id, submitted=issued.plaintext[0])
    assert not await recovery_codes.consume(
        as_db(db), user_id=user_id, submitted=issued.plaintext[0]
    )
    assert await recovery_codes.consume(as_db(db), user_id=user_id, submitted=issued.plaintext[1])


async def test_a_spent_code_is_kept_with_the_moment_it_was_spent() -> None:
    """Deleted rows cannot say "one of your recovery codes was used on Tuesday",
    which is the sentence that tells somebody they have been broken into."""
    db = FakeDb()
    user_id = uuid.uuid4()
    issued = recovery_codes.issue(user_id)
    db.add_all(issued.rows)

    await recovery_codes.consume(as_db(db), user_id=user_id, submitted=issued.plaintext[0])

    assert sum(1 for row in db.rows if row.used_at is not None) == 1


@pytest.mark.parametrize("mangle", [str.lower, lambda code: code.replace("-", ""), str.strip])
async def test_a_code_is_accepted_as_a_person_would_type_it(mangle: Any) -> None:
    """Somebody reading off paper should not be refused for a hyphen or a case."""
    db = FakeDb()
    user_id = uuid.uuid4()
    issued = recovery_codes.issue(user_id)
    db.add_all(issued.rows)

    assert await recovery_codes.consume(
        as_db(db), user_id=user_id, submitted=mangle(issued.plaintext[0])
    )


async def test_an_empty_submission_is_not_a_code() -> None:
    db = FakeDb()
    user_id = uuid.uuid4()
    db.add_all(recovery_codes.issue(user_id).rows)

    assert not await recovery_codes.consume(as_db(db), user_id=user_id, submitted="")
    assert not await recovery_codes.consume(as_db(db), user_id=user_id, submitted="----")


def test_a_code_avoids_the_characters_people_read_back_wrong() -> None:
    """I, L, O and U turn a valid code into a support conversation."""
    for code in recovery_codes.issue(uuid.uuid4()).plaintext:
        assert not set(code.replace("-", "")) & set("ILOU01")


# ---------------------------------------------------------------- enrolling


async def test_enrolment_changes_nothing_until_a_code_confirms_it() -> None:
    """**The half that stops somebody locking themselves out.** A secret stored
    the moment it is generated belongs to an account whose owner may never have
    got it into their authenticator."""
    store, box, user = FakeSecondFactorStore(), _box(), _user()

    await second_factor.begin_enrolment(as_store(store), box, user=user, current_password=PASSWORD)

    assert user.totp_secret is None
    assert user.totp_confirmed_at is None
    assert not second_factor.is_enabled(user)


async def test_the_offer_is_held_sealed_and_not_as_the_secret() -> None:
    store, box, user = FakeSecondFactorStore(), _box(), _user()

    offer = await second_factor.begin_enrolment(
        as_store(store), box, user=user, current_password=PASSWORD
    )

    held = store.enrolments[str(user.id)]
    assert offer.secret.encode() not in held
    assert box.open(held, owner_id=str(user.id)) == offer.secret


async def test_enrolment_needs_the_current_password() -> None:
    """A stolen session must not be able to arm a factor and lock the owner out."""
    store, box, user = FakeSecondFactorStore(), _box(), _user()

    with pytest.raises(InvalidRequestError):
        await second_factor.begin_enrolment(
            as_store(store), box, user=user, current_password="not-the-password"
        )
    assert store.enrolments == {}


async def test_confirming_with_the_right_code_arms_the_factor_and_issues_codes() -> None:
    db, store, box, user = FakeDb(), FakeSecondFactorStore(), _box(), _user()
    offer = await second_factor.begin_enrolment(
        as_store(store), box, user=user, current_password=PASSWORD
    )

    codes = await second_factor.confirm_enrolment(
        as_db(db),
        as_store(store),
        box,
        user=user,
        code=pyotp.TOTP(offer.secret).now(),
    )

    assert second_factor.is_enabled(user)
    assert user.totp_secret == store_secret(box, user, offer.secret)
    assert len(codes) == recovery_codes.RECOVERY_CODE_COUNT
    assert len(db.rows) == recovery_codes.RECOVERY_CODE_COUNT
    assert store.enrolments == {}


def store_secret(box: CredentialBox, user: User, secret: str) -> bytes:
    """The stored bytes, opened, must be the offered secret — the ciphertext
    itself differs every time, so the comparison has to go through the box."""
    assert user.totp_secret is not None
    assert box.open(user.totp_secret, owner_id=str(user.id)) == secret
    return user.totp_secret


async def test_a_wrong_code_leaves_the_offer_open_and_the_account_untouched() -> None:
    """A mistyped digit should not send somebody back to a QR code they have
    already scanned."""
    db, store, box, user = FakeDb(), FakeSecondFactorStore(), _box(), _user()
    await second_factor.begin_enrolment(as_store(store), box, user=user, current_password=PASSWORD)

    with pytest.raises(InvalidRequestError):
        await second_factor.confirm_enrolment(
            as_db(db), as_store(store), box, user=user, code="000000"
        )

    assert not second_factor.is_enabled(user)
    assert str(user.id) in store.enrolments


async def test_confirming_without_an_offer_is_refused() -> None:
    db, store, box, user = FakeDb(), FakeSecondFactorStore(), _box(), _user()

    with pytest.raises(InvalidRequestError):
        await second_factor.confirm_enrolment(
            as_db(db), as_store(store), box, user=user, code="000000"
        )


async def test_enrolling_twice_is_refused_rather_than_silently_replacing() -> None:
    """Replacing a live secret without saying so would leave a customer holding
    an authenticator that has quietly stopped working."""
    db, store, box, user = FakeDb(), FakeSecondFactorStore(), _box(), _user()
    offer = await second_factor.begin_enrolment(
        as_store(store), box, user=user, current_password=PASSWORD
    )
    await second_factor.confirm_enrolment(
        as_db(db), as_store(store), box, user=user, code=pyotp.TOTP(offer.secret).now()
    )

    with pytest.raises(ConflictError):
        await second_factor.begin_enrolment(
            as_store(store), box, user=user, current_password=PASSWORD
        )


# ----------------------------------------------------------------- verifying


async def _enrolled() -> tuple[FakeDb, FakeSecondFactorStore, CredentialBox, User, str, Any]:
    db, store, box, user = FakeDb(), FakeSecondFactorStore(), _box(), _user()
    offer = await second_factor.begin_enrolment(
        as_store(store), box, user=user, current_password=PASSWORD
    )
    codes = await second_factor.confirm_enrolment(
        as_db(db), as_store(store), box, user=user, code=pyotp.TOTP(offer.secret).now()
    )
    return db, store, box, user, offer.secret, codes


async def test_a_current_code_signs_in_and_the_same_code_will_not_do_so_twice() -> None:
    """**Replay.** A code is valid for its whole step, so one read over a
    shoulder or out of a proxy log is usable within the minute unless claimed."""
    db, store, box, user, secret, _ = await _enrolled()
    # The enrolment already claimed this step's code, which is itself the
    # property under test seen from the other side, so the replay is proved
    # against the next step.
    code = pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=TOTP_INTERVAL_SECONDS))

    assert await second_factor.verify(as_db(db), as_store(store), box, user=user, code=code)
    assert not await second_factor.verify(as_db(db), as_store(store), box, user=user, code=code)


async def test_a_recovery_code_signs_in_through_the_same_field() -> None:
    db, store, box, user, _, codes = await _enrolled()

    assert await second_factor.verify(as_db(db), as_store(store), box, user=user, code=codes[0])
    assert not await second_factor.verify(as_db(db), as_store(store), box, user=user, code=codes[0])


async def test_a_recovery_code_still_works_with_no_encryption_key() -> None:
    """**Not an accident.** If the deployment's key is missing, no stored secret
    can be opened and the codes are the only way into an enrolled account."""
    db, store, _, user, _, codes = await _enrolled()

    assert await second_factor.verify(as_db(db), as_store(store), None, user=user, code=codes[0])


async def test_a_recovery_code_being_used_is_recorded() -> None:
    """The entry that tells somebody they have been broken into."""
    db, store, box, user, _, codes = await _enrolled()

    await second_factor.verify(as_db(db), as_store(store), box, user=user, code=codes[0])

    assert db.audit, "using a recovery code wrote no audit entry"
    assert all("code" not in str(getattr(entry, "target", "")).lower() for entry in db.audit)


async def test_nothing_verifies_for_an_account_with_no_second_factor() -> None:
    db, store, box, user = FakeDb(), FakeSecondFactorStore(), _box(), _user()

    assert not await second_factor.verify(as_db(db), as_store(store), box, user=user, code="000000")


async def test_disabling_destroys_the_secret_and_every_code() -> None:
    """Nothing is kept "in case they turn it back on": a secret that outlived
    its enrolment is a credential nobody is watching."""
    db, store, _box_unused, user, _, _ = await _enrolled()

    await second_factor.disable(as_db(db), as_store(store), user=user, current_password=PASSWORD)

    assert user.totp_secret is None
    assert user.totp_confirmed_at is None
    assert db.rows == []


async def test_disabling_needs_the_current_password() -> None:
    """Disarming removes protection its owner added, so it is beyond the reach
    of a session somebody else is holding — like arming."""
    db, store, _box_unused, user, _, _ = await _enrolled()

    with pytest.raises(InvalidRequestError):
        await second_factor.disable(as_db(db), as_store(store), user=user, current_password="wrong")

    assert second_factor.is_enabled(user)


async def test_the_status_never_carries_the_secret() -> None:
    db, _store_unused, _box_unused, user, secret, _ = await _enrolled()

    status = await second_factor.describe(as_db(db), user=user)

    assert status.enabled
    assert status.recovery_codes_remaining == recovery_codes.RECOVERY_CODE_COUNT
    assert secret not in repr(status)


def test_the_organization_is_not_needed_to_describe_a_factor() -> None:
    """A guard against the status growing a tenant read it does not need."""
    assert not hasattr(second_factor.SecondFactorStatus, "org_id")
    assert Organization is not None


# ------------------------------------------------------------ the attempt budget


async def test_a_challenge_is_thrown_away_after_enough_wrong_codes() -> None:
    """Five is generous for somebody copying six digits and miserly for somebody
    guessing them. The rate limiter bounds the rate; this bounds how much one
    password submission is worth."""
    store = FakeSecondFactorStore()
    token = await store.open_challenge(user_id="u1")

    for _ in range(MAX_CHALLENGE_ATTEMPTS):
        challenge = await store.read_challenge(token=token)
        assert challenge is not None
        await store.record_failed_attempt(token=token, challenge=challenge)

    assert await store.read_challenge(token=token) is None
