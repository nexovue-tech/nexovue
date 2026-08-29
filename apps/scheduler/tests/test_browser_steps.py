"""Reading a customer's step list, without a browser.

`parse_steps` is the boundary between a JSONB column a customer edits and a
flow we will actually drive. Everything it rejects here is something that would
otherwise fail halfway through a run, after Chromium had started.
"""

from __future__ import annotations

import pytest

from nexovue_scheduler.probes.browser import (
    ACTION_GOTO,
    BrowserStep,
    FlowConfigError,
    parse_steps,
)

TARGET = "https://shop.acme.example/checkout"


def test_a_check_with_no_steps_still_opens_the_target() -> None:
    """Filling in only a URL is a real check: does the page load."""
    assert parse_steps({}, TARGET) == [
        BrowserStep(name=ACTION_GOTO, action=ACTION_GOTO, url=TARGET)
    ]


def test_steps_keep_their_order_and_their_fields() -> None:
    steps = parse_steps(
        {
            "steps": [
                {"name": "open", "action": "goto", "url": TARGET},
                {"action": "fill", "selector": "#email", "text": "buyer@acme.example"},
                {"action": "click", "selector": "#submit"},
                {
                    "name": "confirm-order",
                    "action": "expect_text",
                    "selector": "h1",
                    "text": "Thanks",
                },
            ]
        },
        TARGET,
    )

    assert [step.name for step in steps] == ["open", "fill", "click", "confirm-order"]
    assert steps[1].text == "buyer@acme.example"
    assert steps[2].selector == "#submit"


def test_an_unknown_action_is_rejected_with_its_index() -> None:
    """The index is what tells the customer which of their steps is wrong."""
    with pytest.raises(FlowConfigError) as rejected:
        parse_steps({"steps": [{"action": "goto", "url": TARGET}, {"action": "teleport"}]}, TARGET)

    assert rejected.value.index == 1
    assert "teleport" in rejected.value.message


@pytest.mark.parametrize(
    ("step", "missing"),
    [
        ({"action": "click"}, "selector"),
        ({"action": "fill", "selector": "#email"}, "text"),
        ({"action": "goto"}, "url"),
        ({"action": "wait_for"}, "selector"),
        ({"action": "expect_text", "selector": "h1"}, "text"),
    ],
)
def test_a_step_missing_what_it_needs_names_the_missing_field(
    step: dict[str, str], missing: str
) -> None:
    with pytest.raises(FlowConfigError) as rejected:
        parse_steps({"steps": [step]}, TARGET)

    assert missing in rejected.value.message


def test_a_steps_value_that_is_not_a_list_is_rejected() -> None:
    with pytest.raises(FlowConfigError):
        parse_steps({"steps": "goto"}, TARGET)


def test_a_step_that_is_not_an_object_is_rejected() -> None:
    with pytest.raises(FlowConfigError) as rejected:
        parse_steps({"steps": [{"action": "goto", "url": TARGET}, "click #submit"]}, TARGET)

    assert rejected.value.index == 1
