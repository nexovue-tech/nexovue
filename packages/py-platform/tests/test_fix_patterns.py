"""The fix rules' text patterns stay linear on any text (CodeQL py/polynomial-redos, PR #25).

K6 reads every line of a repository file up to 256 KiB and every line the model adds, and
G4 reads the model's general answer, so a long run of one character must cost one pass, not
one pass per character. Each case is CodeQL's shape at a size the earlier patterns took
seconds on (2.5 to 37, docs/features/ai-solution/status.md); the budget is far above a
linear pass and far below that, and each case checks what was found as well. A parameter
names its shape and the test builds the text, so no worker holds it for the whole run.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from nexovue_platform.fixes.patterns import addresses_in, without_urls
from nexovue_platform.fixes.prose import email_in, has_markup

# Generous for a linear pass of 100,000 characters, even on a loaded CI runner.
LINEAR_BUDGET_SECONDS = 1.0
LONG = 50_000


def timed[T](work: Callable[[], T]) -> T:
    started = time.perf_counter()
    result = work()
    elapsed = time.perf_counter() - started
    assert elapsed < LINEAR_BUDGET_SECONDS, f"took {elapsed:.2f}s"
    return result


# ------------------------------------------------------------------ URLs (K6, G4)


@pytest.mark.parametrize("hosts_anywhere", [True, False])
def test_a_long_run_of_letters_with_no_scheme_holds_no_address(hosts_anywhere: bool) -> None:
    line = '"' + "a" * LONG

    assert timed(lambda: addresses_in(line, hosts_anywhere=hosts_anywhere)) == []


def test_a_long_run_of_letters_ending_in_a_scheme_is_one_url() -> None:
    line = "1+" + "a" * LONG + "://x.example/p q"

    assert timed(lambda: addresses_in(line, hosts_anywhere=False)) == [line[2:-2]]


def test_a_scheme_inside_a_url_is_part_of_that_url() -> None:
    line = "go http://a.example/x+y://b.example z ftp://c"

    assert addresses_in(line, hosts_anywhere=False) == [
        "http://a.example/x+y://b.example",
        "ftp://c",
    ]
    assert without_urls(line) == "go   z  "


def test_a_url_starts_at_the_first_letter_of_its_scheme() -> None:
    assert addresses_in("1.-\u212a+s://x", hosts_anywhere=False) == ["\u212a+s://x"]
    assert addresses_in("12+.://x", hosts_anywhere=False) == []


def test_removing_urls_keeps_what_led_up_to_the_scheme() -> None:
    assert without_urls("x 1.-https://a.example/p y") == "x 1.-  y"
    assert timed(lambda: without_urls("a" * LONG)) == "a" * LONG


# --------------------------------------------------------------- email (U4, G4)


NO_EMAIL: dict[str, Callable[[], str]] = {
    "plus_run": lambda: "+" * 2 * LONG,
    "domain_with_no_dot": lambda: "a@" + "a-" * LONG,
}


@pytest.mark.parametrize("shape", NO_EMAIL)
def test_a_long_run_with_no_email_address_is_read_once(shape: str) -> None:
    text = NO_EMAIL[shape]()

    assert timed(lambda: email_in(text)) is None


def test_an_email_address_after_a_long_run_is_found_whole() -> None:
    text = "+" * 2 * LONG + "ops@example.org"

    assert timed(lambda: email_in(text)) == text


@pytest.mark.parametrize(
    ("text", "address"),
    [
        ("mail a.b+c@x.example.org now", "a.b+c@x.example.org"),
        ("a@b.c.", "a@b.c"),
        ("a@b@c.d", "b@c.d"),
        ("a@localhost", None),
        ("a@-.x", "a@-.x"),
        ("a@.b.c", None),
    ],
)
def test_an_email_address_is_a_run_then_two_or_more_labels(text: str, address: str | None) -> None:
    assert email_in(text) == address


# -------------------------------------------------------------- markup (U4, G4)


UNCLOSED_MARKUP: dict[str, Callable[[], str]] = {
    "bracket_run": lambda: "[" * 2 * LONG,
    "link_opening_then_brackets": lambda: "[](" + "[" * 2 * LONG,
    "link_openings": lambda: "[](" * LONG,
    "tag_openings": lambda: "<a" * LONG,
}
CLOSED_MARKUP: dict[str, Callable[[], str]] = {
    "link": lambda: "[" * 2 * LONG + "](x)",
    "link_openings_closed": lambda: "[](" * LONG + ")",
    "tag": lambda: "<a" * LONG + ">",
}


@pytest.mark.parametrize("shape", UNCLOSED_MARKUP)
def test_a_long_run_of_unclosed_markup_is_read_once(shape: str) -> None:
    text = UNCLOSED_MARKUP[shape]()

    assert timed(lambda: has_markup(text)) is False


@pytest.mark.parametrize("shape", CLOSED_MARKUP)
def test_markup_closed_after_a_long_run_is_found(shape: str) -> None:
    text = CLOSED_MARKUP[shape]()

    assert timed(lambda: has_markup(text)) is True
