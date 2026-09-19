"""What a service reading *is*, defined once for the screen that shows it and the
alert that fires on it (F21, ADR-012).

The Services screen and a threshold rule both read the per-minute rollup, and the
Errors screen and a new-error rule both need an error group's identity. If each
wrote its own arithmetic, an alert could fire on a p95 the screen it links to does
not show, or announce as new a group the Errors screen has listed for a week.

Plain values and SQL fragments only: this package reaches no store
(`test_the_shared_package_reaches_neither_service_nor_a_database`). Each service
splices these into statements it owns, under its own tenant chokepoint.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------- the rollup

# Exactly the levels the stored `AggregateFunction(quantiles(...), Float64)` was
# built with. `quantilesMerge` refuses a state whose levels differ, so these are
# not a preference — they are the column's own definition, repeated.
QUANTILE_LEVELS: Final = (0.5, 0.95, 0.99)
QUANTILE_ARGUMENTS: Final = ", ".join(str(level) for level in QUANTILE_LEVELS)

# ClickHouse arrays are 1-based, and `quantilesMerge` returns them in the order above.
P50_INDEX: Final = 1
P95_INDEX: Final = 2
P99_INDEX: Final = 3


def failure_ratio(errors: int, total: int) -> float:
    """The share of calls that failed, as a fraction. Nothing measured is 0.0."""
    return errors / total if total > 0 else 0.0


# ----------------------------------------------------------- an error's identity
#
# Grouping on the raw message would make one group per occurrence, because a
# message routinely embeds an id, a path, a duration or an address. Each pattern
# collapses one class of embedded value to a fixed token, so that "User 4f3a…6666
# not found" and "User 9c8d…ffff not found" are one error. Order matters: a UUID
# contains digits and an email contains a hostname, so the specific run first.

# The attribute an SDK sets to choose its own grouping. Present and non-empty, it
# replaces everything derived below. ClickHouse's JSON type reads a dot as a path
# separator, so the flat key arrives as a two-level path.
SDK_FINGERPRINT_PATH: Final = "grouping.fingerprint"

UUID_PLACEHOLDER: Final = "<uuid>"
EMAIL_PLACEHOLDER: Final = "<email>"
HEX_PLACEHOLDER: Final = "<hex>"
NUMBER_PLACEHOLDER: Final = "<n>"

# Doubled backslashes: ClickHouse unescapes the string literal once before the
# regular expression engine sees it.
UUID_PATTERN: Final = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
EMAIL_PATTERN: Final = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}"
# Eight or more hex digits is an id, a hash or a token, never a word.
HEX_PATTERN: Final = r"\\b[0-9a-fA-F]{8,}"
# No trailing word boundary: `45.12ms` must template as `<n>ms`, not `<n>.12ms`.
NUMBER_PATTERN: Final = r"\\b[0-9]+(\\.[0-9]+)?"
WHITESPACE_PATTERN: Final = r"\\s+"

# A template longer than this is a payload dump, not a message. Truncating keeps
# the group key bounded and the list readable.
MESSAGE_TEMPLATE_MAX_CHARS: Final = 512

# Over a row with an `exception_message` column.
MESSAGE_TEMPLATE_SQL: Final = f"""
        substring(
            trim(replaceRegexpAll(replaceRegexpAll(replaceRegexpAll(replaceRegexpAll(
                replaceRegexpAll(exception_message, '{UUID_PATTERN}', '{UUID_PLACEHOLDER}'),
                '{EMAIL_PATTERN}', '{EMAIL_PLACEHOLDER}'),
                '{HEX_PATTERN}', '{HEX_PLACEHOLDER}'),
                '{NUMBER_PATTERN}', '{NUMBER_PLACEHOLDER}'),
                '{WHITESPACE_PATTERN}', ' ')),
            1, {MESSAGE_TEMPLATE_MAX_CHARS})
"""

# The two ways a group id is derived, kept apart inside the hash so an SDK
# fingerprint can never collide with a derived one.
SDK_NAMESPACE: Final = "sdk\\n"
DERIVED_NAMESPACE: Final = "derived\\n"

# Over a row with `sdk_fingerprint`, `exception_type` and `message_template`.
GROUP_ID_SQL: Final = f"""
    lower(hex(sipHash64(if(
        sdk_fingerprint != '',
        concat('{SDK_NAMESPACE}', sdk_fingerprint),
        concat('{DERIVED_NAMESPACE}', exception_type, '\\n', message_template)
    ))))
"""
