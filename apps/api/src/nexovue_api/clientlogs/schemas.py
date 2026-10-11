"""What the console may report, and how much of it.

Anyone can send this — the sign-in screen fails signed out — so every field is
cut to a fixed length rather than refused for being long: a stack trace that is
too long is still worth its first eight kilobytes. Only the batch size and the
severity are refused, because neither has a meaningful truncation.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, BeforeValidator, Field

MAX_RECORDS: Final = 20
MAX_MESSAGE: Final = 1024
MAX_STACK: Final = 8 * 1024
MAX_SHORT: Final = 256
MAX_ATTRIBUTES: Final = 20
MAX_ATTRIBUTE_VALUE: Final = 1024


def _cut(limit: int) -> BeforeValidator:
    return BeforeValidator(lambda value: value[:limit] if isinstance(value, str) else value)


Message = Annotated[str, _cut(MAX_MESSAGE)]
Short = Annotated[str, _cut(MAX_SHORT)]
Stack = Annotated[str, _cut(MAX_STACK)]


def _bounded_attributes(value: Any) -> Any:
    """At most MAX_ATTRIBUTES, each value a scalar, each string cut."""
    if not isinstance(value, dict):
        return value
    kept: dict[str, str | int | float | bool | None] = {}
    for key, item in list(value.items())[:MAX_ATTRIBUTES]:
        if isinstance(item, str):
            item = item[:MAX_ATTRIBUTE_VALUE]
        elif not isinstance(item, int | float | bool) and item is not None:
            item = str(item)[:MAX_ATTRIBUTE_VALUE]
        kept[str(key)[:MAX_SHORT]] = item
    return kept


class ClientError(BaseModel):
    name: Short = ""
    message: Message = ""
    stack: Stack = ""


class ClientLogRecord(BaseModel):
    # `debug` and `info` stay in the browser: they are development detail, and a
    # page could send them at a rate nobody wants in the log.
    severity: Literal["warn", "error"]
    message: Message
    timestamp: Short = ""
    page: Short = ""
    error: ClientError | None = None
    attributes: Annotated[
        dict[str, str | int | float | bool | None], BeforeValidator(_bounded_attributes)
    ] = Field(default_factory=dict)
    trace_id: Short = ""
    correlation_id: Short = ""


class ClientLogBatch(BaseModel):
    records: Annotated[list[ClientLogRecord], Field(min_length=1, max_length=MAX_RECORDS)]
