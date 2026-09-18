"""A health check must probe the port its dependants call.

`depends_on: condition: service_healthy` is only as honest as the probe behind it.
SeaweedFS runs a master on 9333 and an S3 gateway on 8333 in one process, seconds
apart, and the probe used to ask the master. Compose therefore declared the service
healthy while the port the API uses still refused connections, and the API — which
creates its bucket at boot — exited on every cold start with "could not prepare the
bucket". The second start worked, which is how it survived this long.

This reads the compose files rather than the running stack: the defect is in the
declaration, and a test that needed the stack up could not run in `just check`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
import yaml

import nexovue_api

# Service -> the port its dependants connect to, and therefore the port its own
# health check has to prove is listening. Only services whose readiness another
# service gates on need an entry; a port here is a claim this test enforces.
PORT_DEPENDANTS_USE: Final[dict[str, int]] = {"seaweedfs": 8333}

_INFRASTRUCTURE: Final = "compose.infra.yaml"


def _compose_directory() -> Path:
    return Path(nexovue_api.__file__).parents[4] / "deploy" / "compose"


def _services(file_name: str) -> dict[str, Any]:
    document = yaml.safe_load((_compose_directory() / file_name).read_text(encoding="utf-8"))
    return dict(document.get("services") or {})


@pytest.mark.parametrize(("service", "port"), sorted(PORT_DEPENDANTS_USE.items()))
def test_a_health_check_probes_the_port_its_dependants_call(service: str, port: int) -> None:
    definition = _services(_INFRASTRUCTURE).get(service)
    assert definition is not None, f"{service} is not defined in {_INFRASTRUCTURE}"

    health = definition.get("healthcheck")
    assert health is not None, (
        f"{service} is gated on by another service, so it needs a health check; "
        "without one Compose starts its dependants as soon as the container exists."
    )
    probe = " ".join(str(part) for part in health["test"])

    assert f":{port}" in probe, (
        f"{service}'s health check probes something other than port {port}:\n  {probe}\n"
        f"Its dependants connect to {port}, so this gate can pass while that port is "
        "still refusing connections — which is the whole failure it exists to prevent."
    )


def _undeclared(file_name: str) -> dict[str, list[str]]:
    """Which services connect to another service they never declared a dependency on.

    Read from the environment: `S3_ENDPOINT: http://seaweedfs:8333` names `seaweedfs`
    as plainly as a `depends_on` entry would, and the two disagreeing is the defect
    this module exists for.
    """
    services = _services(file_name)
    missing: dict[str, list[str]] = {}
    for name, definition in services.items():
        environment = str(definition.get("environment") or {})
        # `depends_on` is either a mapping of conditions or a plain list of names;
        # both iterate to the names, which is all this rule asks about.
        named = set(definition.get("depends_on") or {})
        undeclared = sorted(
            other
            for other in services
            if other != name and f"{other}:" in environment and other not in named
        )
        if undeclared:
            missing[name] = undeclared
    return missing


@pytest.mark.parametrize(
    "file_name", sorted(path.name for path in _compose_directory().glob("*.yaml"))
)
@pytest.mark.parametrize("store", sorted(PORT_DEPENDANTS_USE))
def test_a_dependant_of_a_gated_store_waits_for_it(store: str, file_name: str) -> None:
    """Fixing one health check fixes nothing where the dependency was never declared.

    The first version of this module asked only "is every gated service listed here?"
    and passed while `docker-compose.app.yaml` — the production replica, whose whole
    job is to fail here rather than in deployment — started the API with no
    `seaweedfs` gate at all. The rule that catches that runs the other way: a service
    whose environment names one of these stores has to wait for it.
    """
    for name, undeclared in _undeclared(file_name).items():
        assert store not in undeclared, (
            f"{file_name}: {name} connects to {store} but does not depend on it, so "
            f"Compose starts it the moment {store}'s container exists rather than when "
            f"{store} answers. That is the failure this module exists to prevent."
        )


# Services that name another and deliberately do not wait for it — each with the
# reason, decided 2026-09-18 from the services' own startup code
# (docs/changes/compose-dependencies/). Everything else that was here was either a
# real dependency, now declared, or a variable the service never read, now deleted.
_WEB_API = "the console loads and explains itself while the API is down"
_BEAT_KAFKA = (
    "beat never produces; the shared scheduler settings require KAFKA_BROKERS, "
    "so the variable stays although beat does not use it"
)
_LIGHT_POSTGRES = (
    "the probe worker opens Postgres on its first task, and its work only arrives "
    "from beat and the API, both of which wait for Postgres"
)
_INSIGHT = "apps/insight does not exist yet; its dependencies are decided when it is written"

DELIBERATELY_UNDECLARED: Final[dict[str, dict[str, dict[str, str]]]] = {
    "docker-compose.yaml": {
        "web": {"api": _WEB_API},
        "scheduler-beat": {"kafka": _BEAT_KAFKA},
        "scheduler-light": {"postgres": _LIGHT_POSTGRES},
        "insight": {"redis": _INSIGHT},
    },
    "docker-compose.app.yaml": {
        "scheduler-beat": {"kafka": _BEAT_KAFKA},
        "scheduler-light": {"postgres": _LIGHT_POSTGRES},
        "insight": {"kafka": _INSIGHT, "ollama": _INSIGHT},
    },
    "docker-compose.onprem.yaml": {
        "scheduler-beat": {"kafka": _BEAT_KAFKA},
        "scheduler-light": {"postgres": _LIGHT_POSTGRES},
        "insight": {"ollama": _INSIGHT},
    },
    "compose.infra.yaml": {},
}


@pytest.mark.parametrize("file_name", sorted(DELIBERATELY_UNDECLARED))
def test_every_undeclared_dependency_is_one_decided_on_purpose(file_name: str) -> None:
    """Held against the files in both directions.

    A new undeclared dependency fails — declare it, or add it here with its reason.
    One that is no longer undeclared fails too, so a stale reason cannot outlive the
    configuration it explained.
    """
    decided = {
        service: sorted(reasons) for service, reasons in DELIBERATELY_UNDECLARED[file_name].items()
    }
    assert _undeclared(file_name) == decided, (
        f"{file_name}'s undeclared dependencies no longer match the decided list. Declare "
        "a real dependency in `depends_on`, delete a variable the service never reads, or "
        "record a deliberate one in DELIBERATELY_UNDECLARED with its reason."
    )


def test_every_service_gated_on_health_is_listed_here() -> None:
    """The list above is the test; an unlisted service is an unchecked claim.

    A new `condition: service_healthy` against an infrastructure service should
    either name the port its dependants use, or say in `PORT_DEPENDANTS_USE` that
    it was considered. This fails when one is added and not.
    """
    gated: set[str] = set()
    for file_name in sorted(path.name for path in _compose_directory().glob("*.yaml")):
        for definition in _services(file_name).values():
            depends = definition.get("depends_on")
            if not isinstance(depends, dict):
                continue
            gated.update(
                name
                for name, how in depends.items()
                if isinstance(how, dict) and how.get("condition") == "service_healthy"
            )

    infrastructure = set(_services(_INFRASTRUCTURE))
    unlisted = sorted((gated & infrastructure) - set(PORT_DEPENDANTS_USE) - _NO_SINGLE_PORT)
    assert not unlisted, (
        "these infrastructure services are gated on by their health check but are not "
        f"listed in PORT_DEPENDANTS_USE: {unlisted}. Name the port each one's dependants "
        "connect to, or add it to _NO_SINGLE_PORT with the reason."
    )


# Services whose health check cannot be reduced to one port, with the reason. Kafka's
# probe runs the broker's own API-versions client, which is a stronger statement than
# a socket being open. Postgres, ClickHouse and Redis each serve exactly one port and
# probe it with their own client — `pg_isready`, an HTTP ping, `redis-cli ping` — so
# there is no second port a dependant could be waiting on by mistake.
_NO_SINGLE_PORT: Final[frozenset[str]] = frozenset({"kafka", "postgres", "clickhouse", "redis"})
