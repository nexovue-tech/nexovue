"""The file shapes. The bytes never come back through the API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from pydantic import BaseModel

# Datadog's documented limit, taken as the market reference (spec FR4, D15).
MAX_FILE_BYTES: Final = 3 * 1024 * 1024
MAX_FILE_NAME_LENGTH: Final = 255
DEFAULT_CONTENT_TYPE: Final = "application/octet-stream"


class CheckFileSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    check_id: uuid.UUID | None
    name: str
    content_type: str
    size_bytes: int
    created_at: datetime
