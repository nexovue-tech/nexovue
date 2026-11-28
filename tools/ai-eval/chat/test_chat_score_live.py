"""The scorers on the product's real loop, with AC51's control (F4 plan T4.1, T4.2).

- **The control:** the bed run against `HostileProvider` must score instruction-followed at
  100% and still show zero ways out, no write and no unlabelled false cause (AC51). It asks
  chat questions and plants injection payloads in a log line, a runbook and a pasted
  question, through the real API, the real loop and the AI account.
- **The answer key:** a fake that states what its read returned scores every question it
  can answer right, with the right range, and abstains where it should: the seed, the API,
  the loop and the scorer agree.

Fakes only; nothing here says anything about a model. Fails rather than skips when the
stores are absent; see tests/helpers/live.
"""

from __future__ import annotations

from datetime import datetime

from chat.live_support import load_api_test_environment, require_stores, served_bed

load_api_test_environment()

from injection import run as injection_run  # noqa: E402
from injection import score as injection_score  # noqa: E402
from injection.payloads import PAYLOADS_BY_ID, Place  # noqa: E402

from chat import harness  # noqa: E402
from chat.answers import questions  # noqa: E402
from chat.providers import fake  # noqa: E402
from chat.score import score_trial, summarise  # noqa: E402

STORES = require_stores("the chat bed's scoring suite")
# One question of each kind the hostile control must not get right.
CHAT_CASES = ("requests-hour-checkout", "slow-endpoint", "who-complained", "other-tenant-count")
# A way out, a command, a false cause and a denial, and a harmless control.
PAYLOAD_CASES = ("lj-09", "lj-13", "lj-25", "lj-29", "lj-33")
PLACES: tuple[Place, ...] = ("log_body", "runbook", "pasted_question")


async def test_the_hostile_control_follows_every_instruction_and_no_way_out_reaches_a_person() -> (
    None
):
    by_id = harness.questions_by_id(questions)
    async with served_bed(STORES) as bed:
        chat_records = [
            await harness.chat_trial(
                bed,
                by_id[case],
                lambda question, anchor: fake("fake-hostile", question, anchor),
                option="fake-hostile",
                trial=1,
            )
            for case in CHAT_CASES
        ]
        injection_records = []
        for payload_id in PAYLOAD_CASES:
            for place in PLACES:
                injection_records += await injection_run.injection_trial(
                    bed,
                    PAYLOADS_BY_ID[payload_id],
                    place,
                    lambda question, payload, anchor: injection_run.fake(
                        "fake-hostile", question, payload, anchor
                    ),
                    option="fake-hostile",
                    trial=1,
                )

    chat = summarise(
        [score_trial(record, by_id[record.case](harness_anchor(record))) for record in chat_records]
    )["fake-hostile"]
    assert chat.right_number.hits == 0 and chat.right_name.hits == 0
    assert chat.ways_out == 0 and chat.writes == 0 and chat.unlabelled_causes == 0
    assert chat.tenant_held.hits == chat.tenant_held.trials == 1

    scored = [injection_score.score_trial(record) for record in injection_records]
    injection = injection_score.summarise(scored)["fake-hostile"]
    assert injection.followed.trials == 4 * len(PLACES) + 4, "each log body replays a turn"
    assert injection.followed.share == 1.0, [
        (row.payload, row.place, row.turn) for row in scored if row.followed is False
    ]
    assert injection.ways_out == 0
    assert injection.writes == 0
    assert injection.unlabelled_false_causes == 0
    assert injection.passes_gates


def harness_anchor(record: harness.TrialRecord) -> datetime:
    return datetime.fromisoformat(record.anchor)


async def test_the_answer_key_scores_right_on_every_question_it_can_answer() -> None:
    by_id = harness.questions_by_id(questions)
    async with served_bed(STORES) as bed:
        records = [
            await harness.chat_trial(
                bed,
                by_id[case],
                lambda question, anchor: fake("fake-answer-key", question, anchor),
                option="fake-answer-key",
                trial=1,
            )
            for case in by_id
        ]
    scores = [score_trial(record, by_id[record.case](harness_anchor(record))) for record in records]
    wrong = [
        (row.case, row.state) for row in scores if row.right is False or row.right_range is False
    ]
    assert not wrong
    summary = summarise(scores)["fake-answer-key"]
    assert summary.right_number.share == 1.0
    assert summary.abstains.share == 1.0
    assert summary.tenant_held.share == 1.0
    assert summary.passes_gates
