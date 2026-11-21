"""Every sentence F5 says about a suggestion, by code (design, *Every sentence*; spec AC8).

One constant, so a code with no sentence fails a test rather than reaching a page as a raw
code, and AC37's banned words are checked once, here. Rung 2b's rows (plan T2b.7), and
rung 3's as far as Step 3 (T3.7). `requester` is the line for someone who may ask (owner, admin,
member); `viewer` for a viewer and a support view, or None where they see nothing.
`{date}` and `{dependency}` are filled by the reader, from Nexovue's own values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from nexovue_platform.fixes.pack import LEFT_OUT_REASONS
from nexovue_platform.fixes.records import FIX_REASONS


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
    "cause_is_elsewhere": FixSentence(
        "{dependency} has its own earlier finding. See it first. You can still name files here.",
        "{dependency} has its own earlier finding.",
    ),
    "no_code_location": FixSentence(
        "Nexovue does not know where this service's code lives. An admin can set it.",
        "Nexovue does not know where this service's code lives.",
    ),
    "no_read_permission": FixSentence(
        "Nexovue cannot read this repository. An admin sets that up, and GitHub's "
        "organization owner accepts.",
        "Nexovue cannot read this repository.",
    ),
    "no_consent": FixSentence(
        "An owner has not agreed to send this repository's code to {host}. Ask an owner.",
        "An owner has not agreed to send this repository's code to {host}.",
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
    # Rung 3's pack job, sweep and person (Step 3).
    "no_usable_file": FixSentence(
        "No file named by the error exists in {folder}. If this is minified or compiled "
        "browser code, that is why. You can name the files yourself.",
        "No usable file was found.",
    ),
    "stack_aged_out": FixSentence(
        "The error's stack has aged out of raw data. You can name the files yourself.",
        "The stack has aged out.",
    ),
    "no_stack": FixSentence(
        "This error arrived with no stack. You can name the files yourself.",
        "The error has no stack.",
    ),
    "repository_too_large": FixSentence(
        "This folder is too large for Nexovue to list. An admin can set a narrower folder.",
        "The folder is too large.",
    ),
    "cancelled": FixSentence("You cancelled this. Nothing was sent.", None),
    "lapsed": FixSentence(
        "Nothing was sent, because nobody confirmed the file list within 15 minutes.", None
    ),
    "github_unavailable": FixSentence("GitHub did not answer.", _NOT_PREPARED),
    "permission_missing": FixSentence(
        "The Nexovue suggestions App lost its access. An admin can restore it.", _NOT_PREPARED
    ),
    "not_linked": FixSentence(
        "Connect your GitHub account so GitHub can tell us what access you have to this "
        "repository.",
        _NOT_PREPARED,
    ),
    "no_github_access": FixSentence(
        "GitHub says you do not have the access to {repository} that this needs.", _NOT_PREPARED
    ),
    "access_unknown": FixSentence(
        "GitHub could not say who has access, so this was refused.", _NOT_PREPARED
    ),
    # Out of date (FR21); written from Step 4's check run.
    "rules_changed": FixSentence(
        "Out of date: Nexovue's rules changed. Ask again.", "Out of date."
    ),
    "mapping_changed": FixSentence(
        "Out of date: this service's code location changed. Ask again.", "Out of date."
    ),
    "cause_changed": FixSentence(
        "Out of date: the likely cause changed. Ask again.", "Out of date."
    ),
    "edit_no_longer_applies": FixSentence(
        "Out of date: the file changed. Ask again.", "Out of date."
    ),
}

# Reasons shown neutrally, with no fix id and no failure tone (design, *State by role*).
NEUTRAL_REASONS: Final = frozenset({"cancelled", "lapsed"})

# A general row has no code, so the actions on code refuse with this (AC73).
GENERAL_HAS_NO_CODE: Final = (
    "A general suggestion is an example, not a change to your code, so it cannot become a "
    "patch or a pull request."
)

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

# Rung 3's words, Step 3 (design, *State by role*, *GitHub account link*).
SOURCE_ACTION: Final = "Ask for a suggested fix"
SOURCE_READY_LABELS: Final[dict[str, str]] = {
    "likely_cause": "Starts from the likely cause above.",
    "stack_only": "No likely cause was established; this starts from the error's stack.",
    "named_files": "Starts from the files you name.",
}
NAME_FILES_HEADING: Final = "Name the files"
NAME_FILES_LEAD: Final = (
    "This finding has no error stack to start from. Name up to 5 files you think are "
    "involved, as paths inside {folder}."
)
EVIDENCE_SLOW: Final = (
    "This is taking longer than it should. The deploy line and the brief still work. You can "
    "name the files yourself."
)
GATHERING_HEADING: Final = "Reading files"
GATHERING_REQUESTER: Final = (
    "Reading files. You will need to confirm the file list here. It waits 15 minutes."
)
BEING_PREPARED_VIEWER: Final = "A suggested fix is being prepared."
GATHERED_HEADING: Final = "Files to send"
GATHERED_FORGEABLE: Final = (
    "These files came from the error's stack, which an outsider can forge. If one looks "
    "unrelated to this error, remove it."
)
GATHERED_SEND_HOST: Final = "To {host}:"
GATHERED_SEND: Final = "Send these files"
TOP_FILE_LEFT_OUT: Final = (
    "The file at the top of the stack was left out. A change made without it may be "
    "confidently wrong. Send anyway?"
)
ASKED_FOR_MORE: Final = "The AI asks for {count} more files. Send them?"
# Step 3 stops at Send (plan T3.11): the provider call is Step 4's.
SEND_COMES_NEXT: Final = (
    "Sending files to the AI provider comes in a later version of Nexovue. Nothing was sent."
)
NO_SUGGESTION_HEADING: Final = "No suggestion"
COULD_NOT_FINISH_HEADING: Final = "Could not finish"
LINK_HEADING: Final = "Connect your GitHub account"
LINK_SENTENCE: Final = (
    "Connect your GitHub account so GitHub can tell us what access you have to this "
    "repository. We keep your GitHub user name and id, and no token. Your GitHub login is "
    "written into any pull request you open, and stays on GitHub if you leave."
)
SUPPORT_VIEW_NO_CODE: Final = "A support view does not show customer code."

# Why each file is on the list (design, *Not-checked, files read*), filled from Nexovue's
# own values: a path in the tree and a line number, never a stack's words.
FILE_WHY: Final[dict[str, str]] = {
    "stack": "named at line {line} of the error's stack",
    "stack_no_line": "named by the error's stack",
    "named": "named by you",
    "imported_by": "imported by {path}",
    "test_for": "test for {path}",
    "asked": "asked because of {path}, line {line}",
    "asked_no_line": "asked for by the AI",
}
# Why an existing file was left out (FR12, FR13, AC45): every `pack.LEFT_OUT_REASONS`.
LEFT_OUT_WHY: Final[dict[str, str]] = {
    "secret_or_key": "never sent: secrets and keys",
    "ci": "never sent: CI configuration",
    "agent_instructions": "never sent: instructions for coding agents",
    "excluded_by_owner": "kept back by an owner",
    "not_source": "not a source file Nexovue sends",
    "configuration": "never sent: configuration, unless an owner allows this path",
    "not_a_regular_file": "not a regular file",
    "not_text": "not text",
    "secret_pattern": "holds something shaped like a secret",
    "too_large": "too large to send",
    "no_line_for_window": "too large to send whole, and the error names no line in it",
    "over_file_cap": "over the limit of 12 files",
    "over_byte_cap": "over the limit of 64 KiB sent in all",
}
TYPED_MISS: Final = "{path} was not found in {folder}."
TYPED_REFUSED: Final = (
    "{path} is not a path inside the folder: no leading slash, no `..`, no control character."
)
WHOLE_REPOSITORY: Final = "the whole repository"
COMMIT_UNKNOWN: Final = "The running version is not known, so this read the default branch."
MOVED_ON_DEFAULT: Final = "This file has changed on the default branch since the running version."

assert set(REASON_SENTENCES) == set(FIX_REASONS), (
    "every reason a suggestion may store needs its sentence here"
)
assert set(LEFT_OUT_WHY) == set(LEFT_OUT_REASONS), "every left-out reason needs its sentence here"
