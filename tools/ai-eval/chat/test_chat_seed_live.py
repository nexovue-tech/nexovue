"""Every answer in the answer file, read back through the real API with a question's ticket
(F4 plan T4.1).

One world is seeded at a fresh anchor; a question is stored through `service.ask`, so its
ticket is a real one; then each question's named tool is read over real HTTP with the
worker's own client (`CurlApiClient`), its rows projected as the loop projects them, and the
question's `pick` on those rows must equal the answer computed from the seed. A mismatch is
a seed, an answer or an API that disagree: the bed would score models against the wrong key.

Also held: another organization's service reads as nothing through this ticket, and the
empty project holds no telemetry.

This suite fails rather than skips when the stores are absent; see tests/helpers/live.
"""

from __future__ import annotations

from chat.live_support import load_api_test_environment, require_stores, served_bed

load_api_test_environment()

from nexovue_platform.assistant_records import TICKET_PURPOSE  # noqa: E402
from nexovue_platform.assistant_tools import TOOLS_BY_NAME  # noqa: E402
from nexovue_platform.checks.sealing import SealedBox, keyring_from  # noqa: E402
from sqlalchemy import text  # noqa: E402

from chat import harness, stores  # noqa: E402
from chat.answers import picked, questions  # noqa: E402
from nexovue_scheduler.insight.api_client import CurlApiClient  # noqa: E402
from nexovue_scheduler.insight.assistant_reads import check_arguments, project  # noqa: E402

STORES = require_stores("the chat bed's seed suite")


async def _ticket(bed: harness.Bed, world: stores.World, question_id: str) -> str:
    question = next(each for each in questions(world.anchor) if each.id == question_id)
    message_id, _ = await harness.store_question(
        bed, world, question, text=question.text, conversation_id=None
    )
    async with bed.install.connect() as db:
        sealed = await db.scalar(
            text("SELECT sealed_ticket FROM assistant_messages WHERE id = :id"),
            {"id": message_id},
        )
    box = SealedBox(keyring_from(bed.keys.current, bed.keys.previous), purpose=TICKET_PURPOSE)
    return box.open(sealed, owner=str(message_id))


async def test_every_answer_matches_what_its_tool_reads_through_the_api() -> None:
    async with served_bed(STORES) as bed:
        world = await harness.seed(bed)
        ticket = await _ticket(bed, world, "requests-hour-checkout")
        # A person has one question running at a time: the empty project's in a world of its own.
        empty_world = await harness.seed(bed)
        empty_ticket = await _ticket(bed, empty_world, "empty-project")
        client = CurlApiClient(bed.api_url)
        checked = 0
        mismatches = []
        for question in questions(world.anchor):
            if question.tool is None or question.pick is None:
                continue
            tool = TOOLS_BY_NAME[question.tool]
            call = check_arguments(
                tool, dict(question.arguments), level="facts_and_text", now=world.anchor
            )
            query = dict(call.query)
            if tool.project_in_query:
                query["project_id"] = str(world.main.project_id)
            answer = client.get(tool, query, ticket=ticket, path=call.path)
            assert answer.status == 200, (question.id, answer.status, answer.body[:300])
            rows = project(tool, answer.body, mask_emails=False).rows
            seen = picked(question.pick, rows)
            if seen not in question.expected:
                mismatches.append((question.id, question.expected, seen))
            checked += 1
        assert not mismatches, mismatches
        assert checked >= 40

        # The empty project's ticket reads no service at all.
        services = TOOLS_BY_NAME["list_services"]
        call = check_arguments(
            services,
            {
                "from": (world.anchor.replace(hour=0, minute=0)).isoformat(),
                "to": world.anchor.isoformat(),
            },
            level="facts_and_text",
            now=world.anchor,
        )
        empty = client.get(
            services,
            {**call.query, "project_id": str(empty_world.empty_project_id)},
            ticket=empty_ticket,
            path=call.path,
        )
        assert empty.status == 200
        assert project(services, empty.body, mask_emails=False).rows == []
