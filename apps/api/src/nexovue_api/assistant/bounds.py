"""What a tool result may carry (F36, FR34, AC35).

A model's context is the scarce thing, and a question like "what is failing?"
can match a day of logs. Every tool answers within two caps — rows and
characters — and a cut result *says* it was cut and how to narrow, because a
silently truncated answer is one a model will reason from as if it were whole.
"""

from __future__ import annotations

import json
from typing import Any, Final

# A hundred rows is enough to see a pattern and few enough to read.
MAX_ROWS: Final = 100
# 32 KiB of text, counted after rendering: the caps are a pair, and a hundred
# rows of long log bodies would blow the context that a hundred short ones do not.
MAX_CHARACTERS: Final = 32 * 1024

NARROW_BY: Final = "Narrow by service, project or a shorter time window."


def bounded(rows: list[dict[str, Any]], *, total: int | None = None, subject: str) -> str:
    """Render rows as JSON, cut to the caps, saying so when it cut.

    `total` is how many there were, when the caller knows; without it the count
    of rows given is all that can be claimed, and the note says "at least".
    """
    kept = rows[:MAX_ROWS]
    body = json.dumps({subject: kept}, default=str, ensure_ascii=False)

    if len(body) > MAX_CHARACTERS:
        # Drop rows until it fits, so the result is always valid JSON — never a
        # string cut mid-token that a model has to guess the end of.
        while kept and len(body) > MAX_CHARACTERS:
            kept = kept[: len(kept) - max(1, len(kept) // 10)]
            body = json.dumps({subject: kept}, default=str, ensure_ascii=False)

    if len(kept) == len(rows) and (total is None or total <= len(rows)):
        return body

    counted = str(total) if total is not None else f"at least {len(rows)}"
    return json.dumps(
        {
            subject: kept,
            "cut": f"Showing {len(kept)} of {counted} {subject}. {NARROW_BY}",
        },
        default=str,
        ensure_ascii=False,
    )
