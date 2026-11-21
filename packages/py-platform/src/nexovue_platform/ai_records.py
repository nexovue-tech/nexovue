"""F3: the closed lists of the `ai_*` tables (design, *Data and migration impact*).

Words the database checks, so the API's models, the migration and the `insight` role name
them one way. **Not AI code**, and deliberately outside `nexovue_platform.ai`, which the
no-AI suites deny: every module that imports the models would otherwise "reach" it. The
provider code itself (wire formats, the request and response, the validator) is
`nexovue_platform.ai`, from plan Step 4.
"""

from __future__ import annotations

from typing import Final

# The three plain-HTTP formats a connection speaks (FD3, design *Provider interface*).
WIRE_FORMATS: Final = ("openai_chat", "anthropic_messages", "bedrock_converse")

# Why a connection is turned off (A53, A80): a person, or the consent ending.
OFF_REASONS: Final = ("person", "consent_ended")

# What a run was for: F3's explanation and connection test (A51), F4's chat, F5's fix
# (foundation, section 2, `purpose`), and F4's embedding calls, joined in migration 0078.
RUN_PURPOSES: Final = ("explain", "check", "chat", "fix", "embed")

# How a run ended (design, *Failure handling*). `started` is written before the call, so a
# killed child leaves a row the sweep turns into `died`. `not_found` (a model or path the
# provider does not know) and `bad_request` (a 400) joined in migration 0071, so a failed
# connection test can say "model not found" apart from a bad key (spec, *Error cases*).
RUN_OUTCOMES: Final = (
    "started",
    "done",
    "abstained",
    "unavailable",
    "limited",
    "bad_key",
    "refused",
    "too_long",
    "invalid",
    "died",
    "not_found",
    "bad_request",
)

# A connection's call time limit, in seconds (design, *Data and migration impact*).
CALL_SECONDS_MIN: Final = 30
CALL_SECONDS_MAX: Final = 300
CALL_SECONDS_DEFAULT: Final = 100

# What the AI tables keep only for a while (design, *Retention*: 13 months, a guess), read by
# the scheduler's expiry sweep: (table, the columns that name a row, what makes it lapsed).
# A run row holds no content (FD9); a year and a month lets an admin compare a month with
# the same month a year before.
EXPIRED: Final = (
    ("ai_runs", "id", "started_at < now() - interval '13 months'"),
    ("ai_usage_monthly", "org_id, month", "month < now() - interval '13 months'"),
)
