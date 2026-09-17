"""`{{name}}` and `{{secret.NAME}}`: the one substitution syntax a check uses.

`{{name}}` is Postman's own syntax, so an imported collection keeps working
unchanged. A reference nothing defines is refused rather than sent as the literal
text — a request carrying `{{token}}` reaches the customer's API looking like a
real one and fails there for a reason nobody can see.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

SECRET_NAMESPACE: Final = "secret"

# A variable may be dotted (`order.id`) because Postman's may; a secret name is
# plain so it reads the same in the settings list and in a request.
VARIABLE_NAME: Final = r"[A-Za-z_][A-Za-z0-9_.\-]{0,63}"
SECRET_NAME: Final = r"[A-Za-z_][A-Za-z0-9_]{0,63}"

VARIABLE_NAME_PATTERN: Final = re.compile(rf"^{VARIABLE_NAME}$")
SECRET_NAME_PATTERN: Final = re.compile(rf"^{SECRET_NAME}$")

_REFERENCE: Final = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")


@dataclass(frozen=True, slots=True)
class References:
    """Every name a piece of text refers to, split by where each must come from."""

    variables: frozenset[str]
    secrets: frozenset[str]


class UnresolvedReferenceError(ValueError):
    """Text named a variable or secret that nothing defines."""

    def __init__(self, reference: str) -> None:
        super().__init__(f"{{{{{reference}}}}} is not defined")
        self.reference = reference


def references_in(text: str) -> References:
    """The variable and secret names `text` uses. Malformed names count as
    variables, so the caller's "is it defined" check refuses them by name."""
    variables: set[str] = set()
    secrets: set[str] = set()
    for match in _REFERENCE.finditer(text):
        namespace, _, name = match.group(1).partition(".")
        if namespace == SECRET_NAMESPACE and name:
            secrets.add(name)
        else:
            variables.add(match.group(1))
    return References(variables=frozenset(variables), secrets=frozenset(secrets))


def substitute(text: str, *, variables: Mapping[str, str], secrets: Mapping[str, str]) -> str:
    """Replace every reference, or raise naming the first one that is undefined."""

    def value_of(match: re.Match[str]) -> str:
        reference = match.group(1)
        namespace, _, name = reference.partition(".")
        if namespace == SECRET_NAMESPACE and name:
            if name in secrets:
                return secrets[name]
        elif reference in variables:
            return variables[reference]
        raise UnresolvedReferenceError(reference)

    return _REFERENCE.sub(value_of, text)


def has_references(text: str) -> bool:
    return _REFERENCE.search(text) is not None
