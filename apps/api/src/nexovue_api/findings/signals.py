"""A finding's stored signals, read with their units (F6, ADR-022).

Apart from `service.py`, which imports F3's explanation service and through it AI code, so
F5's read (`fix_read.py`) reads signals without reaching any (spec BR12, AC33; founder,
2026-11-21).
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import TypeAdapter

from nexovue_api.findings.schemas import FindingSignal, SignalName, SignalUnit, StoredSignal

SIGNAL_UNITS: Final[dict[SignalName, SignalUnit]] = {
    "slower": "ms",
    "failing": "%",
    "traffic": "rpm",
}

_STORED_SIGNALS: Final = TypeAdapter(list[StoredSignal])


def parse_signals(raw: Any) -> list[FindingSignal]:
    """`findings.signals`, leading signal first, each with its unit.

    The column's default, `{}`, is a finding whose signals are not written yet.
    Anything else that is not the stored list is a defect in the writer, and
    raises rather than showing a finding with its evidence silently missing.
    """
    if raw == {}:
        return []
    return [
        FindingSignal(**stored.model_dump(), unit=SIGNAL_UNITS[stored.signal])
        for stored in _STORED_SIGNALS.validate_python(raw)
    ]
