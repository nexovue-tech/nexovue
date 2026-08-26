"""Boot-time configuration: complete environments load, incomplete ones stop us."""

from __future__ import annotations

import pytest

from nexovue_api.settings import Settings, SettingsError, get_settings

COMPLETE_ENVIRONMENT = {
    "ENV": "development",
    "POSTGRES_DSN": "postgresql://nexovue:secret@localhost:5432/nexovue",
    "CLICKHOUSE_DSN": "clickhouse://nexovue:secret@localhost:8123/nexovue",
    "REDIS_URL": "redis://localhost:6379/0",
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
    for name in ("postgres_dsn", "clickhouse_dsn", "redis_url"):
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
