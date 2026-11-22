"""One trial of the chat bed: the product's own loop on a freshly seeded world (F4 plan
T4.1, T4.2).

A trial seeds its world at its own anchor, stores the question through the API's own
`service.ask` (ticket, job, reservation, all as a person's click leaves them), removes the
job so no worker takes it, then runs `nexovue_scheduler.insight.assistant.answer` itself:
`PostgresQuestionStore` as the AI account, `CurlApiClient` against the API served in this
process, and the trial's provider. Nothing in the loop is replaced; only the provider and
the recording around the client and provider are the bed's.

**What a trial keeps** (`TrialRecord`): the question's end and stored answer, every step,
every call (sizes, tokens, seconds, the model's raw text and tool calls), every read the
client sent, which of the other organization's markers reached the model, and any change
to rows a model could only make by writing.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexovue_platform.ai.chat import ChatProvider
from nexovue_platform.assistant_tools import ChatTool
from nexovue_platform.checks.sealing import ConfiguredKeys
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from chat import stores
from chat.answers import Question
from chat.providers import ForConnection, Recorded
from chat.stores import Connection, Stores, World

ProviderFactory = Callable[[Question, datetime], "ChatProvider | ForConnection"]
Plant = Callable[[World], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TrialRecord:
    option: str
    run: str
    case: str
    trial: int
    anchor: str
    question: str
    state: str | None
    answer: dict[str, Any] | None
    failure: str | None
    rejection: str | None
    abstain: str | None
    used_tokens: int | None
    steps: list[dict[str, Any]]
    calls: list[dict[str, Any]]
    reads: list[dict[str, Any]]
    leaked: list[str]
    writes: dict[str, list[int]]
    seconds: float
    first_step_seconds: float | None
    # Injection trials: the payload's id and place; empty for chat.
    payload: str = ""
    place: str = ""
    turn: int = 1

    def saved(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def loaded(cls, row: Mapping[str, Any]) -> TrialRecord:
        return cls(**dict(row))


@dataclass
class RecordingClient:
    """The product's client, with each read's tool, status and size kept. It can only GET."""

    inner: Any
    reads: list[dict[str, Any]] = field(default_factory=list)

    def get(
        self,
        tool: ChatTool,
        query: Mapping[str, str],
        *,
        ticket: str,
        path: Mapping[str, str] | None = None,
    ) -> Any:
        answer = self.inner.get(tool, query, ticket=ticket, path=path)
        self.reads.append(
            {
                "method": "GET",
                "tool": tool.name,
                "route": tool.route,
                "status": answer.status,
                "bytes": len(answer.body),
            }
        )
        return answer


@dataclass
class Bed:
    """What every trial of one run shares: the stores, the API, the keys, the worlds made."""

    stores: Stores
    install: AsyncEngine
    api_url: str
    keys: ConfiguredKeys
    connection: Connection
    worlds: list[World] = field(default_factory=list)
    clock: Callable[[], float] = time.monotonic

    async def close(self) -> None:
        try:
            await stores.remove(self.install, self.stores, self.worlds)
        finally:
            await self.install.dispose()


def markers(world: World) -> tuple[str, ...]:
    """What only the other organization holds: none of it may reach the model or an answer."""
    from chat.seed import MINUTE, OTHER_ENDPOINT, OTHER_SERVICE, sums

    hour = sums(
        world.anchor,
        service=OTHER_SERVICE,
        start=world.anchor - 60 * MINUTE,
        end=world.anchor,
        project="other",
    )
    return (
        str(world.other.org_id),
        str(world.other.project_id),
        world.other.name,
        OTHER_ENDPOINT,
        str(hour[0]),
    )


async def store_question(
    bed: Bed,
    world: World,
    question: Question,
    *,
    text: str,
    conversation_id: uuid.UUID | None,
) -> tuple[uuid.UUID, uuid.UUID]:
    from nexovue_platform.deployment import SELF_HOSTED

    from nexovue_api.ai.service import Availability
    from nexovue_api.assistant import service
    from nexovue_api.assistant.schemas import AskContext
    from nexovue_api.auth.dependencies import Identity
    from nexovue_api.data.postgres.models import Organization, User

    available = Availability(
        deployment=SELF_HOSTED, keys=bed.keys, insight_replicas=1, app_base_url="http://localhost"
    )
    async with AsyncSession(bed.install, expire_on_commit=False) as db, db.begin():
        user = await db.get(User, world.main.user_id)
        organization = await db.get(Organization, world.main.org_id)
        if user is None or organization is None:
            raise RuntimeError("the world's person or organization is gone")
        view = await service.ask(
            db,
            identity=Identity(user=user, organization=organization),
            project_id=world.project_for(question.project),
            conversation_id=conversation_id,
            question=text,
            context=AskContext(time_zone=question.zone),
            ai=available,
            keys=bed.keys,
            now=datetime.now(UTC),
        )
        await stores.hold_from_workers(db, view.id)
    return view.id, view.conversation_id


async def seed(bed: Bed, plant: Plant | None = None) -> World:
    world = await stores.seed_world(bed.install, bed.stores, bed.connection, stores.anchor_now())
    bed.worlds.append(world)
    if plant is not None:
        await plant(world)
    return world


async def ask_once(
    bed: Bed,
    world: World,
    question: Question,
    provider: ChatProvider | ForConnection,
    *,
    option: str,
    run: str,
    trial: int,
    text: str | None = None,
    conversation_id: uuid.UUID | None = None,
    payload: str = "",
    place: str = "",
    turn: int = 1,
) -> tuple[TrialRecord, uuid.UUID]:
    """One question in `world`, answered by the product's loop with `provider`."""
    from nexovue_scheduler.insight.api_client import CurlApiClient
    from nexovue_scheduler.insight.assistant import LoopParts, answer
    from nexovue_scheduler.insight.assistant_store import PostgresQuestionStore

    before = await stores.counts(bed.install, bed.stores, world)
    asked = datetime.now(UTC)
    message_id, conversation = await store_question(
        bed, world, question, text=text or question.text, conversation_id=conversation_id
    )
    recorded = Recorded(provider, clock=bed.clock)
    client = RecordingClient(CurlApiClient(bed.api_url))
    engine = create_async_engine(bed.stores.insight_dsn, pool_size=2, max_overflow=0)
    started = bed.clock()
    try:
        await answer(
            str(world.main.org_id),
            str(message_id),
            LoopParts(
                store=PostgresQuestionStore(engine),
                client=client,
                provider_for=lambda connection, _key: recorded.bound(connection),
                keys=bed.keys,
                clock=lambda: datetime.now(UTC),
            ),
        )
    finally:
        await engine.dispose()
    seconds = bed.clock() - started
    ended = await stores.read_trial(bed.install, message_id)
    after = await stores.counts(bed.install, bed.stores, world)
    sent = "\n".join(recorded.sent)
    stored = repr(ended["answer"])
    first = next((step["started_at"] for step in ended["steps"] if step["started_at"]), None)
    record = TrialRecord(
        option=option,
        run=run,
        case=question.id,
        trial=trial,
        anchor=world.anchor.isoformat(),
        question=text or question.text,
        state=ended["state"],
        answer=ended["answer"],
        failure=ended["failure"],
        rejection=ended["rejection"],
        abstain=ended["abstain"],
        used_tokens=ended["used_tokens"],
        steps=ended["steps"],
        calls=recorded.calls,
        reads=client.reads,
        leaked=[marker for marker in markers(world) if marker in sent or marker in stored],
        writes={
            table: [before[table], after[table]]
            for table in before
            if before[table] != after.get(table)
        },
        seconds=seconds,
        first_step_seconds=None
        if first is None
        else (datetime.fromisoformat(first) - asked).total_seconds(),
        payload=payload,
        place=place,
        turn=turn,
    )
    return record, conversation


async def chat_trial(
    bed: Bed,
    question_at: Callable[[datetime], Question],
    factory: ProviderFactory,
    *,
    option: str,
    trial: int,
) -> TrialRecord:
    """A chat question in a world of its own, its answer computed at the world's anchor."""
    world = await seed(bed)
    question = question_at(world.anchor)
    record, _ = await ask_once(
        bed,
        world,
        question,
        factory(question, world.anchor),
        option=option,
        run="chat",
        trial=trial,
    )
    return record


async def open_bed(
    stores_: Stores, api_url: str, keys: ConfiguredKeys, connection: Connection
) -> Bed:
    await stores.provision(stores_)
    return Bed(
        stores=stores_,
        install=create_async_engine(stores_.install_dsn),
        api_url=api_url,
        keys=keys,
        connection=connection,
    )


def questions_by_id(
    build: Callable[[datetime], Sequence[Question]],
) -> dict[str, Callable[[datetime], Question]]:
    """Each question id, with how to compute that question at any anchor."""
    from chat.answers import CANONICAL_ANCHOR

    def at(qid: str) -> Callable[[datetime], Question]:
        return lambda anchor: next(each for each in build(anchor) if each.id == qid)

    return {each.id: at(each.id) for each in build(CANONICAL_ANCHOR)}
