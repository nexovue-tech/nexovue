"""Adding a connection job, on the caller's own transaction (F22).

A job and what it acts on commit together: the manifest code is sealed and its
conversion job added in one transaction, so the worker can never claim the job before
the code exists, and a refused request leaves neither. The job is added by name, so no
scheduler code is imported (`channels/send_test.py` reached the same answer), with ids
only (AC74). The workers are woken when the transaction commits.
"""

from __future__ import annotations

from nexovue_platform.jobs import ENQUEUE, WAKE, JobRequest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def add(session: AsyncSession, request: JobRequest) -> None:
    await session.execute(text(ENQUEUE), request.parameters())
    await session.execute(text(WAKE), request.wake_parameters())
