"""Stored facts: a `Facts` value as JSON and back, so `rules.decide` can be replayed offline
(plan T2.15; AC62: "each run must store its facts").

Generic over the dataclass types in `nexovue_scheduler.evidence.rules`, driven by their type
hints, so a field added there is stored and restored with no edit here. A value that is not a
type this knows is an error, never a silent drop: a replay that lost a field would score the
wrong thing.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any, Literal, get_args, get_origin, get_type_hints
from zoneinfo import ZoneInfo

from nexovue_scheduler.evidence.rules import Facts


def encode(value: Any) -> Any:
    """`value` as plain JSON types. Every time keeps its zone."""
    if is_dataclass(value) and not isinstance(value, type):
        return {each.name: encode(getattr(value, each.name)) for each in fields(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("a stored time carries its zone")
        return value.isoformat()
    if isinstance(value, ZoneInfo):
        return value.key
    if isinstance(value, Mapping):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, frozenset | set):
        return sorted(encode(item) for item in value)
    if isinstance(value, tuple | list):
        return [encode(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"cannot store a {type(value).__name__}")


def decode(hint: Any, value: Any) -> Any:
    """`value` as the type `hint` names; the inverse of `encode`."""
    origin = get_origin(hint)
    arguments = get_args(hint)
    if hint is Any:
        return value
    if origin in (typing.Union, types.UnionType):
        if value is None:
            return None
        [inner] = [each for each in arguments if each is not type(None)]
        return decode(inner, value)
    if origin is Literal:
        if value not in arguments:
            raise ValueError(f"{value!r} is not one of {arguments}")
        return value
    if origin is tuple:
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return tuple(decode(arguments[0], item) for item in value)
        if len(arguments) != len(value):
            raise ValueError(f"expected {len(arguments)} items, got {len(value)}")
        return tuple(decode(each, item) for each, item in zip(arguments, value, strict=True))
    if origin is frozenset:
        return frozenset(decode(arguments[0], item) for item in value)
    if origin in (Mapping, dict):
        return {key: decode(arguments[1], item) for key, item in value.items()}
    if hint is datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError(f"time {value!r} has no zone")
        return parsed
    if hint is ZoneInfo:
        return ZoneInfo(value)
    if hint is float:
        return None if value is None else float(value)
    if hint in (str, int, bool):
        if not isinstance(value, hint):
            raise TypeError(f"expected {hint.__name__}, got {value!r}")
        return value
    if isinstance(hint, type) and is_dataclass(hint):
        hints = get_type_hints(hint)
        known = {each.name for each in fields(hint)}
        if set(value) - known:
            raise ValueError(f"unknown fields for {hint.__name__}: {sorted(set(value) - known)}")
        return hint(**{name: decode(hints[name], item) for name, item in value.items()})
    raise TypeError(f"cannot restore a {hint}")


def facts_to_json(facts: Facts) -> dict[str, Any]:
    stored: dict[str, Any] = encode(facts)
    return stored


def facts_from_json(raw: Mapping[str, Any]) -> Facts:
    restored: Facts = decode(Facts, dict(raw))
    return restored
