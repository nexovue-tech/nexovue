"""YAML is read as data, and an alias is refused before any is expanded (FR15)."""

from __future__ import annotations

import time

import pytest

from nexovue_api.checkimports import yaml_safe
from nexovue_api.errors.exceptions import InvalidRequestError

# Nine levels of nine-fold aliases: about 387 million strings if expanded.
BILLION_LAUGHS = """
a: &a ["lol","lol","lol","lol","lol","lol","lol","lol","lol"]
b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]
c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]
d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]
e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]
f: &f [*e,*e,*e,*e,*e,*e,*e,*e,*e]
g: &g [*f,*f,*f,*f,*f,*f,*f,*f,*f]
h: &h [*g,*g,*g,*g,*g,*g,*g,*g,*g]
i: &i [*h,*h,*h,*h,*h,*h,*h,*h,*h]
"""
# Refusing the bomb is a matter of reading events, never of building it.
MAX_REFUSAL_SECONDS = 1.0


def test_a_plain_document_is_read() -> None:
    assert yaml_safe.load("openapi: 3.1.0\npaths:\n  /a: {}\n", what="file") == {
        "openapi": "3.1.0",
        "paths": {"/a": {}},
    }


def test_an_anchor_nothing_refers_to_is_harmless() -> None:
    assert yaml_safe.load("a: &unused 1\n", what="file") == {"a": 1}


def test_the_billion_laughs_document_is_refused_without_being_expanded() -> None:
    started = time.monotonic()
    with pytest.raises(InvalidRequestError, match="YAML alias at line 3"):
        yaml_safe.load(BILLION_LAUGHS, what="OpenAPI file")

    assert time.monotonic() - started < MAX_REFUSAL_SECONDS


def test_a_merge_key_is_an_alias_and_is_refused() -> None:
    with pytest.raises(InvalidRequestError, match="alias"):
        yaml_safe.load("base: &base {x: 1}\nchild:\n  <<: *base\n", what="file")


def test_a_python_object_tag_is_refused_by_the_safe_loader() -> None:
    with pytest.raises(InvalidRequestError, match="could not be read"):
        yaml_safe.load("x: !!python/object/apply:os.system ['true']\n", what="file")


def test_a_syntax_error_names_its_line_and_column() -> None:
    with pytest.raises(InvalidRequestError) as refused:
        yaml_safe.load("paths:\n  /a: [unclosed\n", what="OpenAPI file")

    assert "line 3" in refused.value.detail
    assert "column" in refused.value.detail


def test_a_file_over_the_size_limit_is_refused_before_parsing() -> None:
    with pytest.raises(InvalidRequestError, match="larger than 5 MB"):
        yaml_safe.load("a" * (5 * 1024 * 1024 + 1), what="OpenAPI file")
