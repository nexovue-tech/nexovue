"""What a ping is told."""

from __future__ import annotations

from pydantic import BaseModel


class PingReceived(BaseModel):
    ok: bool = True
