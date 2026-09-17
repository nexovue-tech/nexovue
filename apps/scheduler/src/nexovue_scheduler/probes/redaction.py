"""Removing every opened secret from everything a run leaves behind (FR22, AC17).

By value, in every form a value is likely to take on its way out: as typed, URL
encoded, JSON escaped, and base64 — the last because `Basic` auth and many
tokens travel that way. A secret reshaped some other way before sending is not
caught, which ADR-010 states.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from typing import Any, Final, TypeVar
from urllib.parse import quote, quote_plus

from pydantic import BaseModel

REDACTED: Final = "[secret]"

# Shorter than this and a secret would redact ordinary text everywhere it
# appears. The API refuses to store one this short, so this is a second line.
MIN_REDACTED_LENGTH: Final = 4

Model = TypeVar("Model", bound=BaseModel)


class Redactor:
    def __init__(self, secret_values: Iterable[str]) -> None:
        forms: set[str] = set()
        for value in secret_values:
            if len(value) < MIN_REDACTED_LENGTH:
                continue
            forms.update(
                {
                    value,
                    quote(value, safe=""),
                    quote_plus(value),
                    json.dumps(value)[1:-1],
                    base64.b64encode(value.encode()).decode(),
                }
            )
        # Longest first, so a secret containing another is replaced whole.
        self._forms = sorted((form for form in forms if form), key=len, reverse=True)

    def text(self, value: str) -> str:
        for form in self._forms:
            value = value.replace(form, REDACTED)
        return value

    def model(self, instance: Model) -> Model:
        if not self._forms:
            return instance
        return type(instance).model_validate(self._walk(instance.model_dump(mode="json")))

    def _walk(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self._walk(item) for item in value]
        if isinstance(value, dict):
            return {key: self._walk(item) for key, item in value.items()}
        return value
