"""`tests/helpers/fastapi_caches.py` lets a test's dependency override go when it is done.

FastAPI keeps every dependency callable it has resolved in module-level caches. A test
that overrides a dependency with a closure over its own session would keep that session,
and its engine, alive for thousands of tests after it; this is the measured cause of the
suite's workers growing (docs/changes/test-workers-hold-memory/).
"""

from __future__ import annotations

import gc
import weakref

import httpx
from fastapi import Depends, FastAPI
from fastapi_caches import clear_dependency_caches


class _Held:
    """Stands in for what an override closes over: a session, its engine."""


def _session() -> _Held:
    raise AssertionError("overridden in every test")


async def _request_once_with_an_override() -> weakref.ref[_Held]:
    """Serve one request whose dependency is a closure over a fresh object, then drop
    every reference this function holds to the app, the closure and the object."""
    app = FastAPI()

    @app.get("/")
    def read(held: _Held = Depends(_session)) -> dict[str, str]:  # noqa: B008  FastAPI's form
        return {"held": type(held).__name__}

    held = _Held()
    app.dependency_overrides[_session] = lambda: held
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/")
    assert response.json() == {"held": "_Held"}
    return weakref.ref(held)


async def test_fastapi_keeps_an_override_s_closure_after_its_app_is_gone() -> None:
    """The behaviour the plugin exists for. If a FastAPI upgrade stops keeping the
    callables, this fails, and the plugin can go."""
    held = await _request_once_with_an_override()
    gc.collect()

    assert held() is not None


async def test_emptying_the_caches_lets_the_closure_and_what_it_holds_go() -> None:
    held = await _request_once_with_an_override()

    clear_dependency_caches()
    gc.collect()

    assert held() is None
