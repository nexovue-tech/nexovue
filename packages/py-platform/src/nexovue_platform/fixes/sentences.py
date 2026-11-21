"""Every sentence F5 says about a suggestion, by code (design, *Every sentence*; spec AC8).

One constant, so a code with no sentence fails a test rather than reaching a page as a raw
code, and AC37's banned words are checked once, here. Rung 2b's rows first (plan T2b.7);
T3.7 adds rung 3's. `requester` is the line for someone who may ask (owner, admin,
member); `viewer` for a viewer and a support view, or None where they see nothing.
`{date}` and `{dependency}` are filled by the reader, from Nexovue's own values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from nexovue_platform.fixes.records import GENERAL_REASONS


@dataclass(frozen=True, slots=True)
class FixSentence:
    requester: str
    viewer: str | None


_WITHHELD_VIEWER: Final = "Withheld by a rule"
_NOT_PREPARED: Final = "Could not be prepared"
_NO_CHANGE: Final = "The model did not suggest a change"
_ASK_AGAIN: Final = " Asking again may help."


def _withheld(what: str) -> FixSentence:
    return FixSentence(
        f"The general suggestion was withheld because it {what}.{_ASK_AGAIN}", _WITHHELD_VIEWER
    )


# Why rung 2b is not offered, in FR9's order (the first version, plan T2b.7).
ELIGIBILITY_SENTENCES: Final[dict[str, FixSentence]] = {
    "ai_off": FixSentence("AI is off. An admin sets it up, then an owner agrees.", None),
    "project_opted_out": FixSentence(
        "This project is opted out of AI. An admin can change that.",
        "This project is opted out of AI.",
    ),
    "project_finding": FixSentence(
        "This finding covers the whole project, so no one service's code applies.",
        "This finding covers the whole project, so no one service's code applies.",
    ),
    "closed_as_not_a_problem": FixSentence(
        "This was closed as not a problem, so there is nothing to change.",
        "This was closed as not a problem, so there is nothing to change.",
    ),
    "evidence_pending": FixSentence(
        "Still gathering evidence; this can take a few minutes.",
        "Still gathering evidence; this can take a few minutes.",
    ),
    # Naming files here comes with rung 3 (Step 3), so the requester reads the first
    # sentence alone until then.
    "cause_is_elsewhere": FixSentence(
        "{dependency} has its own earlier finding. See it first.",
        "{dependency} has its own earlier finding.",
    ),
    "limit_reached": FixSentence(
        "Suggested fixes stop once this organization has used half its monthly tokens, or "
        "this finding has had its 3 suggestions today, until {date}. An admin can raise the "
        "monthly limit.",
        "The limit for suggested fixes is used up until {date}.",
    ),
    "already_running": FixSentence(
        "A suggested fix is being prepared.", "A suggested fix is being prepared."
    ),
}

# Why a general row ended without a suggestion: every value of `finding_fixes.reason`.
REASON_SENTENCES: Final[dict[str, FixSentence]] = {
    "evidence_too_thin": FixSentence("The model said: the evidence is not enough.", _NO_CHANGE),
    "needs_a_decision": FixSentence("The model said: this needs a person's decision.", _NO_CHANGE),
    "not_a_code_problem": FixSentence(
        "The model said: this does not look like a code problem.", _NO_CHANGE
    ),
    "general_withheld_G3": _withheld("held hidden or look-alike characters"),
    "general_withheld_G4": _withheld("held a web address or host name"),
    "general_withheld_G5": _withheld("held a shell command or a call that starts a program"),
    "general_withheld_G6": _withheld(
        "told you to install or download something, or used a package outside the "
        "language's standard library"
    ),
    "general_withheld_G7": _withheld(
        "held something shaped like a secret or a long encoded string"
    ),
    "general_withheld_G8": _withheld(
        "claimed more than Nexovue checked, or wrote as if it had read your code"
    ),
    "general_withheld_G9": _withheld("named something Nexovue could not match to this finding"),
    "ai_off": FixSentence("AI was turned off before this was sent, so nothing was sent.", None),
    "project_opted_out": FixSentence(
        "This project was opted out of AI before this was sent, so nothing was sent.",
        "This project is opted out of AI.",
    ),
    "consent_ended_in_flight": FixSentence(
        "Consent was withdrawn, or ended when the provider changed, so this stopped. Nothing "
        "more was sent.",
        "Consent was withdrawn, or ended when the provider changed, so this stopped. Nothing "
        "more was sent.",
    ),
    "limit_reached": FixSentence(
        "The monthly limit for suggested fixes was reached before this was sent.",
        "The limit for suggested fixes is used up.",
    ),
    "provider_refused": FixSentence("The AI provider refused this request.", _NOT_PREPARED),
    "provider_too_long": FixSentence(
        "The request was too long for this AI provider.", _NOT_PREPARED
    ),
    "provider_unavailable": FixSentence("The AI provider did not answer.", _NOT_PREPARED),
    "invalid_output": FixSentence(
        "The model's answer could not be used, even after one more try.", _NOT_PREPARED
    ),
    "timed_out": FixSentence("This did not finish in time.", _NOT_PREPARED),
    "nexovue_error": FixSentence(
        "This stopped because of a fault in Nexovue." + _ASK_AGAIN, _NOT_PREPARED
    ),
}

# A general row has no code, so the actions on code refuse with this (AC73).
GENERAL_HAS_NO_CODE: Final = (
    "A general suggestion is an example, not a change to your code, so it cannot become a "
    "patch or a pull request."
)

# FR9's `no_code_location`, its first sentence alone: before Step 3 nobody can set a code
# location, so "An admin can set it" would be untrue. The line rung 2b's offer stands under.
NO_CODE_LOCATION_YET: Final = "Nexovue does not know where this service's code lives."

# Rung 2b's own words (spec FR33, FR37; design, *State by role*).
GENERAL_HEADING: Final = "General fix suggestion"
GENERAL_ACTION: Final = "Suggest a general fix"
GENERAL_LABEL: Final = (
    "Example pattern, not your code. Nexovue has not seen your code. Check before using."
)
GENERAL_OFFER_LEAD: Final = "Without access to the code:"
GENERAL_SEND: Final = (
    "Sends this finding's facts, and the language you picked, to {host}. No code is read or sent."
)
GENERAL_LEAD: Final = (
    "Written from this finding's facts only, in {language}. Not compiled and not run."
)
GENERAL_EARLIER_READING: Final = "Written from an earlier reading of this finding."
GENERAL_PROPOSING_REQUESTER: Final = "Writing a general suggestion. You may leave this page."
GENERAL_PROPOSING_VIEWER: Final = "A general suggestion is being prepared."
GENERAL_NONE_HEADING: Final = "No general suggestion"
SECTION_HEADING_UNAVAILABLE: Final = "Suggested fix not available"
SECTION_HEADING_PROPOSING: Final = "Writing a general suggestion"
SECTION_HEADING: Final = "Suggested fix"

assert set(REASON_SENTENCES) == set(GENERAL_REASONS), (
    "every reason a general row may store needs its sentence here"
)
