"""What each encryption key holds, and moving it all to the current key.

The other half of the keyring (readiness blocker 8, `nexovue_platform.checks.
sealing`). After a rotation the old key is kept as a previous key, which only
opens; `reseal` re-seals every value still under it with the current key, and
`status` says when nothing is left under it, so it can be removed. The procedure
is `docs/operations/key-rotation.md`.

Run where the keys are — the API's container — because they live in its
environment and nowhere else:

    python -m nexovue_api.keys status
    python -m nexovue_api.keys reseal

**Across every tenant, on purpose.** A key is the deployment's, not an
organization's. Every statement here names one sealed column and its primary
key, and an update changes only that column, and only if it still holds the
value that was read — a customer who saved a new secret meanwhile keeps it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from nexovue_platform.checks.sealing import (
    CHECK_SECRET_PURPOSE,
    NOTIFICATION_CHANNEL_PURPOSE,
    ConfiguredKeys,
    SealedBox,
    SealError,
    channel_secret_owner,
    check_secret_owner,
    keyring_from,
    sealed_key_id,
)
from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from nexovue_api.auth.encryption import TOTP_PURPOSE
from nexovue_api.data.postgres import postgres_dsn

BATCH_SIZE: Final = 500

CURRENT: Final = "current"
PREVIOUS: Final = "previous"
UNREADABLE: Final = "unreadable"

EXIT_UNREADABLE: Final = 2
EXIT_NOT_CONFIGURED: Final = 3


@dataclass(frozen=True, slots=True)
class Keys:
    """The two keyrings, read from the environment the API reads them from."""

    check: ConfiguredKeys
    credential: ConfiguredKeys

    @classmethod
    def from_environment(cls) -> Keys:
        return cls(
            check=ConfiguredKeys(
                os.environ.get("CHECK_SECRET_KEY", ""),
                os.environ.get("CHECK_SECRET_PREVIOUS_KEYS", ""),
            ),
            credential=ConfiguredKeys(
                os.environ.get("CREDENTIAL_ENCRYPTION_KEY", ""),
                os.environ.get("CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS", ""),
            ),
        )


@dataclass(frozen=True, slots=True)
class SealedColumn:
    """One column holding sealed values: how to page it, whom each is bound to,
    and how to replace one only if it has not changed."""

    name: str
    purpose: str
    keys: Callable[[Keys], ConfiguredKeys]
    page: TextClause
    replace: TextClause
    owner: Callable[[Any], str]


SEALED_COLUMNS: Final[tuple[SealedColumn, ...]] = (
    SealedColumn(
        name="check_secrets.sealed_value",
        purpose=CHECK_SECRET_PURPOSE,
        keys=lambda keys: keys.check,
        page=text(
            "SELECT id AS pk, project_id, name, sealed_value AS sealed FROM check_secrets "
            "WHERE id > :after ORDER BY id LIMIT :limit"
        ),
        replace=text(
            "UPDATE check_secrets SET sealed_value = :new WHERE id = :pk AND sealed_value = :old"
        ),
        owner=lambda row: check_secret_owner(str(row.project_id), row.name),
    ),
    SealedColumn(
        name="notification_channels.sealed_secret",
        purpose=NOTIFICATION_CHANNEL_PURPOSE,
        keys=lambda keys: keys.check,
        page=text(
            "SELECT id AS pk, org_id, sealed_secret AS sealed FROM notification_channels "
            "WHERE sealed_secret IS NOT NULL AND id > :after ORDER BY id LIMIT :limit"
        ),
        replace=text(
            "UPDATE notification_channels SET sealed_secret = :new "
            "WHERE id = :pk AND sealed_secret = :old"
        ),
        owner=lambda row: channel_secret_owner(str(row.org_id), str(row.pk)),
    ),
    SealedColumn(
        name="users.totp_secret",
        purpose=TOTP_PURPOSE,
        keys=lambda keys: keys.credential,
        page=text(
            "SELECT id AS pk, totp_secret AS sealed FROM users "
            "WHERE totp_secret IS NOT NULL AND id > :after ORDER BY id LIMIT :limit"
        ),
        replace=text("UPDATE users SET totp_secret = :new WHERE id = :pk AND totp_secret = :old"),
        owner=lambda row: str(row.pk),
    ),
    SealedColumn(
        name="totp_enrolments.sealed_secret",
        purpose=TOTP_PURPOSE,
        keys=lambda keys: keys.credential,
        page=text(
            "SELECT user_id AS pk, sealed_secret AS sealed FROM totp_enrolments "
            "WHERE user_id > :after ORDER BY user_id LIMIT :limit"
        ),
        replace=text(
            "UPDATE totp_enrolments SET sealed_secret = :new "
            "WHERE user_id = :pk AND sealed_secret = :old"
        ),
        owner=lambda row: str(row.pk),
    ),
)

# A UUID below every real one, to start paging from.
_FIRST: Final = "00000000-0000-0000-0000-000000000000"


@dataclass(slots=True)
class ColumnReport:
    name: str
    counts: Counter[str]
    resealed: int = 0
    configured: bool = True

    def line(self) -> str:
        if not self.configured:
            held = sum(self.counts.values())
            return f"{self.name}: no key configured; {held} value(s) cannot be read"
        parts = [f"{kind} {self.counts[kind]}" for kind in (CURRENT, PREVIOUS, UNREADABLE)]
        moved = f"; re-sealed {self.resealed}" if self.resealed else ""
        return f"{self.name}: {', '.join(parts)}{moved}"


async def survey(connection: AsyncConnection, keys: Keys, *, reseal: bool) -> list[ColumnReport]:
    """Count every sealed value by the key that sealed it; with `reseal`, move
    those under a previous key to the current one."""
    return [await _column(connection, column, keys, reseal=reseal) for column in SEALED_COLUMNS]


async def _column(
    connection: AsyncConnection, column: SealedColumn, keys: Keys, *, reseal: bool
) -> ColumnReport:
    configured = column.keys(keys)
    report = ColumnReport(name=column.name, counts=Counter(), configured=bool(configured.current))
    box = (
        SealedBox(keyring_from(configured.current, configured.previous), purpose=column.purpose)
        if configured.current
        else None
    )
    after: Any = _FIRST
    while True:
        rows = (await connection.execute(column.page, {"after": after, "limit": BATCH_SIZE})).all()
        if not rows:
            return report
        for row in rows:
            sealed = bytes(row.sealed)
            kind = _kind(box, sealed)
            if kind == PREVIOUS and box is not None and reseal:
                try:
                    plaintext = box.open(sealed, owner=column.owner(row))
                except SealError:
                    kind = UNREADABLE
                else:
                    resealed = box.seal(plaintext, owner=column.owner(row))
                    replaced = await connection.execute(
                        column.replace, {"pk": row.pk, "old": sealed, "new": resealed}
                    )
                    report.resealed += replaced.rowcount
                    kind = CURRENT if replaced.rowcount else PREVIOUS
            report.counts[kind] += 1
        # A batch at a time, so a long re-seal never holds one transaction open.
        await connection.commit()
        after = rows[-1].pk


def _kind(box: SealedBox | None, sealed: bytes) -> str:
    kid = sealed_key_id(sealed)
    if box is None or kid is None:
        return UNREADABLE
    if kid == box.current_key_id:
        return CURRENT
    return PREVIOUS if box.holds(kid) else UNREADABLE


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nexovue_api.keys",
        description="What each encryption key holds, and re-sealing it under the current key.",
    )
    parser.add_argument("action", choices=("status", "reseal"))
    return parser


async def _run(action: str) -> list[ColumnReport]:
    keys = Keys.from_environment()
    engine = create_async_engine(postgres_dsn())
    try:
        async with engine.connect() as connection:
            return await survey(connection, keys, reseal=action == "reseal")
    finally:
        await engine.dispose()


def main() -> int:
    arguments = _parser().parse_args()
    reports = asyncio.run(_run(arguments.action))
    for report in reports:
        print(report.line())
    if any(not report.configured and report.counts for report in reports):
        return EXIT_NOT_CONFIGURED
    if any(report.counts[UNREADABLE] for report in reports):
        return EXIT_UNREADABLE
    return 0


if __name__ == "__main__":
    sys.exit(main())
