"""YAML read as data only, with every alias refused before anything is built.

The safe loader constructs no Python objects, but it still expands aliases: a
few kilobytes of nested aliases ("billion laughs") become gigabytes in memory.
Walking the event stream first finds an alias without expanding any of them.
"""

from __future__ import annotations

from typing import Final

import yaml

from nexovue_api.errors.exceptions import InvalidRequestError

MAX_YAML_CHARACTERS: Final = 5 * 1024 * 1024


def load(text: str, *, what: str) -> object:
    """The document `text` holds, or `InvalidRequestError` naming where it failed (FR15)."""
    if len(text) > MAX_YAML_CHARACTERS:
        raise InvalidRequestError(
            f"This {what} is larger than {MAX_YAML_CHARACTERS // (1024 * 1024)} MB, "
            "which is the most an import may read."
        )
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent):
                raise InvalidRequestError(
                    f"This {what} uses a YAML alias{_position(event.start_mark)}. Aliases are "
                    "not accepted in an import; write the repeated part out in full."
                )
        return yaml.safe_load(text)
    except yaml.MarkedYAMLError as unparseable:
        problem = unparseable.problem or "it is not valid YAML"
        raise InvalidRequestError(
            f"This {what} could not be read{_position(unparseable.problem_mark)}: {problem}."
        ) from unparseable
    except yaml.YAMLError as unparseable:
        raise InvalidRequestError(f"This {what} could not be read as YAML.") from unparseable
    except RecursionError as too_deep:
        raise InvalidRequestError(f"This {what} is nested too deeply to read.") from too_deep


def _position(mark: object) -> str:
    line = getattr(mark, "line", None)
    column = getattr(mark, "column", None)
    if not isinstance(line, int) or not isinstance(column, int):
        return ""
    return f" at line {line + 1}, column {column + 1}"
