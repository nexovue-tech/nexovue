"""What the deletion endpoint answers with."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class DeletionAccepted(BaseModel):
    """The account is closed as of `deleted_at`; erasure follows and is not awaited.

    Two fields and no progress percentage on purpose. The honest answer to "is
    it gone yet" is a count of rows still in the store, which takes minutes to
    become zero and is not what the person who just closed their account is
    waiting to read.
    """

    organization_id: uuid.UUID
    deleted_at: datetime
