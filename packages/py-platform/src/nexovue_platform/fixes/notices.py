"""Which of Nexovue's rules show admins a notice when they withhold a suggestion (F5 spec UR12,
AC39, AC63; design, *The summary*; open decision 89).

K2, K7, K9 and K10 may mean a file in the repository holds a planted instruction, so each
refusal shows admins a notice, with the files read and no text. K6 (a new address) and K8 (a
long encoded string) also refuse honest code, so they stay quiet until the founder has read
T6.3's replay report of how often they do (AC63). That reading is recorded here, as a date, in
a reviewed commit with `status.md`'s T6.3 line, as the write gate records its go: no setting,
environment variable or row can turn the two on.
"""

from __future__ import annotations

from datetime import date
from typing import Final

from nexovue_platform.fixes.sentences import WITHHELD_RULE_WORDS

ALWAYS: Final = ("K2", "K7", "K9", "K10")
HELD_UNTIL_REPLAY_READ: Final = ("K6", "K8")
# The day the founder read T6.3's K6 and K8 replay report (AC63). None: not yet read.
REPLAY_REPORT_READ: Final[date | None] = None

_RULE_ORDER: Final = tuple(WITHHELD_RULE_WORDS)


def notice_rules(read: date | None = REPLAY_REPORT_READ) -> tuple[str, ...]:
    """The rules whose refusals show a notice, in the rules' own order."""
    shown = set(ALWAYS) | (set(HELD_UNTIL_REPLAY_READ) if read is not None else set())
    return tuple(rule for rule in _RULE_ORDER if rule in shown)


def notice_sentence(rule: str) -> str:
    """The notice's words: the rule and what it found, never the suggestion's text."""
    return (
        f"Rule {rule} withheld a suggestion because it {WITHHELD_RULE_WORDS[rule]}. A planted "
        "instruction in a file of this repository may look like that."
    )
