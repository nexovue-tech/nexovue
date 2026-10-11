"""The heartbeat URL answers GET and POST as two operations (group 9, A6).

One `api_route` taking both methods publishes one operation id twice: FastAPI
derives it once per route and warns while building the schema. The billing
router's checkout return is split for the same reason.
"""

from __future__ import annotations

import warnings

from fastapi import FastAPI

from nexovue_api.heartbeats import router as heartbeats_router


def test_get_and_post_are_two_operations_with_their_own_ids() -> None:
    app = FastAPI()
    app.include_router(heartbeats_router.router)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        schema = app.openapi()

    path = schema["paths"]["/heartbeat/{token}"]
    assert set(path) == {"get", "post"}
    assert path["get"]["operationId"] != path["post"]["operationId"]
