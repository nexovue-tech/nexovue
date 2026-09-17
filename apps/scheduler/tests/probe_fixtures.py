"""Building check definitions and run contexts for the probe suites."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from nexovue_platform.checks.sealing import (
    CHECK_SECRET_PURPOSE,
    SealedBox,
    check_secret_owner,
    decode_key,
    encode_sealed,
)
from nexovue_platform.checks.task import CheckDefinition, FileReference

from nexovue_scheduler.probes.runner import RunContext

SECRET_KEY = "qPYHqC1y6hD0s6knf12GgLTcvJsKkPdzaDgvE8sAFA4="
ORG_ID = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
PROJECT_ID = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"


class MemoryFiles:
    """File bytes handed over directly, recording every read."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.reads: list[str] = []

    def read(self, org_id: str, file_id: str) -> bytes:
        self.reads.append(file_id)
        from nexovue_scheduler.probes.failures import MisconfiguredError

        if file_id not in self.files:
            raise MisconfiguredError(f"the file {file_id} is no longer stored")
        return self.files[file_id]


def sealed(values: dict[str, str]) -> dict[str, str]:
    box = SealedBox(decode_key(SECRET_KEY), purpose=CHECK_SECRET_PURPOSE)
    return {
        name: encode_sealed(box.seal(value, owner=check_secret_owner(PROJECT_ID, name)))
        for name, value in values.items()
    }


def definition(
    config: dict[str, Any],
    *,
    secrets: dict[str, str] | None = None,
    variables: dict[str, str] | None = None,
    files: dict[str, FileReference] | None = None,
    degraded_ms: int | None = None,
    failing_ms: int | None = None,
    retries: int = 1,
    locations: list[str] | None = None,
) -> CheckDefinition:
    return CheckDefinition(
        check_id=str(uuid.uuid4()),
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        name="test",
        type=config["type"],
        config=config,
        interval_seconds=60,
        locations=locations or ["local"],
        degraded_ms=degraded_ms,
        failing_ms=failing_ms,
        retries=retries,
        variables=variables or {},
        sealed_secrets=sealed(secrets or {}),
        files=files or {},
    )


def context(files: MemoryFiles | None = None, *, secret_key: str = SECRET_KEY) -> RunContext:
    return RunContext(
        location="local",
        attempt=1,
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(UTC),
        maintenance=False,
        private_allowed=True,
        secret_key=secret_key,
        files=files or MemoryFiles(),
    )


def status_below_400() -> dict[str, str]:
    return {"source": "status_code", "comparison": "less_than", "target": "400"}
