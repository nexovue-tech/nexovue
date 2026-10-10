"""Boot-time configuration: complete environments load, incomplete ones stop us."""

from __future__ import annotations

import pytest
from environment import TEST_ENVIRONMENT
from pydantic import ValidationError

from nexovue_api.settings import (
    DEFAULT_OTLP_ENDPOINT,
    Settings,
    SettingsError,
    get_settings,
)

COMPLETE_ENVIRONMENT = {
    "ENV": "development",
    "POSTGRES_DSN": "postgresql://nexovue:secret@localhost:5432/nexovue",
    "CLICKHOUSE_DSN": "clickhouse://nexovue_reader:secret@localhost:8123/nexovue",
    # Erasure's own credential (docs/changes/clickhouse-protects-itself): required,
    # so a deployment cannot quietly run every read as an account that can delete.
    "CLICKHOUSE_WRITER_DSN": "clickhouse://nexovue:secret@localhost:8123/nexovue",
}

KNOWN_VARIABLES = (
    *COMPLETE_ENVIRONMENT,
    "LOG_LEVEL",
    "LOG_FORMAT",
    "POSTGRES_POOL_SIZE",
    "CLICKHOUSE_MAX_RESULT_ROWS",
    "RATE_LIMIT_ENABLED",
    "TRUSTED_PROXY_COUNT",
    "CORS_ALLOWED_ORIGINS",
)


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from an environment with none of our variables set."""
    for name in KNOWN_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def _apply(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_minimum_environment_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch, COMPLETE_ENVIRONMENT)

    settings = get_settings()

    assert settings.env == "development"
    assert settings.log_format == "json", "production-safe default"
    assert settings.postgres_pool_size > 0, "the pool must be sized, never left to chance"


@pytest.mark.parametrize("missing", list(COMPLETE_ENVIRONMENT))
def test_missing_required_variable_is_named(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    _apply(monkeypatch, {k: v for k, v in COMPLETE_ENVIRONMENT.items() if k != missing})

    with pytest.raises(SettingsError) as failure:
        get_settings()

    assert missing in str(failure.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ENV", "prod"),
        ("LOG_LEVEL", "verbose"),
        ("LOG_FORMAT", "logfmt"),
        ("CLICKHOUSE_DSN", "http://localhost:8123/nexovue"),
        ("CLICKHOUSE_WRITER_DSN", "http://localhost:8123/nexovue"),
        ("POSTGRES_DSN", "mysql://localhost:3306/nexovue"),
        ("POSTGRES_POOL_SIZE", "0"),
        ("CLICKHOUSE_MAX_RESULT_ROWS", "0"),
    ],
)
def test_malformed_value_is_named(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    _apply(monkeypatch, {**COMPLETE_ENVIRONMENT, name: value})

    with pytest.raises(SettingsError) as failure:
        get_settings()

    assert name in str(failure.value)


def test_no_secret_carries_a_default() -> None:
    """A credential with a default is a credential that will be used by accident."""
    for name in ("postgres_dsn", "clickhouse_dsn"):
        assert Settings.model_fields[name].is_required(), f"{name} must have no default"


def test_clickhouse_dsn_is_split_into_connection_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _apply(monkeypatch, COMPLETE_ENVIRONMENT)

    settings = get_settings()

    assert settings.clickhouse_host == "localhost"
    assert settings.clickhouse_port == 8123
    assert settings.clickhouse_database == "nexovue"
    assert settings.clickhouse_secure is False


class TestTheEndpointACustomerIsHanded:
    """`otlp_endpoint` is printed on the onboarding screen of every new account.

    Its default is localhost, which is correct on a laptop and a silent disaster
    anywhere else: the customer sets two variables, their exporter posts to their
    own machine, the screen waits for a span that cannot arrive, and no request
    reaches us to say so. They conclude the product does not work.
    """

    def test_development_keeps_the_localhost_default(self) -> None:
        assert _settings(env="development").otlp_endpoint.startswith("http://localhost")

    @pytest.mark.parametrize("env", ["staging", "production"])
    @pytest.mark.parametrize("endpoint", ["http://localhost:14318", "http://127.0.0.1:14318"])
    def test_a_deployed_install_refuses_to_start_on_an_address_only_we_can_reach(
        self, env: str, endpoint: str
    ) -> None:
        """At start-up rather than at the first signup. A deployment that is wrong
        should refuse to run, not wait to be wrong in front of somebody."""
        with pytest.raises(ValidationError) as refusal:
            _settings(env=env, otlp_endpoint=endpoint)

        assert "no customer can reach" in str(refusal.value)

    def test_a_real_address_is_accepted(self) -> None:
        deployed = _settings(env="production", otlp_endpoint="https://ingest.nexovue.com")

        assert deployed.otlp_endpoint == "https://ingest.nexovue.com"


def _settings(*, env: str, otlp_endpoint: str = DEFAULT_OTLP_ENDPOINT) -> Settings:
    """`Settings` for a named environment, spelled out rather than splatted.

    `Settings` also takes pydantic-settings' own keyword-only parameters, and a
    `**dict[str, str]` cannot type-check against those — it reports one error per
    parameter it could have been.
    """
    return Settings(
        env=env,
        postgres_dsn=TEST_ENVIRONMENT["POSTGRES_DSN"],
        clickhouse_dsn=TEST_ENVIRONMENT["CLICKHOUSE_DSN"],
        clickhouse_writer_dsn=TEST_ENVIRONMENT["CLICKHOUSE_WRITER_DSN"],
        otlp_endpoint=otlp_endpoint,
    )
