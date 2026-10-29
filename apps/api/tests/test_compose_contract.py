"""Every install layout, as Docker Compose itself renders it (docs/changes/ready-to-deploy, T13).

nexovue.yaml is the self-hosted install and the base the overlays change: hosted, the quickstart
and the replica's local build. A raw file cannot show what an overlay does to it, so these ask
`docker compose config` — Docker is required, as it is for `just check`'s live suites, and its
absence fails here rather than skipping.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

import nexovue_api

COMPOSE: Final = Path(nexovue_api.__file__).parents[4] / "deploy" / "compose"

LAYOUTS: Final[dict[str, tuple[str, ...]]] = {
    "self-hosted": ("nexovue.yaml",),
    "hosted": ("nexovue.yaml", "nexovue.hosted.yaml"),
    "quickstart": ("nexovue.yaml", "nexovue.quickstart.yaml"),
    "replica": ("nexovue.yaml", "nexovue.hosted.yaml", "nexovue.build.yaml"),
    "three-brokers": ("nexovue.yaml", "nexovue.kafka3.yaml"),
}
DEPLOYMENT: Final = {
    "self-hosted": "self-hosted",
    "hosted": "hosted",
    "quickstart": "self-hosted",
    "replica": "hosted",
    "three-brokers": "self-hosted",
}
ONE_SHOT: Final = frozenset({"setup", "setup-pipeline", "web-assets-init"})
OURS: Final = ("ghcr.io/nexovue-tech/nexovue:", "ghcr.io/nexovue-tech/nexovue-pipeline:")

# What `nexovue install` writes, with stand-in values: the variables every layout demands.
SETTINGS: Final = {
    "NEXOVUE_VERSION": "1.2.3",
    "POSTGRES_DB": "nexovue",
    "POSTGRES_USER": "nexovue",
    "POSTGRES_PASSWORD": "postgres-secret",
    "CLICKHOUSE_DB": "nexovue",
    "CLICKHOUSE_USER": "nexovue",
    "CLICKHOUSE_PASSWORD": "clickhouse-secret",
    "CLICKHOUSE_READER_PASSWORD": "reader-secret",
    "CLICKHOUSE_ERASER_PASSWORD": "eraser-secret",
    "KAFKA_CLUSTER_ID": "fo8KHCs9Tl-KmwwdLj9KWw",
    "WORKERS_MASKING_HASH_SECRET": "masking-secret",
    "CHECK_SECRET_KEY": "check-key",
    "CREDENTIAL_ENCRYPTION_KEY": "credential-key",
    "S3_BUCKET": "nexovue",
    "S3_ACCESS_KEY_ID": "install-access-key",
    "S3_SECRET_ACCESS_KEY": "install-secret-key",
    "NEXOVUE_APP_HOST": "nexovue.example.com",
    "NEXOVUE_INGEST_HOST": "ingest.nexovue.example.com",
    "NEXOVUE_TLS_EMAIL": "ops@example.com",
}


def _render(
    layout: str, tmp_path: Path, *, profiles: tuple[str, ...] = (), **extra: str
) -> dict[str, Any]:
    settings = tmp_path / f"{layout}.env"
    settings.write_text("".join(f"{k}={v}\n" for k, v in (SETTINGS | extra).items()))
    files = [argument for name in LAYOUTS[layout] for argument in ("-f", str(COMPOSE / name))]
    chosen = [argument for profile in profiles for argument in ("--profile", profile)]
    rendered = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(settings),
            *files,
            *chosen,
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert rendered.returncode == 0, f"{layout} does not render:\n{rendered.stderr}"
    services: dict[str, Any] = json.loads(rendered.stdout)["services"]
    return services


@pytest.fixture(params=sorted(LAYOUTS))
def layout(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def test_only_the_edge_publishes_a_port(layout: str, tmp_path: Path) -> None:
    services = _render(layout, tmp_path, profiles=("monitoring",))

    published = sorted(name for name, service in services.items() if service.get("ports"))

    assert published == ["proxy"], f"{layout}: {published} publish ports"


def test_every_long_running_service_restarts_and_has_a_healthcheck(
    layout: str, tmp_path: Path
) -> None:
    services = _render(layout, tmp_path, profiles=("monitoring",))

    unchecked = sorted(
        name
        for name, service in services.items()
        if name not in ONE_SHOT and "healthcheck" not in service
    )
    not_restarting = sorted(
        name
        for name, service in services.items()
        if name not in ONE_SHOT and service.get("restart") != "unless-stopped"
    )

    assert not unchecked, f"{layout}: no healthcheck on {unchecked}"
    assert not not_restarting, f"{layout}: a reboot would not bring back {not_restarting}"


def test_images_are_ours_by_version_or_pinned(layout: str, tmp_path: Path) -> None:
    services = _render(layout, tmp_path, profiles=("monitoring",))

    for name, service in services.items():
        image = service["image"]
        if image.startswith(OURS):
            assert image.endswith(":1.2.3"), f"{layout}: {name} runs {image}, not the version"
        else:
            tag = image.rsplit(":", 1)[-1] if ":" in image.rsplit("/", 1)[-1] else ""
            assert tag and tag != "latest", f"{layout}: {name} runs {image}, which can move"


def test_the_deployment_mode_is_the_layout_s(layout: str, tmp_path: Path) -> None:
    services = _render(layout, tmp_path)

    modes = {
        name: service["environment"]["NEXOVUE_DEPLOYMENT"]
        for name, service in services.items()
        if "NEXOVUE_DEPLOYMENT" in (service.get("environment") or {})
    }

    assert set(modes) >= {"api", "receiver", "scheduler-beat", "scheduler-light"}
    assert set(modes.values()) == {DEPLOYMENT[layout]}, modes


def test_no_layout_carries_the_repository_s_development_keys(layout: str, tmp_path: Path) -> None:
    rendered = json.dumps(_render(layout, tmp_path))

    for development in ("nexovue_dev_access", "nexovue_dev_secret", "nexovue_dev"):
        assert development not in rendered, f"{layout} carries {development}"


def test_the_quickstart_is_plain_http_on_localhost_only(tmp_path: Path) -> None:
    services = _render("quickstart", tmp_path)

    ports = {(port["host_ip"], port["published"]) for port in services["proxy"]["ports"]}
    assert ports == {("127.0.0.1", "8080"), ("127.0.0.1", "4318")}
    api = services["api"]["environment"]
    assert api["APP_BASE_URL"] == "http://localhost:8080"
    assert api["OTLP_ENDPOINT"] == "http://localhost:4318"
    assert services["proxy"]["environment"]["NEXOVUE_SITE_SCHEME"] == "http"


@pytest.mark.parametrize("layout_name", ["self-hosted", "hosted"])
def test_a_real_install_is_production_over_tls(layout_name: str, tmp_path: Path) -> None:
    services = _render(layout_name, tmp_path)

    environments = {
        name: service["environment"]["ENV"]
        for name, service in services.items()
        if "ENV" in (service.get("environment") or {})
    }
    assert set(environments.values()) == {"production"}, environments
    assert {port["published"] for port in services["proxy"]["ports"]} == {"80", "443"}
    assert "NEXOVUE_SITE_SCHEME" not in services["proxy"]["environment"]


def test_the_replica_builds_every_image_it_runs_of_ours(tmp_path: Path) -> None:
    services = _render("replica", tmp_path)

    unbuilt = sorted(
        name
        for name, service in services.items()
        if service["image"].startswith(OURS) and "build" not in service
    )
    assert not unbuilt, f"the replica would pull {unbuilt} instead of building them"


def test_an_offline_install_never_pulls_our_images(tmp_path: Path) -> None:
    services = _render("self-hosted", tmp_path, NEXOVUE_PULL_POLICY="never")

    policies = {
        name: service.get("pull_policy")
        for name, service in services.items()
        if service["image"].startswith(OURS)
    }
    assert set(policies.values()) == {"never"}, policies


@pytest.mark.parametrize(
    "overlay", ["nexovue.hosted.yaml", "nexovue.build.yaml", "nexovue.kafka3.yaml"]
)
def test_an_overlay_adds_no_list_to_a_service_it_shares(overlay: str) -> None:
    """`-f` appends lists, so on a service nexovue.yaml already runs an overlay changes settings
    only. A service of its own (the extra brokers) is whole. The quickstart replaces its ports with
    `!override` and is held by its own test above."""
    base = set(yaml.safe_load((COMPOSE / "nexovue.yaml").read_text())["services"])
    shared = {
        name: service
        for name, service in yaml.safe_load((COMPOSE / overlay).read_text())["services"].items()
        if name in base
    }

    lists = sorted(
        f"{name}.{key}"
        for name, service in shared.items()
        for key, value in service.items()
        if isinstance(value, list)
    )
    assert not lists, f"{overlay} appends {lists}"


def test_the_monitor_runs_only_when_asked_for(tmp_path: Path) -> None:
    assert "prometheus" not in _render("self-hosted", tmp_path)

    services = _render("self-hosted", tmp_path, profiles=("monitoring",))

    assert {"prometheus", "alertmanager"} <= set(services)
