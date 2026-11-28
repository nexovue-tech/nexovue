"""Rung 2b's fixed request: instructions, schema and version (spec FR34, FR35; design,
*Rung 2b*; plan T2b.4).

**Text only, no AI code** (`fixes/__init__.py`): the AI role wraps these in F3's
`AIRequest`, as one trusted part, so the API's rung 1 and 2 path can import this package
and still reach nothing of `nexovue_platform.ai`. The request holds F3's `model_input` for
the version, the picked language from FR33's list, and on a second attempt one fixed retry
sentence (D51). **No `Untrusted` part exists:** nothing in it was written outside Nexovue's
code. The instructions are not a safety control; G1 to G9 are (D68). Changing any constant
here changes every request's bytes, so `PROMPT_VERSION` moves with it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from nexovue_platform.fixes.general_rules import (
    ANSWER_KEYS,
    CODE_LINE_MAX_CHARACTERS,
    CODE_MAX_LINES,
    OUTCOME_ABSTAIN,
    OUTCOME_SUGGESTION,
    STEP_MAX_CHARACTERS,
    STEPS_MAX,
    SUMMARY_MAX_CHARACTERS,
)
from nexovue_platform.fixes.records import (
    GENERAL_ABSTAIN_REASONS,
    RETRY_SHAPE,
    RETRY_TOO_LONG,
    GeneralLanguage,
)

PROMPT_VERSION: Final = "fix-general-1"
# The most one call may write (design: 1,500, a guess the bed replaces).
MAX_OUTPUT_TOKENS: Final = 1500
SCHEMA_NAME: Final = "general_fix_suggestion"

INSTRUCTIONS: Final = (
    "You are given the checked facts about one finding in a software system, as JSON under "
    "model_input, and a programming language under language. Names are stand-ins such as "
    "service_1; times are minutes from the finding's start. You have not seen the code of "
    "this system and must not write as if you had. Suggest one general pattern that often "
    "helps with problems shaped like these facts: a short summary, one to five steps, and "
    "one short example in the given language that uses only that language's standard "
    "library. Cite the ids of the facts the suggestion rests on. Do not include web "
    "addresses, host names, email addresses, file names, shell commands, install or "
    "download steps, or code that starts a process or runs text as code. Do not claim the "
    "suggestion is tested, safe, verified, approved, a root cause, or a fix. If the facts "
    "are too thin, need a person's decision, or do not look like a code problem, set "
    "outcome to abstain with the matching abstain_reason and return no suggestion. "
    "Return only JSON that fits the schema."
)

# What a second attempt is told, by its fixed code: never the model's own words (D51).
RETRY_SENTENCES: Final[dict[str, str]] = {
    RETRY_SHAPE: "The previous answer did not fit the schema. Return only JSON that fits it.",
    RETRY_TOO_LONG: (
        "The previous answer was over a length limit. Keep within every limit in the schema."
    ),
}

RESPONSE_SCHEMA: Final[Mapping[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": sorted(ANSWER_KEYS),
    "properties": {
        "outcome": {"type": "string", "enum": [OUTCOME_SUGGESTION, OUTCOME_ABSTAIN]},
        "abstain_reason": {"type": ["string", "null"], "enum": [*GENERAL_ABSTAIN_REASONS, None]},
        "summary": {"type": ["string", "null"], "maxLength": SUMMARY_MAX_CHARACTERS},
        "steps": {
            "type": "array",
            # None for an abstention, one to five for a suggestion: G1 holds the pairing,
            # which a schema cannot state per outcome in every format.
            "maxItems": STEPS_MAX,
            "items": {"type": "string", "maxLength": STEP_MAX_CHARACTERS},
        },
        "example": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["language", "code"],
            "properties": {
                "language": {"type": "string"},
                "code": {
                    "type": "string",
                    "maxLength": CODE_MAX_LINES * (CODE_LINE_MAX_CHARACTERS + 1),
                },
            },
        },
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
}


def request_document(
    model_input: Mapping[str, Any], language: GeneralLanguage, retry_code: str | None = None
) -> dict[str, Any]:
    """The one trusted part, before F3's canonical JSON makes it bytes: exactly the
    version's `model_input` and the language, and a retry's fixed sentence (FR34, AC68)."""
    document: dict[str, Any] = {"model_input": dict(model_input), "language": language}
    if retry_code is not None:
        document["retry"] = RETRY_SENTENCES[retry_code]
    return document
