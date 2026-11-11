"""The limits a run meets before it spends, in order (design, *Limits, in order, at the point
of spending*; spec BR13, FR34, FR40, FR51).

Pure arithmetic, shared by the AI role, which enforces them, and the API, which refuses a
click at the cap and says when the next is allowed. The statements that take a reservation
live beside the AI account's other statements (`nexovue_scheduler.insight.store`), where the
tenancy structure test reads them.

1. **The run cap:** at most `RUNS_PER_FINDING` decided runs of one finding in
   `RUN_CAP_WINDOW`, counted from first attempts, so an invalid answer and its retry are one.
2. **The input limit:** an input estimated over `MAX_INPUT_TOKENS` ends `too_long` before
   anything is reserved.
3. **The monthly limit:** a run reserves its estimate plus the most it may write, against the
   share its finding's severity may use; a run that dies keeps what it reserved.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Final

from nexovue_platform.ai.contract import estimate_tokens
from nexovue_platform.ai.explain_prompt import MAX_OUTPUT_TOKENS
from nexovue_platform.evidence import MAX_INPUT_TOKENS

RUNS_PER_FINDING: Final = 3
RUN_CAP_WINDOW: Final = timedelta(hours=24)
# Warning findings may use this much of the month's limit, critical ones all of it (FR40,
# A10; 20% kept for critical findings is a guess).
WARNING_SHARE: Final = 0.8
CRITICAL_SHARE: Final = 1.0


def share_for(severity: object) -> float:
    """The part of the limit a finding may spend into. Anything but `critical` is a warning:
    the severity reaches the AI role through `model_input`, so an unknown value is the
    stricter one."""
    return CRITICAL_SHARE if severity == "critical" else WARNING_SHARE


def ceiling(monthly_limit: int, severity: object) -> int:
    """Used plus reserved tokens may not pass this after a reservation."""
    return int(monthly_limit * share_for(severity))


def input_estimate(body: bytes) -> int:
    """Tokens a request's body may hold, estimated cautiously from its bytes (design)."""
    return estimate_tokens(len(body))


def too_long(input_tokens: int) -> bool:
    return input_tokens > MAX_INPUT_TOKENS


def reservation(input_tokens: int) -> int:
    """What a run takes from the month before it calls: its input and the most it may write."""
    return input_tokens + MAX_OUTPUT_TOKENS


def month_of(moment: datetime) -> date:
    """The usage row a moment counts toward: the first of its month, UTC (design)."""
    return date(moment.year, moment.month, 1)


def first_of_next_month(month: date) -> date:
    """When a month's limit resets: "Plainer wording is paused until {date}"."""
    return date(month.year + month.month // 12, month.month % 12 + 1, 1)


def runs_left(decided_in_window: int) -> int:
    return max(0, RUNS_PER_FINDING - decided_in_window)


def next_run_at(decided_started: list[datetime]) -> datetime | None:
    """When a finding at its cap may run again: the oldest counted run leaves the window."""
    if len(decided_started) < RUNS_PER_FINDING:
        return None
    oldest_counted = sorted(decided_started)[-RUNS_PER_FINDING]
    return oldest_counted + RUN_CAP_WINDOW
