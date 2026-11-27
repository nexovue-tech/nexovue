"""Every sentence F5 says about a suggestion, by code (design, *Every sentence*; spec AC8).

One constant, so a code with no sentence fails a test rather than reaching a page as a raw
code, and AC37's banned words are checked once, here. Rung 2b's rows (plan T2b.7), rung
3's from Step 3 (T3.7), and Step 5's patch, verdict and deleted code. `requester` is the
line for someone who may ask (owner, admin, member); `viewer` for a viewer and a support
view, or None where they see nothing.
`{date}` and `{dependency}` are filled by the reader, from Nexovue's own values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from nexovue_platform.fixes.pack import LEFT_OUT_REASONS
from nexovue_platform.fixes.records import (
    BRANCH_CHANGED,
    BRANCH_NOT_OURS,
    CHANGED_SINCE_CHECK,
    CHECKS_REFUSED_AT_CLICK,
    CODE_DELETED_REASONS,
    DID_NOT_FINISH,
    DRAFT_REFUSALS,
    DRAFTS_NOT_ALLOWED,
    FIX_CHECK_REASONS,
    FIX_REASONS,
    FIX_VERDICTS,
    NEEDS_LIKELY_CAUSE,
    NO_DRAFT_SUPPORT,
    NO_WRITE_PERMISSION,
    PUBLIC_NOT_ALLOWED,
    PULL_REQUEST_NOT_OPEN,
    PULL_REQUEST_STATES,
    STALE_REASONS,
    VERDICT_MERGE_AS_IS,
    VERDICT_USEFUL_START,
    VERDICT_WRONG,
    WRITE_FAILURES,
    WRITES_OFF,
)


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


# K2 and K6 to K10 can mean a file in the repository holds planted text (design).
_PLANTED: Final = (
    " This can mean a file in this repository holds planted text. Tell an admin before "
    "asking again, and use the brief carefully."
)


def _rule_withheld(what: str, *, planted: bool, after: str = "") -> FixSentence:
    tail = _PLANTED if planted else after
    return FixSentence(f"The suggestion was withheld because it {what}.{tail}", _WITHHELD_VIEWER)


# What each of Nexovue's rules found, in the words the requester and an admin's notice share.
WITHHELD_RULE_WORDS: Final[dict[str, str]] = {
    "K1": "touched a file outside the list you agreed to",
    "K2": "tried to change a build, CI, dependency or agent instruction file",
    "K3": "its change did not match the file exactly once",
    "K4": (
        "tried to create or delete a file, or change a file's mode, which suggested fixes never do"
    ),
    "K5": "was too large",
    "K6": "added an address not already in the file",
    "K7": "added hidden or look-alike characters",
    "K8": "added a long encoded string",
    "K9": "added something shaped like a secret",
    "K10": "a line was too long to read, or hid text behind spaces",
}


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
    # FR9 row 13 (Step 7, BR8): one draft Nexovue can still see per finding.
    "draft_open": FixSentence(
        "A draft is open for this finding: {link}. Close it on GitHub to ask again.",
        "A draft is open for this finding: {link}.",
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
    # Step 4: the model's own answer, and Nexovue's rules on it (design, *Every sentence*).
    "not_in_these_files": FixSentence(
        "The model said: the answer is not in these files, and it named no other file it "
        "needed, or its extra rounds were used up. You can name other files.",
        _NO_CHANGE,
    ),
    "edit_did_not_apply": FixSentence(
        "The model's change did not match the file, even after one more try.",
        "Could not be applied.",
    ),
    "withheld_K1": _rule_withheld(WITHHELD_RULE_WORDS["K1"], planted=False, after=_ASK_AGAIN),
    "withheld_K2": _rule_withheld(WITHHELD_RULE_WORDS["K2"], planted=True),
    "withheld_K3": _rule_withheld(WITHHELD_RULE_WORDS["K3"], planted=False, after=_ASK_AGAIN),
    "withheld_K4": _rule_withheld(WITHHELD_RULE_WORDS["K4"], planted=False),
    "withheld_K5": _rule_withheld(
        WITHHELD_RULE_WORDS["K5"], planted=False, after=" Removing files may help."
    ),
    "withheld_K6": _rule_withheld(WITHHELD_RULE_WORDS["K6"], planted=True),
    "withheld_K7": _rule_withheld(WITHHELD_RULE_WORDS["K7"], planted=True),
    "withheld_K8": _rule_withheld(WITHHELD_RULE_WORDS["K8"], planted=True),
    "withheld_K9": _rule_withheld(WITHHELD_RULE_WORDS["K9"], planted=True),
    "withheld_K10": _rule_withheld(WITHHELD_RULE_WORDS["K10"], planted=True),
    "checks_refused_at_click": FixSentence(
        "This suggestion no longer passes Nexovue's rules. Do not apply it. Tell your admin.",
        _NOT_PREPARED,
    ),
    "project_deleted": FixSentence(
        "This project was deleted, so this stopped. Nothing more was sent.",
        "This project was deleted, so this stopped.",
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
ASKED_ROUND: Final = "Round {round} of at most 3."
ASKED_REFUSED: Final = "Asked for, and refused: {path}, {why}."
MODEL_WORDS_LABEL: Final = "The model's words, not checked by Nexovue"

# Step 4: writing, and the written suggestion (design, *State by role*, *Section parts*).
PROPOSING_HEADING: Final = "Writing a suggestion"
PROPOSING_REQUESTER: Final = (
    "Writing a suggestion. You may leave this page. The result will be here, and on the "
    "finding's timeline."
)
# The plain terms (founder decision 2026-11-27; docs/legal/suggested-fixes.md).
AS_IS: Final = "Provided as is, without warranty: you review and you merge."
PROPOSED_LEAD: Final = (
    "Suggested fix: {files} {files_word}, {lines} {lines_word}, in {folder}. Not compiled and "
    "not run. " + AS_IS
)
PROPOSED_VIEWER: Final = (
    "Code is shown to people who can ask for a suggested fix. Ask an owner, admin or member."
)
FACTS_FROM_COMMIT: Final = (
    "Line numbers and context are from the file as read at {commit}. Prepare patch or Open "
    "draft re-reads it."
)
CONTEXT_TOO_LONG: Final = "Context lines too long to store; Prepare patch shows them."
EXPLANATION_LABEL: Final = "The model's explanation, not checked by Nexovue"
NOT_CHECKED: Final = "Not compiled and not run by Nexovue. A subtle bug passes every rule."
NOT_COVERED: Final = (
    "Nexovue's lists of imports, network calls and comments do not cover {path}'s language."
)
WRITTEN_AT: Final = "Written {age}, from {commit}. {tokens} tokens used."
OUT_OF_DATE_HEADING: Final = "Out of date"
NOT_AVAILABLE_HEADING: Final = "Not available"
OWNER_LIMITS_CODE: Final = (
    "The owner limits quoted code to people with access to this repository on GitHub."
)
CHECK_MY_ACCESS: Final = "Check my access"
CHECK_RUNNING_HEADING: Final = "Checking your access"
CHECK_RUNNING: Final = "Asking GitHub what access you have to {repository}."
CHECK_TIMED_OUT: Final = "Re-reading the files took too long, so this stopped. Try again."
# Each flag's label beside its line (FR18).
FLAG_LABELS: Final[dict[str, str]] = {
    "new_import": "new import",
    "risky_call": "new call",
    "comment": "comment",
    "removed_check": "removed check",
    "test_file": "test file",
    "new_letters": "new letters",
}
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

# Why a check run refused or failed (design, *The check run*): every `FIX_CHECK_REASONS`.
# A patch check out of date, or refused by the rules, says what the suggestion's row says.
CHECK_REASON_SENTENCES: Final[dict[str, str]] = {
    "not_linked": REASON_SENTENCES["not_linked"].requester,
    "no_github_access": REASON_SENTENCES["no_github_access"].requester,
    "access_unknown": REASON_SENTENCES["access_unknown"].requester,
    "github_unavailable": REASON_SENTENCES["github_unavailable"].requester,
    "permission_missing": REASON_SENTENCES["permission_missing"].requester,
    "consent_withdrawn": "An owner withdrew agreement, so this stopped.",
    "nexovue_error": REASON_SENTENCES["nexovue_error"].requester,
    **{reason: REASON_SENTENCES[reason].requester for reason in STALE_REASONS},
    CHECKS_REFUSED_AT_CLICK: REASON_SENTENCES[CHECKS_REFUSED_AT_CLICK].requester,
}

# Step 7: why a draft cannot be opened here (design, *Every sentence*; FR10c's hidden
# button states). `writes_off` is Nexovue's own, while write access is off in code until
# the founder's go (ADR-097): its words are the founder's call (status, open decision 90).
DRAFT_REFUSAL_SENTENCES: Final[dict[str, str]] = {
    WRITES_OFF: (
        "Opening a draft pull request is switched off in this version of Nexovue, until its "
        "safety evaluation is done. Download the patch instead."
    ),
    DRAFTS_NOT_ALLOWED: (
        "An owner has not allowed draft pull requests for this repository. Download the "
        "patch instead."
    ),
    PUBLIC_NOT_ALLOWED: (
        "This repository is public. A branch would show that something is broken, and "
        "where. An admin may allow it."
    ),
    NO_WRITE_PERMISSION: (
        "The Nexovue suggestions App has no write access to this repository. An admin can "
        "raise it, and GitHub's organization owner accepts."
    ),
    NO_DRAFT_SUPPORT: (
        "GitHub refused a draft pull request for this repository. Download the patch instead."
    ),
    NEEDS_LIKELY_CAUSE: (
        "A draft needs this service's own signed deploy as its likely cause. Download the "
        "patch instead."
    ),
}
CHECK_REASON_SENTENCES.update(DRAFT_REFUSAL_SENTENCES)
# Why a draft was not opened, on `last_write_failure` (design, *Every sentence*).
WRITE_FAILURE_SENTENCES: Final[dict[str, str]] = {
    **DRAFT_REFUSAL_SENTENCES,
    CHANGED_SINCE_CHECK: "A file changed after your check, so nothing was written. Check again.",
    BRANCH_NOT_OURS: (
        "A branch with this name exists and was not made by this suggestion. Nothing was "
        "written. Delete it on GitHub to try again."
    ),
    DID_NOT_FINISH: (
        "Opening the draft did not finish. A branch may already exist on GitHub: {branch}."
    ),
    "no_github_access": REASON_SENTENCES["no_github_access"].requester,
    "not_linked": REASON_SENTENCES["not_linked"].requester,
    "access_unknown": REASON_SENTENCES["access_unknown"].requester,
    "permission_missing": REASON_SENTENCES["permission_missing"].requester,
    "github_unavailable": REASON_SENTENCES["github_unavailable"].requester,
    "consent_withdrawn": "An owner withdrew agreement while this was opening, so it stopped.",
}
# Step 8: why Run CI did not move the branch, on an opened draft's `last_write_failure`
# (spec FR25; design, *Run CI*). New wording, the founder's call (status, open decision).
CI_FAILURE_SENTENCES: Final[dict[str, str]] = {
    WRITES_OFF: (
        "Running CI from Nexovue is switched off in this version of Nexovue, until its safety "
        "evaluation is done."
    ),
    DRAFTS_NOT_ALLOWED: (
        "An owner no longer allows draft pull requests for this repository, so CI was not "
        "asked for."
    ),
    NO_WRITE_PERMISSION: (
        "The Nexovue suggestions App has no write access to this repository, so CI was not "
        "asked for."
    ),
    PUBLIC_NOT_ALLOWED: (
        "This repository is public and an admin has not allowed it, so CI was not asked for."
    ),
    BRANCH_CHANGED: (
        "This branch has changed since Nexovue's last commit, so CI was not asked for. Check "
        "GitHub."
    ),
    PULL_REQUEST_NOT_OPEN: "This pull request is no longer open, so CI was not asked for.",
    "no_github_access": REASON_SENTENCES["no_github_access"].requester,
    "not_linked": REASON_SENTENCES["not_linked"].requester,
    "access_unknown": REASON_SENTENCES["access_unknown"].requester,
    "permission_missing": REASON_SENTENCES["permission_missing"].requester,
    "github_unavailable": REASON_SENTENCES["github_unavailable"].requester,
}
WRITE_FAILURE_SENTENCES.update(
    {code: CI_FAILURE_SENTENCES[code] for code in (BRANCH_CHANGED, PULL_REQUEST_NOT_OPEN)}
)
# What GitHub says of the draft, followed by F5's poller (design, *Every sentence*).
PULL_REQUEST_SENTENCES: Final[dict[str, FixSentence]] = {
    "open": FixSentence(
        "A draft pull request is open: {link}.", "A draft pull request is open: {link}."
    ),
    "merged": FixSentence(
        "This pull request was merged: {link}.", "This pull request was merged: {link}."
    ),
    "closed": FixSentence(
        "This pull request was closed without merging: {link}.",
        "This pull request was closed without merging: {link}.",
    ),
    "not_opened": FixSentence(
        "No pull request was opened. A branch may be left on GitHub: {branch}. You can "
        "delete it here.",
        "No pull request was opened.",
    ),
    "untracked": FixSentence(
        "Nexovue can no longer see this pull request; check GitHub.",
        "Nexovue can no longer see this pull request; check GitHub.",
    ),
}
OPEN_DRAFT: Final = "Open draft pull request"
CONFIRM_DRAFT: Final = "Open the draft"
DELETE_BRANCH: Final = "Delete this branch"
DRAFT_RUNNING_HEADING: Final = "Re-reading the files"
DRAFT_READY_HEADING: Final = "Ready to confirm"
OPENING_HEADING: Final = "Opening the draft"
OPENING_REQUESTER: Final = "Opening the draft."
OPENING_SLOW: Final = "This did not finish. A branch may already exist on GitHub: {branch}."
OPENING_VIEWER: Final = "A draft is being opened."
OPENED_HEADING: Final = "Draft pull request"
# FR26, on the page: what holding CI means.
CI_HELD_ON_PAGE: Final = (
    "Nexovue's commit asked GitHub Actions to wait. Anything pushed since is not tracked "
    "here; check GitHub."
)
# Step 8, Run CI (spec FR25, FR26; design, *Run CI*). FR25's confirmation says it runs the
# model's code in the customer's CI, possibly with secrets, after the diff is read.
RUN_CI: Final = "Run CI"
RUN_CI_CONFIRM: Final = (
    "This runs the model's code in this repository's CI, possibly with its secrets. Read the "
    "diff on GitHub first."
)
RUN_CI_CONFIRM_ACTION: Final = "Run CI now"
CI_REQUESTED: Final = "Asking GitHub to run CI: Nexovue is adding a commit with no skip marker."
CI_DID_NOT_FINISH: Final = "Asking GitHub to run CI did not finish. You can ask again."
CI_PUSHED: Final = (
    "CI was asked for: Nexovue's newest commit on this branch has no skip marker. Anything "
    "pushed since is not tracked here; check GitHub."
)
# FR24's confirmation, each asserted by exact string (AC37e, AC62).
CONFIRM_FACTS: Final = "{repository}, branch {branch}: {files} {files_word}, {lines} {lines_word}."
CONFIRM_HOLDS: Final = (
    "Asks GitHub Actions to hold `push` and `pull_request` runs. Other triggers, other CI "
    "systems and apps connected to this repository may still run."
)
CONFIRM_CI_ON_OPEN: Final = (
    "An owner chose to run CI when a draft opens here, so CI runs the model's code, possibly "
    "with this repository's secrets, before anyone reviews it."
)
CONFIRM_CASE: Final = "This also starts a case on this finding."
CONFIRM_AUTHOR: Final = (
    "On GitHub this pull request's author is the App, so the person who requested it can "
    "approve it. We have not yet shown that a GitHub rule can stop that."
)
CONFIRM_LOGIN: Final = (
    "Your GitHub login is written into the pull request, and stays on GitHub if you leave."
)
CONFIRM_REACH: Final = (
    "Write access reaches every repository you installed this App on; Nexovue restricts where "
    "drafts open."
)
NOTE_LABEL: Final = "Why this change (optional, one line)"
NOTE_PUBLIC_CONFIRM: Final = "This repository is public. Put my note in the pull request."
NOTE_REFUSED: Final = (
    "Write one plain line of at most 200 characters, with no address, email or markup."
)
NOTE_NOT_CONFIRMED: Final = (
    "This repository is public: confirm that your note may go into the pull request, or "
    "leave it out."
)
NOT_YOUR_CHECK: Final = "Check this yourself first, since GitHub's answer is about you."

# Step 5: the patch (design, *State by role*, check rows; spec FR20a, FR21, UR6).
PREPARE_PATCH: Final = "Prepare patch"
PATCH_RUNNING_HEADING: Final = "Re-reading the files"
PATCH_RUNNING: Final = "Re-reading the files and re-applying Nexovue's rules."
PATCH_READY_HEADING: Final = "Ready to confirm"
PATCH_BUILT_AGAINST: Final = (
    "Built against {commit}, the default branch's head when it was re-read. Apply it with "
    "git apply. It can be downloaded until {until}; prepare it again after that."
)
PATCH_NOT_PREPARED: Final = (
    "Prepare the patch first: it is rebuilt from the repository as it is now."
)
CHECK_EXPIRED: Final = "This check is more than 15 minutes old. Check again."
NO_WRITTEN_SUGGESTION: Final = "There is no written suggestion here to prepare or answer about."
FACTS_FROM_HEAD: Final = (
    "Line numbers and context are from the default branch's head at {commit}, re-read for "
    "your patch."
)
MOVED_ON: Final = "This file moved on since the suggestion."
MOVED_LEAD: Final = (
    "A file moved on since the suggestion, and every change still applies. The changes below "
    "are re-read from the default branch's head."
)

# FR29, UR11: "Was this suggestion useful?", and what an answer does.
VERDICT_QUESTION: Final = "Was this suggestion useful?"
VERDICT_LABELS: Final[dict[str, str]] = {
    VERDICT_MERGE_AS_IS: "Yes, nearly as it is",
    VERDICT_USEFUL_START: "Yes, as a start",
    VERDICT_WRONG: "No, it was wrong",
}
VERDICT_RECORDED: Final = "Recorded. This changes nothing else."

# BR9: a suggestion whose stored code is gone (design, *State by role*, `code_deleted_at`).
CODE_DELETED_HEADING: Final = "Code deleted"
CODE_DELETED_SENTENCE: Final = (
    "Code deleted on {when}, because {why} Backups that hold it age out by {until}. What the "
    "AI provider was sent follows your contract with them."
)
CODE_DELETED_WHY: Final[dict[str, str]] = {
    "ended_short": "the suggestion ended without a change.",
    "pull_request_done": "its pull request ended 7 days ago.",
    "aged_out": "it was written 30 days ago.",
    "consent_withdrawn": "an owner withdrew agreement to send this code.",
    "disconnected": "the Nexovue suggestions App was disconnected.",
    "ai_off": "AI was switched off.",
    "access_gone": "Nexovue could not reach this repository on GitHub for 72 hours.",
    "restored": "Nexovue was restored from a backup.",
    "project_deleted": "the project was deleted.",
}

assert set(CHECK_REASON_SENTENCES) == set(FIX_CHECK_REASONS), (
    "every reason a check run may store needs its sentence here"
)
assert set(REASON_SENTENCES) == set(FIX_REASONS), (
    "every reason a suggestion may store needs its sentence here"
)
assert set(LEFT_OUT_WHY) == set(LEFT_OUT_REASONS), "every left-out reason needs its sentence here"
assert set(VERDICT_LABELS) == set(FIX_VERDICTS), "every verdict needs its words here"
assert set(CODE_DELETED_WHY) == set(CODE_DELETED_REASONS), (
    "every reason code is deleted needs its words here"
)
assert set(DRAFT_REFUSAL_SENTENCES) == set(DRAFT_REFUSALS), "every draft refusal needs words"
assert set(WRITE_FAILURE_SENTENCES) == set(WRITE_FAILURES), "every write failure needs words"
assert set(CI_FAILURE_SENTENCES) <= set(WRITE_FAILURES), "Run CI stores only write failures"
assert set(PULL_REQUEST_SENTENCES) == set(PULL_REQUEST_STATES), "every state needs words"
