"""`POST /api/v1/client-logs`: the console's warnings and errors, as log lines.

Signed in or not, deliberately: a sign-in screen can fail too, and resolving the
session would extend a support view as a side effect of reporting an error. A
record that came from a failed API call carries that call's correlation id,
which ties it to the API's own line — organization included.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, Response, status
from nexovue_platform.telemetry.logging import get_logger

from nexovue_api.clientlogs.schemas import ClientLogBatch, ClientLogRecord

logger = get_logger("nexovue_web")

SOURCE: Final = "browser"

# Fields every line already carries, which a browser attribute must not overwrite.
_RESERVED: Final = frozenset(
    {
        "event",
        "level",
        "log_level",
        "timestamp",
        "ts",
        "service",
        "env",
        "source",
        "page",
        "browser_timestamp",
        "browser_trace_id",
        "browser_correlation_id",
        "error_name",
        "error_message",
        "error_stack",
        "correlation_id",
        "trace_id",
    }
)

router = APIRouter(prefix="/client-logs", tags=["client logs"])


@router.post(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Record the console's own warnings and errors",
    include_in_schema=False,
)
async def record_client_logs(batch: ClientLogBatch) -> Response:
    for record in batch.records:
        _log(record)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _log(record: ClientLogRecord) -> None:
    fields: dict[str, object] = {
        key: value for key, value in record.attributes.items() if key not in _RESERVED
    }
    fields.update(
        source=SOURCE,
        page=record.page,
        browser_timestamp=record.timestamp,
        browser_trace_id=record.trace_id,
        browser_correlation_id=record.correlation_id,
    )
    if record.error is not None:
        fields.update(
            error_name=record.error.name,
            error_message=record.error.message,
            error_stack=record.error.stack,
        )
    write = logger.error if record.severity == "error" else logger.warning
    write(record.message, **fields)
