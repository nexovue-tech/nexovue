"""F4: a tool call's arguments checked against the catalog, and a result projected, masked
and cut before the model reads it (design, sections 2 and 3; FR14, FR40).

Arguments are model output: anything the catalog does not name is refused with a sentence
the model is told, and that counts as a step. A window is two ISO 8601 times, at most seven
days apart, ending no later than now. A service name may hold no character that could move a
route, as if it were a path slot. Only catalog fields of each row are kept, so a field a
route adds later is never sent until someone decides it may be. FR40's send-time masks are
`nexovue_platform.assistant_masks`, which the API's consent preview applies too.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform.assistant_masks import masked_value
from nexovue_platform.assistant_tools import (
    DERIVED_CAP_DAYS,
    MAX_SEARCH_TEXT,
    MAX_WINDOW,
    PATH_SHAPES,
    ArgumentKind,
    ChatTool,
    DataLevel,
    bounded,
    is_missing,
    read_field,
    rows_of,
)

from nexovue_scheduler.insight.api_client import PathRefused, check_path_slot
from nexovue_scheduler.insight.assistant_answer import display_argument

MIN_SEVERITY: Final = 1
MAX_SEVERITY: Final = 24
MAX_SERVICE_NAME: Final = 200
# A metric name, as the route's own path parameter allows (`metrics/router.py`).
MAX_METRIC_NAME: Final = 255
# A window may end a little ahead of the worker's clock, as clocks differ (a guess).
CLOCK_SLACK: Final = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ValidCall:
    """A call the catalog allows: the query to send, the window, what is stored on the step,
    and the one phrase a step may show, if it passed the checks."""

    query: dict[str, str]
    from_time: datetime | None
    to_time: datetime | None
    stored: dict[str, Any]
    display: str | None
    # Route slots, each value checked as a path segment and against its kind's shape.
    path: dict[str, str] = field(default_factory=dict)


class ArgumentsRefused(ValueError):  # noqa: N818 — a refusal, named for the reader
    pass


def _time(name: str, value: object) -> datetime:
    if not isinstance(value, str):
        raise ArgumentsRefused(f"`{name}` must be an ISO 8601 time in UTC.")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ArgumentsRefused(f"`{name}` must be an ISO 8601 time in UTC.") from error
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def check_arguments(
    tool: ChatTool, arguments: Mapping[str, Any] | None, *, level: DataLevel, now: datetime
) -> ValidCall:
    """The call as the catalog allows it, or `ArgumentsRefused` with what to tell the model."""
    if not tool.offered_at(level):
        raise ArgumentsRefused(f"{tool.name} is not offered here.")
    if arguments is None:
        raise ArgumentsRefused("The arguments were not one JSON object.")
    unknown = sorted(set(arguments) - set(tool.arguments))
    if unknown:
        raise ArgumentsRefused(f"{tool.name} takes no argument named {', '.join(unknown)}.")
    missing = sorted(
        n for n, rule in tool.arguments.items() if rule.required and n not in arguments
    )
    if missing:
        raise ArgumentsRefused(f"{tool.name} needs {', '.join(missing)}.")
    query: dict[str, str] = {}
    path: dict[str, str] = {}
    stored: dict[str, Any] = {}
    display: str | None = None
    for name, value in arguments.items():
        rule = tool.arguments[name]
        if rule.kind == "time":
            continue
        if rule.kind in PATH_SHAPES or rule.kind == "metric_name":
            text = _path_value(name, rule.kind, value)
            path[rule.query] = text
            stored[name] = text
            continue
        if rule.kind == "closed":
            if value not in rule.choices:
                raise ArgumentsRefused(f"`{name}` must be one of {', '.join(rule.choices)}.")
            text = str(value)
        elif rule.kind == "severity":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ArgumentsRefused(f"`{name}` must be a whole number.")
            if not MIN_SEVERITY <= value <= MAX_SEVERITY:
                raise ArgumentsRefused(f"`{name}` must be {MIN_SEVERITY} to {MAX_SEVERITY}.")
            text = str(value)
        elif rule.kind == "service_name":
            if not isinstance(value, str) or len(value) > MAX_SERVICE_NAME:
                raise ArgumentsRefused(f"`{name}` must be one service name.")
            _slot(name, value)
            text = value
        else:  # search text
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_SEARCH_TEXT:
                raise ArgumentsRefused(f"`{name}` must be a phrase of 1 to {MAX_SEARCH_TEXT}.")
            # The read runs either way: the phrase goes only to nexovue's own API. It is
            # stored and shown only if it passes the checks; else the step says it is hidden.
            query[rule.query] = value
            display = display_argument(value)
            # Null marks a phrase read but never stored or shown ("search phrase hidden").
            stored[name] = value if display is not None else None
            continue
        if rule.in_path:
            path[rule.query] = text
        else:
            query[rule.query] = text
        stored[name] = value
    from_time = to_time = None
    if tool.takes_window:
        from_time = _time("from", arguments.get("from"))
        to_time = _time("to", arguments.get("to"))
        if from_time >= to_time:
            raise ArgumentsRefused("`from` must be earlier than `to`.")
        if to_time - from_time > MAX_WINDOW:
            raise ArgumentsRefused("A window may be at most 7 days.")
        if to_time > now + CLOCK_SLACK:
            raise ArgumentsRefused("A window may not end in the future.")
        query["from"] = from_time.isoformat()
        query["to"] = to_time.isoformat()
    return ValidCall(query, from_time, to_time, stored, display, path)


def _slot(name: str, value: str) -> None:
    try:
        check_path_slot(value)
    except PathRefused as refused:
        raise ArgumentsRefused(f"`{name}`: {refused}.") from refused


def _path_value(name: str, kind: ArgumentKind, value: object) -> str:
    """An id or a metric name bound for a route's path: refused before any URL exists unless
    it has its kind's exact shape (FR8, AC10)."""
    if not isinstance(value, str):
        raise ArgumentsRefused(f"`{name}` must be text, as a tool returned it.")
    if kind == "metric_name":
        if len(value) > MAX_METRIC_NAME:
            raise ArgumentsRefused(f"`{name}` must be one metric name.")
        _slot(name, value)
        return value
    shape = PATH_SHAPES[kind]
    lowered = value.lower()
    if shape.fullmatch(lowered) is None:
        raise ArgumentsRefused(f"`{name}` must be an id exactly as a tool returned it.")
    _slot(name, lowered)
    return lowered


def kept_since(
    tool: ChatTool, *, raw_days: int, derived_days: int, now: datetime
) -> datetime | None:
    """The oldest moment this tool's route still holds data for, on its own clock (design,
    *The aged-out check*); None for a tool reading records with their own purge."""
    days = {
        "raw": raw_days,
        "derived": derived_days,
        "derived_capped": min(derived_days, DERIVED_CAP_DAYS),
    }.get(tool.retention)
    return None if days is None else now - timedelta(days=days)


def within_retention(call: ValidCall, kept: datetime | None) -> ValidCall | None:
    """The call narrowed to what is still kept: unchanged when all of it is, from `kept` when
    only its start has aged out, or None when none of it is (AC24)."""
    if kept is None or call.from_time is None or call.to_time is None:
        return call
    if call.to_time <= kept:
        return None
    if call.from_time >= kept:
        return call
    return replace(call, from_time=kept, query={**call.query, "from": kept.isoformat()})


@dataclass(frozen=True, slots=True)
class Projected:
    """What the model reads: the bounded envelope as text, and the rows it kept."""

    text: str
    rows: list[dict[str, Any]]
    was_cut: bool


def project(tool: ChatTool, body: bytes, *, mask_emails: bool) -> Projected:
    """Only catalog fields, masked, labelled and cut (design, *The loop*). A body that is not
    the route's shape raises `ValueError`."""
    rows = rows_of(tool, json.loads(body))
    if rows is None:
        raise ValueError("the result was not the route's shape")
    kept = [
        {
            name: masked_value(value, mask_emails=mask_emails)
            for name in tool.fields
            if not is_missing(value := read_field(row, name))
        }
        for row in rows
    ]
    text = bounded(
        kept,
        subject=tool.rows_at,
        untrusted=tool.untrusted_paths(),
        max_rows=tool.max_rows,
        max_characters=tool.max_characters,
    )
    envelope = json.loads(text)
    return Projected(text, list(envelope[tool.rows_at]), "cut" in envelope)
