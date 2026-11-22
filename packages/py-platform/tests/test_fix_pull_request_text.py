"""A draft's title, commit message and body are all template (F5 plan T7.6; spec BR10, BR11,
FR23, FR24a, FR26, AC27, AC62).

No model prose can reach GitHub: the facts take no free text but the requester's checked
note. A public repository's body holds no telemetry figure, console link or login, and the
note only when confirmed; a private one names the login as plain text, never `@login`.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from nexovue_platform.fixes import pull_request_text as words
from nexovue_platform.fixes.prose import BANNED
from nexovue_platform.fixes.pull_request_text import (
    CI_HELD,
    CI_ON_OPEN,
    SKIP_CHECKS_TRAILER,
    SKIP_CI_MARKER,
    ChangedFile,
    MarkedLine,
    NoteRefused,
    PullRequestFacts,
    SignalFacts,
    checked_note,
    pull_request_text,
)
from nexovue_platform.fixes.sentences import NOT_CHECKED

FIX_ID = "0193c3f2-0000-7000-8000-000000000001"


def _facts(**overrides: Any) -> PullRequestFacts:
    values: dict[str, Any] = {
        "fix_id": FIX_ID,
        "folder": "services/shop",
        "private": True,
        "ci_on_open": False,
        "files": [ChangedFile("services/shop/app/pay.py", "stack", 1, 1)],
        "flags": [MarkedLine("risky_call", "services/shop/app/pay.py", 7)],
        "note": "Retries stop the checkout timeouts.",
        "requester_login": "ada",
        "requester_role": "member",
        "signal": SignalFacts("failing", "0.31", "0.01"),
        "console_url": "https://nexovue.example/findings/f-1",
    }
    return PullRequestFacts(**(values | overrides))


def test_fr23_the_commit_carries_both_skip_instructions_and_names_its_one_branch() -> None:
    text = pull_request_text(_facts())

    subject, _, trailer = text.commit_message.partition("\n\n")
    assert subject == (
        f"Suggested change for services/shop (nexovue suggestion {FIX_ID}) {SKIP_CI_MARKER}"
    )
    assert trailer == SKIP_CHECKS_TRAILER
    assert text.branch == f"nexovue/suggestion-{FIX_ID}"
    assert CI_HELD in text.body


def test_ac61_ci_on_open_carries_no_marker_and_says_so() -> None:
    text = pull_request_text(_facts(ci_on_open=True))

    assert SKIP_CI_MARKER not in text.commit_message
    assert SKIP_CHECKS_TRAILER not in text.commit_message
    assert CI_ON_OPEN in text.body and CI_HELD not in text.body


def test_ac27_a_private_body_holds_the_facts_the_login_as_plain_text_and_the_note() -> None:
    body = pull_request_text(_facts()).body

    assert words.WHAT_THIS_IS in body and NOT_CHECKED in body
    assert "Requested by GitHub user ada, through Nexovue." in body and "@ada" not in body
    assert "The failing signal read 0.31 against a normal of 0.01." in body
    assert "https://nexovue.example/findings/f-1" in body
    assert "`Retries stop the checkout timeouts.`" in body
    assert "`services/shop/app/pay.py`: named by the error's stack, +1 -1" in body
    assert "`services/shop/app/pay.py`, line 7: new call" in body


def test_ac27_a_public_body_holds_no_figure_link_login_or_unconfirmed_note() -> None:
    body = pull_request_text(_facts(private=False)).body

    assert "0.31" not in body and "nexovue.example" not in body and "ada" not in body
    assert "Requested by a Nexovue member." in body
    assert "Retries stop" not in body and words.NOTE_HEADING not in body


def test_ac62_a_public_note_goes_in_only_when_confirmed() -> None:
    body = pull_request_text(_facts(private=False, note_confirmed=True)).body

    assert "`Retries stop the checkout timeouts.`" in body


@pytest.mark.parametrize(
    ("note", "code"),
    [
        ("see https://evil.example/x", "address"),
        ("mail me at a@b.example", "email"),
        ("[click](http://x.example)", "markup"),
        ("one\ntwo", "one_line"),
        ("x" * 201, "too_long"),
    ],
)
def test_ac62_the_note_refuses_an_address_markup_a_second_line_and_length(
    note: str, code: str
) -> None:
    with pytest.raises(NoteRefused) as refused:
        checked_note(note)

    assert refused.value.code == code


def test_fr24a_a_note_s_markup_and_mentions_sit_in_a_code_span_it_cannot_close() -> None:
    body = pull_request_text(_facts(note="ping @team with `x` here")).body

    assert "``ping @team with `x` here``" in body


def test_br10_the_facts_take_no_free_text_but_the_note() -> None:
    """No field could carry a model's words: every string is a path, a fixed code, a
    number, the login or a link Nexovue builds; the note is the requester's own."""
    text_fields = {
        field.name
        for field in dataclasses.fields(PullRequestFacts)
        if "str" in str(field.type) and "Sequence" not in str(field.type)
    }
    assert text_fields == {
        "fix_id",
        "folder",
        "note",
        "requester_login",
        "requester_role",
        "console_url",
    }


def test_br11_no_template_word_is_banned() -> None:
    templates = [
        value for name, value in vars(words).items() if name.isupper() and isinstance(value, str)
    ] + list(words.FILE_ROLE_WORDS.values())
    assert templates
    for template in templates:
        assert not BANNED.search(template), template


def test_an_unknown_role_reads_neutrally() -> None:
    body = pull_request_text(_facts(files=[ChangedFile("a/b.py", "other", 0, 2)])).body

    assert "`a/b.py`: read for this suggestion, +0 -2" in body
