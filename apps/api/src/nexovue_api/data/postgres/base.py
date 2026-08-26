"""Declarative base and the column shapes every control-plane table shares."""

import uuid
from datetime import datetime
from typing import Annotated, Final

from sqlalchemy import DateTime, MetaData, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Postgres invents constraint names when we do not supply them, which makes a
# downgrade a guessing game. Naming them here keeps every migration reversible.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

UuidType: Final = postgresql.UUID(as_uuid=True)

# `timestamptz` everywhere. A naive timestamp in an observability product is a
# bug waiting for a customer in another timezone.
TimestampTz: Final = DateTime(timezone=True)

# RFC 9562 UUIDv7 is time-ordered, so inserts land at the right-hand edge of the
# index the way a sequence would, while staying globally unique and safe to
# expose. Postgres 18 generates them; the application never supplies an id.
UuidPrimaryKey = Annotated[
    uuid.UUID,
    mapped_column(UuidType, primary_key=True, server_default=text("uuidv7()")),
]

CreatedAt = Annotated[
    datetime,
    mapped_column(TimestampTz, nullable=False, server_default=text("now()")),
]

RequiredTimestamp = Annotated[datetime, mapped_column(TimestampTz, nullable=False)]

# Soft-delete markers, revocations and "last used" all share this shape.
OptionalTimestamp = Annotated[datetime | None, mapped_column(TimestampTz, nullable=True)]


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
