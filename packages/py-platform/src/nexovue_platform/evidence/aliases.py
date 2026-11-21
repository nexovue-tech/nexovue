"""Stand-in names for a finding's real ones, append-only across its versions (design,
*The model's input*). In the platform so the API and F5 can restore them (A37).

`service_1` keeps its meaning for a finding's whole life: a version reuses the book of
the one before and only appends. Fact numbers (`e1`) work the same way over evidence
ids. The book holds real names, so it is stored apart from `model_input`, in a column
the AI account cannot read (A19, AC45).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final, Literal

from nexovue_platform.evidence import MAX_ALIASES
from nexovue_platform.evidence.model_facts import Alias, FactId

AliasKind = Literal["service", "version", "error", "check"]
ALIAS_KINDS: Final = ("service", "version", "error", "check")


class AliasesFull(Exception):  # noqa: N818 — a state, not an error
    """The finding already holds `MAX_ALIASES` names; the fact becomes `truncated`."""


class AliasBook:
    """Names and fact ids, numbered in the order they were first met."""

    def __init__(self, stored: Mapping[str, Any] | None = None) -> None:
        stored = stored or {}
        self._names: dict[str, str] = dict(stored.get("names", {}))
        self._facts: dict[str, str] = dict(stored.get("facts", {}))
        self._by_name = {(alias.split("_")[0], name): alias for alias, name in self._names.items()}
        self._by_evidence = {evidence: number for number, evidence in self._facts.items()}

    def alias(self, kind: AliasKind, name: str) -> Alias:
        """The stand-in for `name`, made if new. Raises `AliasesFull` past the bound."""
        known = self._by_name.get((kind, name))
        if known is not None:
            return Alias(known)
        if len(self._names) >= MAX_ALIASES:
            raise AliasesFull
        number = 1 + sum(1 for alias in self._names if alias.startswith(f"{kind}_"))
        made = f"{kind}_{number}"
        self._names[made] = name
        self._by_name[(kind, name)] = made
        return Alias(made)

    def fact(self, evidence_id: str) -> FactId:
        """The input's number for a fact, made if new."""
        known = self._by_evidence.get(evidence_id)
        if known is not None:
            return FactId(known)
        made = f"e{len(self._facts) + 1}"
        self._facts[made] = evidence_id
        self._by_evidence[evidence_id] = made
        return FactId(made)

    def name_of(self, alias: str) -> str | None:
        return self._names.get(alias)

    def evidence_of(self, fact_id: str) -> str | None:
        return self._facts.get(fact_id)

    def as_json(self) -> dict[str, dict[str, str]]:
        return {"names": dict(self._names), "facts": dict(self._facts)}


def restore(segments: Iterable[Mapping[str, str]], book: AliasBook) -> list[dict[str, str]]:
    """Segments with each alias put back as a name segment. Text that is not an alias
    stays text, so restoring can never build a link (design, *Validation*)."""
    restored: list[dict[str, str]] = []
    for segment in segments:
        alias = segment.get("alias")
        if alias is not None:
            name = book.name_of(alias)
            restored.append({"name": name} if name is not None else {"text": alias})
        else:
            restored.append(dict(segment))
    return restored
