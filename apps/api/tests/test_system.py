"""Facts about the deployment, as opposed to about a customer."""

from __future__ import annotations

from environment import TEST_ENVIRONMENT

from nexovue_api.settings import Settings
from nexovue_api.system.router import KEY_HEADER, ingest_endpoint


def _settings(otlp_endpoint: str) -> Settings:
    return Settings(
        env="development",
        postgres_dsn=TEST_ENVIRONMENT["POSTGRES_DSN"],
        clickhouse_dsn=TEST_ENVIRONMENT["CLICKHOUSE_DSN"],
        otlp_endpoint=otlp_endpoint,
    )


class TestWhereTelemetryGoes:
    """The answer was reachable exactly once.

    Signup returned `otlp_endpoint` and the onboarding screen printed it. Nothing
    else in the product ever showed it again — not the projects list, not the
    API-keys screen, not the key-create response — so a customer who closed that
    tab could not recover the one value their exporter needs, and no screen could
    tell them where to look.
    """

    async def test_it_names_the_endpoint_this_deployment_serves(self) -> None:
        answer = await ingest_endpoint(_settings("https://ingest.nexovue.com"))

        assert answer.otlp_endpoint == "https://ingest.nexovue.com"

    async def test_it_names_the_header_rather_than_leaving_a_screen_to_spell_it(self) -> None:
        """Returned rather than hard-coded in the console, so one spelling cannot
        drift from the receiver's."""
        answer = await ingest_endpoint(_settings("https://ingest.nexovue.com"))

        assert answer.key_header == KEY_HEADER == "x-nexovue-key"

    async def test_it_hands_back_a_base_url_rather_than_a_signal_path(self) -> None:
        """An SDK appends `/v1/traces` itself. A customer who pastes the path too
        gets `/v1/traces/v1/traces` and a 404 naming neither mistake."""
        answer = await ingest_endpoint(_settings("https://ingest.nexovue.com"))

        assert not answer.otlp_endpoint.rstrip("/").endswith("/v1/traces")
