"""Opening the secrets a run uses, in the worker, immediately before the request.

They arrive sealed inside the task (ADR-010), so the queue never holds a value.
Opened values live in this run's memory and in the redaction list, and nowhere
else.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from nexovue_platform.checks.sealing import (
    CHECK_SECRET_PURPOSE,
    ConfiguredKeys,
    SealedBox,
    SealError,
    check_secret_owner,
    decode_sealed,
    keyring_from,
)

from nexovue_scheduler.probes.failures import ProberError

_NO_KEY: Final = "check secrets cannot be opened on this prober: no check secret key is configured"
_WRONG_KEY: Final = "a check secret could not be opened with this prober's key"


def open_secrets(
    sealed: Mapping[str, str], *, project_id: str, keys: ConfiguredKeys
) -> dict[str, str]:
    """Every sealed value opened, or `ProberError` — a key problem is ours."""
    if not sealed:
        return {}
    if not keys.current:
        raise ProberError(_NO_KEY)
    try:
        box = SealedBox(keyring_from(keys.current, keys.previous), purpose=CHECK_SECRET_PURPOSE)
    except ValueError as unusable:
        raise ProberError(_NO_KEY) from unusable
    opened: dict[str, str] = {}
    for name, value in sealed.items():
        try:
            opened[name] = box.open(
                decode_sealed(value), owner=check_secret_owner(project_id, name)
            )
        except (SealError, ValueError) as unreadable:
            raise ProberError(_WRONG_KEY) from unreadable
    return opened
