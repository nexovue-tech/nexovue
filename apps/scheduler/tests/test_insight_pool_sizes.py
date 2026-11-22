"""The pools the AI role opens are the ones its account's connection limit was worked out
for (F3 A58): `insight_account.CHILD_CONNECTIONS` and `PARENT_CONNECTIONS` name them, and
nothing else holds them to the code that opens the pools."""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from typing import Any

from pytest import MonkeyPatch

import nexovue_scheduler.__main__ as scheduler_main
import nexovue_scheduler.process_engine as process_engine
from nexovue_api import insight_account


def _pool_arguments(source: str) -> dict[str, int]:
    """`pool_size` and `max_overflow` of the one `create_async_engine` call in `source`."""
    (call,) = (
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", "")) == "create_async_engine"
    )
    return {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in call.keywords
        if keyword.arg in ("pool_size", "max_overflow")
    }


def test_a_child_holds_the_connections_the_limit_counts(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(process_engine, "_engine", None)
    monkeypatch.setattr(process_engine, "_loop", None)
    monkeypatch.setattr(
        process_engine, "get_settings", lambda: SimpleNamespace(postgres_dsn="postgresql://x/y")
    )
    monkeypatch.setattr(
        process_engine,
        "create_async_engine",
        lambda *_, **keywords: captured.update(keywords) or object(),
    )

    async def nothing(_engine: object) -> None:
        return None

    process_engine.with_engine(nothing)

    assert captured["pool_size"] + captured["max_overflow"] == insight_account.CHILD_CONNECTIONS


def test_the_parent_holds_its_claim_pool_and_one_listener() -> None:
    pool = _pool_arguments(inspect.getsource(scheduler_main._worker))
    listener = 1

    assert pool["pool_size"] + pool["max_overflow"] + listener == insight_account.PARENT_CONNECTIONS
