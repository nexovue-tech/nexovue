"""Create or disable a staff account, from a terminal (F31, ADR-016).

There is no screen that creates staff: the power a staff account holds is not
something a web form should be able to grant.

    python -m nexovue_api.staff create --email raj@nexovue.com --name Raj
    python -m nexovue_api.staff disable --email raj@nexovue.com

`create` asks for a password, prints an `otpauth://` URI to add to an
authenticator, and asks for the code it shows. The account is written only if
the code matches — so none exists without its second factor.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.encryption import STAFF_TOTP_PURPOSE, credential_box
from nexovue_api.auth.totp import new_secret, provisioning_uri
from nexovue_api.data.postgres import postgres_dsn
from nexovue_api.errors.exceptions import DomainError
from nexovue_api.staff import service

EXIT_REFUSED: Final = 2
MIN_PASSWORD_LENGTH: Final = 12
ISSUER: Final = "nexovue staff"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nexovue_api.staff")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a staff account, enrolling TOTP")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    disable = commands.add_parser("disable", help="stop a staff account signing in")
    disable.add_argument("--email", required=True)
    return parser


def _password() -> str:
    first = getpass.getpass("Password (at least 12 characters): ")
    if len(first) < MIN_PASSWORD_LENGTH:
        raise SystemExit("Too short. Nothing was created.")
    if getpass.getpass("Again: ") != first:
        raise SystemExit("The two did not match. Nothing was created.")
    return first


async def _create(email: str, name: str) -> str:
    box = credential_box(
        os.environ.get("CREDENTIAL_ENCRYPTION_KEY", ""), purpose=STAFF_TOTP_PURPOSE
    )
    password = _password()
    secret = new_secret()
    print("\nAdd this to your authenticator (it is shown once):\n")
    print(provisioning_uri(secret, account=email, issuer=ISSUER))
    code = input("\nThe code it shows now: ").strip()
    engine = create_async_engine(postgres_dsn())
    try:
        async with AsyncSession(engine) as session, session.begin():
            account = await service.create_account(
                session,
                box,
                email=email,
                name=name,
                password=password,
                secret=secret,
                code=code,
            )
            return f"Staff account created for {account.email}."
    finally:
        await engine.dispose()


async def _disable(email: str) -> str:
    engine = create_async_engine(postgres_dsn())
    try:
        async with AsyncSession(engine) as session, session.begin():
            account = await service.disable(session, email=email)
            return (
                f"{email} can no longer sign in." if account else f"No staff account for {email}."
            )
    finally:
        await engine.dispose()


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "create":
            print(asyncio.run(_create(arguments.email, arguments.name)))
        else:
            print(asyncio.run(_disable(arguments.email)))
    except DomainError as refused:
        print(refused, file=sys.stderr)
        return EXIT_REFUSED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
