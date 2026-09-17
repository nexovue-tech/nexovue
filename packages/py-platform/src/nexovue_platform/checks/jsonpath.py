"""RFC 9535 JSONPath, without the two functions that run a regular expression.

`match()` and `search()` evaluate I-Regexp through the backtracking `regex`
module, so a filter a customer typed could hold a probe worker. A pattern belongs
in a `matches` assertion, which runs on RE2; everything else in the RFC stays.
"""

from __future__ import annotations

from typing import Any, Final

from jsonpath_rfc9535 import JSONPathEnvironment, JSONPathError, JSONPathQuery

_BACKTRACKING_FUNCTIONS: Final = ("match", "search")


class _Environment(JSONPathEnvironment):
    def setup_function_extensions(self) -> None:
        super().setup_function_extensions()
        for name in _BACKTRACKING_FUNCTIONS:
            del self.function_extensions[name]


_ENVIRONMENT: Final = _Environment()


def compile_json_path(expression: str) -> JSONPathQuery:
    """The compiled query, or `ValueError` saying why it does not parse."""
    try:
        return _ENVIRONMENT.compile(expression)
    except JSONPathError as unparseable:
        raise ValueError(f"not a valid JSONPath expression: {unparseable}") from unparseable


def find_values(expression: str, document: Any) -> list[Any]:
    return [node.value for node in compile_json_path(expression).finditer(document)]
