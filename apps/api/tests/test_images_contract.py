"""Two images, each role its own container (ADR-019; docs/changes/ready-to-deploy, T4).

Read from the Dockerfiles and compose files rather than built images, so `just check` holds the
contract without Docker: two recipes and no others, every role the compose files start is one the
image knows, and every nexovue service runs one of the two images.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml

import nexovue_api
from nexovue_api.roles import ROLES

ROOT: Final = Path(nexovue_api.__file__).parents[4]
IMAGES: Final = ROOT / "deploy" / "images"
NEXOVUE: Final = "nexovue"
PIPELINE: Final = "nexovue-pipeline"
PIPELINE_BINARIES: Final = ("/receiver", "/worker", "/dlq", "/setup")
DEPLOYED_FILES: Final = ("docker-compose.app.yaml",)


def _services(file_name: str) -> dict[str, Any]:
    document = yaml.safe_load((ROOT / "deploy" / "compose" / file_name).read_text())
    return dict(document["services"])


def _image_name(reference: str) -> str:
    """`ghcr.io/nexovue-tech/nexovue-pipeline:1.2.3` → `nexovue-pipeline`."""
    return reference.rsplit("/", 1)[-1].split(":", 1)[0]


def test_there_are_two_image_recipes_and_no_others() -> None:
    assert sorted(path.name for path in IMAGES.glob("*.Dockerfile")) == [
        f"{PIPELINE}.Dockerfile",
        f"{NEXOVUE}.Dockerfile",
    ]
    assert not list((ROOT / "apps").glob("*/Dockerfile")), "a per-service image came back"


def test_the_nexovue_image_starts_through_the_role_command_and_carries_the_console() -> None:
    recipe = (IMAGES / f"{NEXOVUE}.Dockerfile").read_text()

    assert 'ENTRYPOINT ["nexovue"]' in recipe
    assert "COPY --from=console /app/apps/web/dist /srv/web" in recipe
    assert "org.opencontainers.image.revision" in recipe


def test_the_pipeline_image_carries_every_binary_and_the_migrations() -> None:
    recipe = (IMAGES / f"{PIPELINE}.Dockerfile").read_text()
    copied = re.search(r"COPY --from=build (.+) /\n", recipe)

    assert copied is not None
    assert sorted(copied.group(1).replace("/out", "").split()) == sorted(PIPELINE_BINARIES)
    assert "COPY deploy/clickhouse/migrations /migrations/clickhouse" in recipe
    assert "BUILDINFO}.revision=${REVISION}" in recipe


def test_every_nexovue_service_runs_one_of_the_two_images_with_a_known_role() -> None:
    for file_name in DEPLOYED_FILES:
        for name, definition in _services(file_name).items():
            reference = definition.get("image", "")
            image = _image_name(reference)
            if image not in (NEXOVUE, PIPELINE):
                assert not reference.split("/")[-1].startswith(f"{NEXOVUE}/"), (name, reference)
                continue
            command = definition.get("command") or []
            assert command, f"{file_name}: {name} names no role"
            if image == NEXOVUE:
                assert command[0] in {*ROLES, "web-assets"}, f"{name}: unknown role {command}"
            else:
                assert command[0] in PIPELINE_BINARIES, f"{name}: unknown binary {command}"
