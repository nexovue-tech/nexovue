"""F4 plan T3.5 (AC30): every row of the spec's *Error cases*, listed row by row with the test
that produces its state and the test that shows its sentence.

The rows are read from `docs/features/ai-chatbot/spec.md` itself, so a row added there
without a test here fails, and so does a row listed here that the spec no longer has. Each
named test must exist where it is said to be. A row that a later step builds names that step
in place of a test, and the plan's step says so too; it is never silently missing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[3]
SPEC: Final = ROOT / "docs" / "features" / "ai-chatbot" / "spec.md"

API: Final = "apps/api/tests"
SCHEDULER: Final = "apps/scheduler/tests"
WEB: Final = "apps/web/src"
PANEL: Final = f"{WEB}/features/assistant/AssistantPanel.test.ts"
STATES: Final = f"{WEB}/features/assistant/states.test.ts"
STEPS: Final = f"{WEB}/features/assistant/step-links.test.ts"
ANSWER: Final = f"{WEB}/features/assistant/answer.test.ts"

# (file, the test's own name as written there). Python: a `def` name; web: the case's title,
# or for an `it.each` row, its row label.
Reference = tuple[str, str]

PRODUCED_AND_SHOWN: Final[dict[str, tuple[Reference | str, Reference | str]]] = {
    "AI off": (
        (
            f"{API}/test_assistant_api_live.py",
            "test_each_refusal_has_its_own_code_and_stores_nothing",
        ),
        (PANEL, '["ai_off", STANDING_WORDS.ai_off(false)]'),
    ),
    "Chat consent not given": (
        (
            f"{API}/test_assistant_api_live.py",
            "test_availability_without_consent_names_the_owners_it_waits_for",
        ),
        (PANEL, "no consent names the owners it waits for"),
    ),
    "Project opted out": (
        (
            f"{API}/test_assistant_tickets_live.py",
            "test_chat_turned_off_or_moved_ends_the_ticket_on_its_next_read",
        ),
        (PANEL, '["opted_out", STANDING_WORDS.opted_out(false)]'),
    ),
    "Hosted service, AI not offered": (
        (f"{WEB}/shared/assistant-context.test.ts", '["member", "hosted", false, "false"]'),
        (f"{WEB}/shared/assistant-context.test.ts", "whether chat is offered"),
    ),
    "AI container stopped": (
        (
            f"{SCHEDULER}/test_jobs_live.py",
            "test_with_no_worker_a_queued_question_ends_unavailable_by_its_queue_clock",
        ),
        (PANEL, '["unavailable", "Not available right now."]'),
    ),
    "Provider down, bad key": (
        (
            f"{SCHEDULER}/test_assistant_loop.py",
            "test_a_call_that_timed_out_charges_its_estimate_and_fails",
        ),
        (STATES, '"Bad key"'),
    ),
    "Provider rate limit": (
        (
            f"{SCHEDULER}/test_assistant_loop.py",
            "test_a_call_that_timed_out_charges_its_estimate_and_fails",
        ),
        (STATES, '"Provider rate limit"'),
    ),
    "Chat's share of the month gone": (
        (
            f"{SCHEDULER}/test_assistant_budget_live.py",
            "test_a_question_is_refused_below_its_reservation_and_a_critical_run_still_reserves",
        ),
        (PANEL, '"over_limit",'),
    ),
    "Daily limit reached": (
        (
            f"{API}/test_assistant_api_live.py",
            "test_the_daily_limit_is_counted_apart_from_conversations",
        ),
        (PANEL, "a refusal shows the API's sentence beside the box and keeps the draft (AC79)"),
    ),
    "No telemetry in this project yet": (
        (
            f"{API}/test_assistant_api_live.py",
            "test_a_project_that_never_reported_says_so_before_anyone_asks",
        ),
        (PANEL, "no telemetry yet offers the way to send it, and no starters"),
    ),
    "Service still learning its normal, or stopped reporting": (
        "Step 4 (T4.1): the answer's fact comes from `get_service_detection`'s state, which "
        "only a model's answer turns into a sentence; the bed's known-answer cases hold it",
        "Step 4 (T4.1), as above",
    ),
    "Install has no `vector` extension": (
        (
            f"{API}/test_setup_no_vector.py",
            "test_the_settings_say_keyword_only_and_search_answers_by_keyword",
        ),
        (f"{WEB}/features/aisettings/ChatSection.test.ts", "says search is keyword only, and why"),
    ),
    "Tool refused by rate limit": (
        (
            f"{SCHEDULER}/test_assistant_loop.py",
            "test_a_read_the_api_refused_for_now_or_could_not_serve_says_which",
        ),
        (STEPS, "says a read the rate limit refused was refused for now"),
    ),
    "Data aged out": (
        (
            f"{SCHEDULER}/test_assistant_loop.py",
            "test_a_range_past_raw_retention_ends_aged_out_in_code_with_no_read",
        ),
        (STEPS, "says an aged-out read in code's words, with the first day still kept"),
    ),
    "No rows": (
        (
            f"{SCHEDULER}/test_assistant_loop.py",
            "test_a_question_reads_through_its_ticket_and_answers_with_a_counted_label",
        ),
        (STEPS, "says an empty read is no matching rows in what was stored"),
    ),
    "Telemetry store down": (
        (
            f"{SCHEDULER}/test_assistant_loop.py",
            "test_a_read_the_api_refused_for_now_or_could_not_serve_says_which",
        ),
        (PANEL, "names the message as its reference, a look of its own and Ask again"),
    ),
    "Quote's log aged out": (
        (
            f"{API}/test_assistant_quotes_live.py",
            "test_a_record_gone_reads_gone_and_a_failed_read_reads_failed",
        ),
        (ANSWER, "says a quote's record is gone, and a failed read can be retried"),
    ),
    "Knowledge has no match": (
        (
            f"{API}/test_assistant_knowledge_live.py",
            "test_keyword_search_finds_a_runbook_and_says_it_searched_by_keyword",
        ),
        (STEPS, "says an empty knowledge search is no record of what is kept, with its days"),
    ),
    "Removed mid-question": (
        (
            f"{SCHEDULER}/test_assistant_loop.py",
            "test_a_ticket_refused_on_a_read_ends_the_question_with_no_further_call",
        ),
        (PANEL, '["refused", "Your access or AI settings changed during this question."]'),
    ),
    "Support view": (
        (f"{API}/test_assistant_api_live.py", "test_a_support_view_reads_no_conversation"),
        (f"{WEB}/shared/assistant-context.test.ts", '["owner", "self-hosted", true, "false"]'),
    ),
    "Deleted conversation": (
        (
            f"{API}/test_assistant_api_live.py",
            "test_stop_ends_the_question_and_its_ticket_but_moves_no_money",
        ),
        "Not rendered by a test yet: the panel's delete flow is held by `assistant.spec.ts`, "
        "written and not run (hardening)",
    ),
    "Panel poll refused or failing": (
        (PANEL, "a 403 renders inside the panel and stops polling (AC73)"),
        (PANEL, "three failed polls in a row say Lost contact, with Retry"),
    ),
}


def _spec_rows() -> list[str]:
    text = SPEC.read_text(encoding="utf-8")
    section = text.split("## Error cases", 1)[1].split("\n## ", 1)[0]
    # Rows start "| "; the divider starts "|-". The header comes first.
    rows = [line for line in section.splitlines() if line.startswith("| ")]
    return [row.split("|")[1].strip() for row in rows[1:]]


def test_every_spec_row_is_listed_and_nothing_else() -> None:
    assert _spec_rows() == list(PRODUCED_AND_SHOWN)


def _exists(reference: Reference) -> bool:
    path, name = reference
    source = (ROOT / path).read_text(encoding="utf-8")
    if path.endswith(".py"):
        return re.search(rf"def {re.escape(name)}\w*\(", source) is not None
    return name in source


@pytest.mark.parametrize("row", list(PRODUCED_AND_SHOWN))
def test_each_row_names_tests_that_exist_or_the_step_that_builds_it(row: str) -> None:
    for reference in PRODUCED_AND_SHOWN[row]:
        if isinstance(reference, str):
            assert re.search(r"Step \d|hardening", reference), (row, reference)
        else:
            assert _exists(reference), (row, reference)
