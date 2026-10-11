"""A CSV cell a spreadsheet would run is written as text (group 9, B2).

Customer text — a log line, a span name, a service name — that begins with `=`,
`+`, `-`, `@`, a tab or a line break is a formula to Excel and Sheets when the
file is opened: `=HYPERLINK(...)` in a log line becomes a link the reader is
invited to click. OWASP's rule is a leading `'`, which every spreadsheet reads as
"this is text" and hides. Applied to customer text only; a number we wrote
ourselves, such as a negative amount, is left as the number it is.

The console builds the same log file itself (`features/logs/log-export.ts`) and
applies the same rule, so the two files open identically.
"""

from __future__ import annotations

from typing import Final

FORMULA_LEADS: Final = ("=", "+", "-", "@", "\t", "\r", "\n")


def spreadsheet_safe(text: str) -> str:
    return f"'{text}" if text.startswith(FORMULA_LEADS) else text
