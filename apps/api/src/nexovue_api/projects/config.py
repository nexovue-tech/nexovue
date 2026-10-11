"""The grammar of `projects.masking_config` and `projects.sampling_config`.

Both documents are parsed twice: here, when somebody writes one, and in the Go
worker that enforces it — `pipeline/mask/rules.go` and `pipeline/sample/policy.go`.
Neither language can import the other, so `tests/test_cross_service_contracts.py`
asserts the names below are the ones those two files accept.

**Why the API validates at all**. `projectconfig/refresh.go` keeps the
last-good masking rules when a document fails to parse, but only for a worker
that has already loaded that project. A cold start falls back to defaults that
name *no custom keys*, so the first restart after a bad edit is when a project
silently stops masking the keys it asked for. Refusing the write is that check
moved to where the person is, before the row exists.

**One deliberate difference from Go, and it is in the safe direction.**
`encoding/json` ignores a document key it has no field for, so the worker accepts
`{"mask_key": [...]}` — a typo for `mask_keys` — and masks nothing. That is the
same failure the worker already refuses for an unknown *rule* name, for the same
reason: the operator who typed it believes data is being masked. Refusing it here
never accepts a document the worker would reject, so the invariant that matters
holds; what changes is that a typo is caught rather than stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from nexovue_api.errors.exceptions import InvalidRequestError

MASKING_DOCUMENT: Final = "masking_config"
SAMPLING_DOCUMENT: Final = "sampling_config"

# The default rule families, spelled as `mask.DefaultRuleNames` spells them. A
# name here that the worker does not know is a rule the API would accept and the
# worker would reject — the cold-start hole with a nicer form on top.
MASKING_RULE_NAMES: Final = (
    "credential_keys",
    "authorization_headers",
    "cookies",
    "card_numbers",
    "authorization_credentials",
    "url_credentials",
)

# The two keys whose entries name a default family. `disable` switches one off,
# `hash` makes its matches a pseudonym instead of the placeholder.
RULE_LIST_KEYS: Final = ("disable", "hash")

# The keys whose entries are the customer's own attribute keys and values. The
# worker lower-cases and trims these and drops the empties, so anything is
# accepted here as long as it is a string.
TEXT_LIST_KEYS: Final = (
    "mask_keys",
    "mask_key_contains",
    "hash_keys",
    "hash_key_contains",
    "allow_values",
)

MASKING_DOCUMENT_KEYS: Final = RULE_LIST_KEYS + TEXT_LIST_KEYS

DISABLE_KEY: Final = "disable"

KEEP_ERRORS: Final = "keep_errors"
SLOW_THRESHOLD_MS: Final = "slow_threshold_ms"
HEALTHY_PERCENT: Final = "healthy_percent"
SAMPLING_DOCUMENT_KEYS: Final = (KEEP_ERRORS, SLOW_THRESHOLD_MS, HEALTHY_PERCENT)

# `policy.go` refuses anything outside these rather than clamping: a project that
# believes it stores everything while it stores a tenth is the failure clamping
# would produce.
HEALTHY_PERCENT_MIN: Final = 0
HEALTHY_PERCENT_MAX: Final = 100
SLOW_THRESHOLD_MS_MIN: Final = 0


def validate_masking_document(document: object) -> dict[str, Any]:
    """The document the worker would accept, or a refusal naming what is wrong.

    Returns the document rather than a parsed form: what is stored is what the
    worker reads, so normalising here would store something nobody typed.
    """
    fields = _require_object(document, MASKING_DOCUMENT)
    _refuse_unknown_keys(fields, MASKING_DOCUMENT_KEYS, MASKING_DOCUMENT)

    for key in RULE_LIST_KEYS:
        for name in _string_list(fields, key, MASKING_DOCUMENT):
            # `rules.go` trims before matching, so " cookies " is a valid line
            # there and has to be one here.
            if name.strip() not in MASKING_RULE_NAMES:
                raise InvalidRequestError(
                    f"{MASKING_DOCUMENT}: unknown rule {name!r} in {key}; "
                    f"valid names are {', '.join(MASKING_RULE_NAMES)}"
                )
    for key in TEXT_LIST_KEYS:
        _string_list(fields, key, MASKING_DOCUMENT)
    return fields


def validate_sampling_document(document: object) -> dict[str, Any]:
    """The sampling document the worker would accept, or a refusal naming why."""
    fields = _require_object(document, SAMPLING_DOCUMENT)
    _refuse_unknown_keys(fields, SAMPLING_DOCUMENT_KEYS, SAMPLING_DOCUMENT)

    _optional_bool(fields, KEEP_ERRORS)
    threshold = _optional_int(fields, SLOW_THRESHOLD_MS)
    if threshold is not None and threshold < SLOW_THRESHOLD_MS_MIN:
        raise InvalidRequestError(
            f"{SAMPLING_DOCUMENT}: {SLOW_THRESHOLD_MS} is {threshold}, want zero or more"
        )
    percent = _optional_int(fields, HEALTHY_PERCENT)
    if percent is not None and not HEALTHY_PERCENT_MIN <= percent <= HEALTHY_PERCENT_MAX:
        raise InvalidRequestError(
            f"{SAMPLING_DOCUMENT}: {HEALTHY_PERCENT} is {percent}, "
            f"want {HEALTHY_PERCENT_MIN} to {HEALTHY_PERCENT_MAX}"
        )
    return fields


def disabled_rules(document: Mapping[str, Any]) -> frozenset[str]:
    """The default families a stored document switches off.

    Trimmed the way `rules.go` trims, so a stored `" cookies "` and a new
    `"cookies"` are recognised as the same rule rather than as one being turned
    off again.
    """
    entries = document.get(DISABLE_KEY) or []
    if not isinstance(entries, list):
        return frozenset()
    return frozenset(entry.strip() for entry in entries if isinstance(entry, str))


def _require_object(document: object, name: str) -> dict[str, Any]:
    """`json.Unmarshal` into a struct refuses anything that is not an object."""
    if not isinstance(document, dict):
        raise InvalidRequestError(f"{name}: must be a JSON object")
    return document


def _refuse_unknown_keys(fields: Mapping[str, Any], known: tuple[str, ...], name: str) -> None:
    unknown = sorted(key for key in fields if key not in known)
    if unknown:
        raise InvalidRequestError(
            f"{name}: unknown key(s) {', '.join(repr(key) for key in unknown)}; "
            f"valid keys are {', '.join(known)}"
        )


def _string_list(fields: Mapping[str, Any], key: str, name: str) -> list[str]:
    """A `[]string` field. Absent and null are both "not set", as they are in Go."""
    value = fields.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidRequestError(f"{name}: {key} must be a list of strings")
    for entry in value:
        if not isinstance(entry, str):
            raise InvalidRequestError(f"{name}: {key} must be a list of strings")
    return value


def _optional_bool(fields: Mapping[str, Any], key: str) -> bool | None:
    value = fields.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise InvalidRequestError(f"{SAMPLING_DOCUMENT}: {key} must be true or false")
    return value


def _optional_int(fields: Mapping[str, Any], key: str) -> int | None:
    """A whole number. A float is refused even when it is integral, because
    `json.Unmarshal` refuses `10.0` into an `int` — and a value the API stored
    that the worker cannot parse is the hole this module exists to close."""
    value = fields.get(key)
    if value is None:
        return None
    # bool is an int in Python and is not one in Go.
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRequestError(f"{SAMPLING_DOCUMENT}: {key} must be a whole number")
    return value
