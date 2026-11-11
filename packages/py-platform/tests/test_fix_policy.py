"""Which files may enter a pack, and which a suggestion may change (F5 plan T2.1; spec
FR12, K2, BR7, D9; AC12's list forms)."""

from __future__ import annotations

import pytest
from nexovue_platform.fixes.policy import (
    CHECKS_VERSION,
    OwnerLists,
    has_source_extension,
    is_never_changed,
    never_sent_reason,
)

# AC12's list forms, each with the reason it is kept back.
AC12_FORMS = [
    ("prod.env", "secret_or_key"),
    (".ENV", "secret_or_key"),
    ("services/a/.github/x", "ci"),
    ("id_ed25519", "secret_or_key"),
    ("config/prod.py", "configuration"),
    ("application-prod.yml", "not_source"),
]


@pytest.mark.parametrize(("path", "reason"), AC12_FORMS)
def test_ac12_each_list_form_is_kept_out_of_a_pack(path: str, reason: str) -> None:
    assert never_sent_reason(path) == reason


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("deploy/keys/server.PEM", "secret_or_key"),
        ("src/secrets/rotate.py", "secret_or_key"),
        ("svc/.env.production", "secret_or_key"),
        ("svc/.docker/config.json", "secret_or_key"),
        ("svc/Jenkinsfile", "ci"),
        ("svc/.circleci/config.yml", "ci"),
        ("services/a/AGENTS.md", "agent_instructions"),
        ("services/a/.claude/settings.py", "agent_instructions"),
        ("src/app_settings.py", "configuration"),
        ("src/production/handlers.py", "configuration"),
        ("infra/main.tf", "not_source"),
        ("appsettings.json", "not_source"),
    ],
)
def test_hard_and_configuration_lists_match_at_any_depth(path: str, reason: str) -> None:
    assert never_sent_reason(path) == reason


@pytest.mark.parametrize(
    "path",
    ["src/checkout/pay.ts", "services/billing/tax.py", "cmd/api/main.go", "Pay.java", "a/b.KT"],
)
def test_near_misses_source_files_may_enter(path: str) -> None:
    assert never_sent_reason(path) is None


def test_an_owner_may_allow_a_named_configuration_path_and_nothing_harder() -> None:
    owner = OwnerLists(allowed_config_paths=("/config/prod.py", "src/secrets/rotate.py"))

    assert never_sent_reason("Config/Prod.py", owner) is None
    assert never_sent_reason("config/other.py", owner) == "configuration"
    # The hard list cannot be loosened.
    assert never_sent_reason("src/secrets/rotate.py", owner) == "secret_or_key"


def test_an_owner_allowance_lets_a_file_be_read_never_changed() -> None:
    owner = OwnerLists(allowed_config_paths=("config/prod.py",))

    assert never_sent_reason("config/prod.py", owner) is None
    assert is_never_changed("config/prod.py", owner)


def test_extra_excluded_paths_add_to_both_hard_lists() -> None:
    owner = OwnerLists(extra_excluded_paths=("src/billing/legacy.py",))

    assert never_sent_reason("SRC/billing/legacy.py", owner) == "excluded_by_owner"
    assert is_never_changed("src/billing/legacy.py", owner)
    assert never_sent_reason("src/billing/legacy2.py", owner) is None


@pytest.mark.parametrize(
    "path",
    [
        "services/a/tests/conftest.py",  # AC17: a nested conftest.py
        "CODEOWNERS",  # AC17: a root CODEOWNERS
        "docs/CODEOWNERS",
        "package.json",
        "web/pnpm-lock.yaml",
        "requirements-dev.txt",
        "svc/Dockerfile.prod",
        "deploy/compose.override.yaml",
        "web/vite.config.ts",
        ".github/workflows/ci.yml",
        ".husky/pre-commit",
        "svc/.vscode/launch.json",
        "build.gradle.kts",
    ],
)
def test_k2_never_changed_list(path: str) -> None:
    assert is_never_changed(path)


@pytest.mark.parametrize(
    "path", ["src/checkout/pay.ts", "tests/test_pay.py", "src/build_report.py", "src/docker.py"]
)
def test_k2_near_misses_may_be_changed(path: str) -> None:
    assert not is_never_changed(path)


def test_extension_is_read_from_the_file_name_only() -> None:
    assert has_source_extension("src.py/readme") is False
    assert has_source_extension("src/readme.md") is False
    assert has_source_extension("src/handler.tsx")


def test_checks_version_is_stated() -> None:
    assert CHECKS_VERSION
