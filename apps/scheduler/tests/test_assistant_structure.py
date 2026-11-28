"""F4 plan T2.10a, AC81: model words reach no one but the person who asked.

Only chat's own conversation code reads `assistant_messages.answer`, and nothing in
detection, alerts, findings or the delivery path names it: a stored answer can never become
an alert, a webhook or an issue. Read from the source, so a new reader fails here the day it
is written.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

REPOSITORY: Final = Path(__file__).resolve().parents[3]
SOURCES: Final = (
    REPOSITORY / "apps" / "api" / "src" / "nexovue_api",
    REPOSITORY / "apps" / "scheduler" / "src" / "nexovue_scheduler",
    REPOSITORY / "packages" / "py-platform" / "src" / "nexovue_platform",
)
# Where reading or writing a stored answer is the job, each with why.
ANSWER_READERS: Final = {
    # The panel's routes: the person's own conversation, and their export.
    "apps/api/src/nexovue_api/assistant/service.py",
    # The worker writes the checked answer through `assistant_finish_message`.
    "apps/scheduler/src/nexovue_scheduler/insight/assistant.py",
    "apps/scheduler/src/nexovue_scheduler/insight/assistant_store.py",
}
_NAMES_ANSWER: Final = re.compile(
    r"AssistantMessage\.answer|\bassistant_messages\b[^\n]*\banswer\b"
)
_NEVER_NAME_CHAT: Final = ("detection", "alerts", "findings", "evidence", "notifications", "cases")


def _python() -> list[Path]:
    return [path for source in SOURCES for path in source.rglob("*.py")]


def test_only_chat_s_own_code_reads_a_stored_answer() -> None:
    readers = {
        str(path.relative_to(REPOSITORY))
        for path in _python()
        if _NAMES_ANSWER.search(path.read_text(encoding="utf-8"))
    }
    assert readers <= ANSWER_READERS, sorted(readers - ANSWER_READERS)


def test_detection_alerts_and_findings_never_name_chat_s_tables() -> None:
    offenders = [
        str(path.relative_to(REPOSITORY))
        for path in _python()
        if any(part in _NEVER_NAME_CHAT for part in path.parts)
        and (
            "assistant_messages" in (text := path.read_text(encoding="utf-8"))
            or "AssistantMessage" in text
        )
    ]
    assert offenders == []
