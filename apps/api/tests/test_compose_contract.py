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
from nexovue_api.roles import command_for
from nexovue_scheduler import releases, slos
from nexovue_scheduler.__main__ import worker_queues
from nexovue_scheduler.detection import DETECTION_QUEUE
from nexovue_scheduler.jobs.worker import QUEUE_CAPS

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
    "CLICKHOUSE_DETECTION_WRITER_PASSWORD": "detection-writer-secret",
    "KAFKA_CLUSTER_ID": "fo8KHCs9Tl-KmwwdLj9KWw",
    "WORKERS_MASKING_HASH_SECRET": "masking-secret",
    "CHECK_SECRET_KEY": "check-key",
    "CREDENTIAL_ENCRYPTION_KEY": "credential-key",
    "CONNECTION_SECRET_KEY": "connection-key",
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
    rendered = _compose_config(layout, tmp_path, SETTINGS | extra, profiles)
    assert rendered.returncode == 0, f"{layout} does not render:\n{rendered.stderr}"
    services: dict[str, Any] = json.loads(rendered.stdout)["services"]
    return services


def _compose_config(
    layout: str, tmp_path: Path, values: dict[str, str], profiles: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    settings = tmp_path / f"{layout}.env"
    settings.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    files = [argument for name in LAYOUTS[layout] for argument in ("-f", str(COMPOSE / name))]
    chosen = [argument for profile in profiles for argument in ("--profile", profile)]
    return subprocess.run(
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


@pytest.fixture(params=sorted(LAYOUTS))
def layout(request: pytest.FixtureRequest) -> str:
    return str(request.param)


# Every optional service, so the general rules below hold for them too.
EVERY_PROFILE: Final = ("monitoring", "connections")


def test_only_the_edge_publishes_a_port(layout: str, tmp_path: Path) -> None:
    services = _render(layout, tmp_path, profiles=EVERY_PROFILE)

    published = sorted(name for name, service in services.items() if service.get("ports"))

    assert published == ["proxy"], f"{layout}: {published} publish ports"


def test_every_long_running_service_restarts_and_has_a_healthcheck(
    layout: str, tmp_path: Path
) -> None:
    services = _render(layout, tmp_path, profiles=EVERY_PROFILE)

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
    services = _render(layout, tmp_path, profiles=EVERY_PROFILE)

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


WRITER_ACCOUNT_FILE: Final = "/etc/clickhouse-server/users.d/detection-writer.xml"


def test_the_detection_writer_is_provisioned_and_handed_to_one_worker(
    layout: str, tmp_path: Path
) -> None:
    """ADR-023, D32: the account exists in the store, and only `scheduler-light` holds it."""
    services = _render(layout, tmp_path, profiles=("monitoring",))

    clickhouse = services["clickhouse"]
    assert clickhouse["environment"]["CLICKHOUSE_DETECTION_WRITER_PASSWORD"] == (
        "detection-writer-secret"
    )
    assert WRITER_ACCOUNT_FILE in {volume["target"] for volume in clickhouse["volumes"]}
    holders = {
        name: service["environment"]["CLICKHOUSE_DETECTION_WRITER_DSN"]
        for name, service in services.items()
        if "CLICKHOUSE_DETECTION_WRITER_DSN" in (service.get("environment") or {})
    }
    assert set(holders) == {"scheduler-light"}, f"{layout}: {sorted(holders)} hold the writer"
    assert "//nexovue_detection_writer:detection-writer-secret@" in holders["scheduler-light"]


def test_an_install_without_the_writer_password_refuses_to_start(tmp_path: Path) -> None:
    """`${…:?}`: an empty password would start the account with none (design 2.1, m5)."""
    values = {k: v for k, v in SETTINGS.items() if k != "CLICKHOUSE_DETECTION_WRITER_PASSWORD"}

    rendered = _compose_config("self-hosted", tmp_path, values)

    assert rendered.returncode != 0
    assert "set CLICKHOUSE_DETECTION_WRITER_PASSWORD" in rendered.stderr


def _queues_of(argv: list[str], region: str) -> list[str]:
    """What a scheduler worker started with `argv` claims from (`__main__.py`)."""
    named = argv[argv.index("--queues") + 1] if "--queues" in argv else ""
    return worker_queues(named, region=region)


def test_scheduler_light_judges_detection_beside_its_probes(layout: str, tmp_path: Path) -> None:
    """C23, D32: `scheduler-light` takes the `detection` queue, capped there at two of
    its slots, and still takes its own location's probes. No other worker takes it."""
    services = _render(layout, tmp_path)

    queues = {
        name: _queues_of(
            command_for(service["command"][0], service["command"][1:]),
            service["environment"]["PROBER_REGION"],
        )
        for name, service in services.items()
        if name.startswith("scheduler-") and name != "scheduler-beat"
    }

    region = services["scheduler-light"]["environment"]["PROBER_REGION"]
    assert DETECTION_QUEUE in queues["scheduler-light"]
    assert f"light.{region}" in queues["scheduler-light"], "the location's probes were dropped"
    others = [name for name, taken in queues.items() if DETECTION_QUEUE in taken]
    assert others == ["scheduler-light"], f"{layout}: {others} take detection jobs"


def _floor_of(argv: list[str]) -> int:
    return int(argv[argv.index("--uncapped-floor") + 1]) if "--uncapped-floor" in argv else 0


def _concurrency_of(argv: list[str]) -> int:
    return int(argv[argv.index("--concurrency") + 1])


def test_slo_work_runs_on_scheduler_light_under_its_floor(layout: str, tmp_path: Path) -> None:
    """F19 D66, AC68, NFR2: the three SLO queues are `scheduler-light`'s and no other
    worker's, each capped, and every capped queue together sits under a floor that
    leaves probes and alert sweeps 4 of its 8 slots. No `scheduler-slo` exists."""
    services = _render(layout, tmp_path)
    assert "scheduler-slo" not in services, f"{layout}: a scheduler-slo service exists"

    argv = {
        name: command_for(service["command"][0], service["command"][1:])
        for name, service in services.items()
        if name.startswith("scheduler-") and name != "scheduler-beat"
    }
    queues = {
        name: _queues_of(arguments, services[name]["environment"]["PROBER_REGION"])
        for name, arguments in argv.items()
    }
    light = argv["scheduler-light"]
    for queue_name in slos.QUEUES:
        assert queue_name in queues["scheduler-light"]
        assert QUEUE_CAPS[queue_name] == slos.QUEUE_CAPS[queue_name]
        others = [name for name, taken in queues.items() if queue_name in taken]
        assert others == ["scheduler-light"], f"{layout}: {others} take {queue_name}"
    assert (_concurrency_of(light), _floor_of(light)) == (8, 4)
    capped = [name for name in queues["scheduler-light"] if name in QUEUE_CAPS]
    assert set(capped) >= {DETECTION_QUEUE, *slos.QUEUES, *releases.QUEUES}


def test_the_release_sync_runs_on_scheduler_light_capped_under_its_floor(
    layout: str, tmp_path: Path
) -> None:
    """F28 D31, AC72: the `releases` queue is `scheduler-light`'s and no other worker's,
    capped at two seats inside the floor that keeps probes and alert sweeps 4 of 8."""
    services = _render(layout, tmp_path)
    argv = {
        name: command_for(service["command"][0], service["command"][1:])
        for name, service in services.items()
        if name.startswith("scheduler-") and name != "scheduler-beat"
    }
    queues = {
        name: _queues_of(arguments, services[name]["environment"]["PROBER_REGION"])
        for name, arguments in argv.items()
    }
    for queue_name in releases.QUEUES:
        assert QUEUE_CAPS[queue_name] == releases.QUEUE_CAPS[queue_name] == 2
        others = [name for name, taken in queues.items() if queue_name in taken]
        assert others == ["scheduler-light"], f"{layout}: {others} take {queue_name}"
    assert (_concurrency_of(argv["scheduler-light"]), _floor_of(argv["scheduler-light"])) == (8, 4)


def test_the_development_stack_s_scheduler_light_judges_detection_too() -> None:
    """The dev stack runs the worker by its own command line, not by role."""
    dev = yaml.safe_load((COMPOSE / "docker-compose.yaml").read_text(encoding="utf-8"))
    workers = {
        name: _queues_of(
            service["command"].split(), service["environment"].get("PROBER_REGION", "local")
        )
        for name, service in dev["services"].items()
        if name in {"scheduler-light", "scheduler-deliveries"}
    }

    assert DETECTION_QUEUE in workers["scheduler-light"]
    assert "light.local" in workers["scheduler-light"]
    assert set(slos.QUEUES) <= set(workers["scheduler-light"])
    assert set(releases.QUEUES) <= set(workers["scheduler-light"])
    assert _floor_of(dev["services"]["scheduler-light"]["command"].split()) == 4
    assert DETECTION_QUEUE not in workers["scheduler-deliveries"]


# ------------------------------------------------------- F22: scheduler-connections

CONNECTIONS_ROLE: Final = "scheduler-connections"
# AC33: what the role holds, and nothing else. No Kafka, no check key, no mail, no object
# store: it opens tracker keys and calls trackers, and a compromise of it reaches no more.
CONNECTIONS_ROLE_VARIABLES: Final = frozenset(
    {
        "ENV",
        "NEXOVUE_DEPLOYMENT",
        "LOG_LEVEL",
        "LOG_FORMAT",
        "SCHEDULER_ROLE",
        "POSTGRES_DSN",
        "APP_BASE_URL",
        "CONNECTION_SECRET_KEY",
        "CONNECTION_SECRET_PREVIOUS_KEYS",
    }
)


def test_the_connections_role_runs_only_where_its_profile_is_on(tmp_path: Path) -> None:
    """AC118: `NEXOVUE_CONNECTIONS=no` leaves the profile off, and Compose starts no role."""
    assert CONNECTIONS_ROLE not in _render("self-hosted", tmp_path)
    assert CONNECTIONS_ROLE in _render("self-hosted", tmp_path, profiles=("connections",))


@pytest.mark.parametrize("layout_name", ["self-hosted", "quickstart", "three-brokers"])
def test_the_connections_role_holds_exactly_its_own_variables(
    layout_name: str, tmp_path: Path
) -> None:
    """AC33: an exact list, so a merged `control-plane-env` (its check key, its mail and
    object store keys) or anything added later fails here."""
    services = _render(layout_name, tmp_path, profiles=("connections",))

    held = set(services[CONNECTIONS_ROLE]["environment"])

    assert held == CONNECTIONS_ROLE_VARIABLES, sorted(held ^ CONNECTIONS_ROLE_VARIABLES)
    assert services[CONNECTIONS_ROLE]["environment"]["SCHEDULER_ROLE"] == "connections"
    assert services[CONNECTIONS_ROLE]["command"] == ["scheduler-connections"]


def test_only_the_api_and_the_connections_role_hold_the_connection_key(
    layout: str, tmp_path: Path
) -> None:
    """AC33, FD10: the separation is which container holds the key."""
    services = _render(layout, tmp_path, profiles=EVERY_PROFILE)

    holders = {
        name
        for name, service in services.items()
        if "CONNECTION_SECRET_KEY" in (service.get("environment") or {})
    }

    assert "api" in holders
    assert holders <= {"api", CONNECTIONS_ROLE}, f"{layout}: {sorted(holders)}"


def test_the_hosted_overlay_names_no_connections_role() -> None:
    """Hosted has no tracker connections until founder decision 3 (step H)."""
    hosted = yaml.safe_load((COMPOSE / "nexovue.hosted.yaml").read_text())["services"]

    assert CONNECTIONS_ROLE not in hosted


def test_an_install_without_the_connection_key_refuses_to_start(tmp_path: Path) -> None:
    """`${…:?}`: an upgrade adds the key (`deploy/nexovue`), so a missing one is a fault."""
    values = {k: v for k, v in SETTINGS.items() if k != "CONNECTION_SECRET_KEY"}

    rendered = _compose_config("self-hosted", tmp_path, values)

    assert rendered.returncode != 0
    assert "set CONNECTION_SECRET_KEY" in rendered.stderr


def test_the_development_stack_runs_the_connections_role_on_its_own_tasks() -> None:
    dev = yaml.safe_load((COMPOSE / "docker-compose.yaml").read_text(encoding="utf-8"))
    service = dev["services"][CONNECTIONS_ROLE]

    argv = service["command"].split()
    assert argv[argv.index("--queues") + 1] == "connections,connections-interactive"
    assert argv[argv.index("--tasks") + 1] == "connections"
    assert service["environment"]["SCHEDULER_ROLE"] == "connections"
    assert "CHECK_SECRET_KEY" not in service["environment"]


def test_a_restore_stops_every_role_that_writes_to_postgres(tmp_path: Path) -> None:
    """T3.12: a role left running would write to the database being replaced. Held against
    the rendered install with every profile on, so a new role must join the stop list."""
    from install_kit import run_kit_function

    services = _render("self-hosted", tmp_path, profiles=EVERY_PROFILE)
    writers = {
        name
        for name, service in services.items()
        if "POSTGRES_DSN" in (service.get("environment") or {}) and name not in ONE_SHOT
    }
    settings = tmp_path / "kit-settings.env"
    settings.write_text("NEXOVUE_CONNECTIONS='yes'\n")

    stopped = run_kit_function(tmp_path, "services_to_stop", str(settings)).stdout.split()

    assert CONNECTIONS_ROLE in writers
    assert writers <= set(stopped), sorted(writers - set(stopped))
