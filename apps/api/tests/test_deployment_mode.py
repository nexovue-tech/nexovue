"""Hosted or self-hosted (nexovue_platform.deployment; ready-to-deploy, Step 2).

A customer's own install has no billing, no payment processor and no operator console, and
the console learns which it is from `/system/info`. Self-hosted is what an install is
unless it says otherwise.
"""

from __future__ import annotations

from typing import Any, Final

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from fastapi import FastAPI
from pydantic import ValidationError

from nexovue_api.settings import API_PREFIX, Settings


@pytest.fixture(autouse=True)
def no_processor_from_the_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's `.env` holds a test payment key, which a self-hosted install refuses —
    rightly; these tests say themselves whether a key is present."""
    for name in ("PAYMENTS_DOMESTIC_APP_ID", "PAYMENTS_DOMESTIC_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides: str) -> Settings:
    values: dict[str, Any] = {**TEST_ENVIRONMENT, **overrides}
    return Settings(**values)


def _app(deployment: str) -> FastAPI:
    from nexovue_api.main import create_app

    return create_app(_settings(NEXOVUE_DEPLOYMENT=deployment))


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(API_PREFIX + path)


def test_an_install_that_says_nothing_is_self_hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEXOVUE_DEPLOYMENT", raising=False)
    values = {key: value for key, value in TEST_ENVIRONMENT.items() if key != "NEXOVUE_DEPLOYMENT"}
    settings = Settings(**values)  # type: ignore[arg-type]

    assert settings.nexovue_deployment == "self-hosted"
    assert not settings.is_hosted


def _paths(app: FastAPI) -> set[str]:
    # From the OpenAPI document: FastAPI keeps an included router lazy, so `app.routes`
    # holds the router rather than its paths (test_profile.py says the same).
    return set(app.openapi()["paths"])


BILLING: Final = f"{API_PREFIX}/billing"
OPERATOR_CONSOLE: Final = f"{API_PREFIX}/admin/organizations"
SUPPORT_ACCESS: Final = f"{API_PREFIX}/support-access"


def test_a_self_hosted_install_has_no_billing_no_operator_console_and_no_support_view() -> None:
    paths = _paths(_app("self-hosted"))

    hosted_only = (BILLING, f"{API_PREFIX}/admin/", SUPPORT_ACCESS, f"{API_PREFIX}/support/")
    assert not {path for path in paths if path.startswith(hosted_only)}


def test_the_hosted_service_has_both() -> None:
    paths = _paths(_app("hosted"))

    assert BILLING in paths
    assert OPERATOR_CONSOLE in paths
    assert SUPPORT_ACCESS in paths


@pytest.mark.parametrize("deployment", ["hosted", "self-hosted"])
async def test_the_console_learns_which_install_it_is(deployment: str) -> None:
    info = (await _get(_app(deployment), "/system/info")).json()

    assert info["deployment"] == deployment
    assert {"service", "revision", "build_time"} <= info.keys()


def test_a_self_hosted_install_refuses_a_payment_processor_key() -> None:
    with pytest.raises(ValidationError, match="takes no payments"):
        _settings(NEXOVUE_DEPLOYMENT="self-hosted", PAYMENTS_DOMESTIC_APP_ID="rzp_test_abc")

    hosted = _settings(NEXOVUE_DEPLOYMENT="hosted", PAYMENTS_DOMESTIC_APP_ID="rzp_test_abc")
    assert hosted.payments_domestic_app_id == "rzp_test_abc"
