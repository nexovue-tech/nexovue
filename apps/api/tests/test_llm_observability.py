"""The LLM-calls read: one table, generic identification, no enumerated models.

The fake answers from the values the repository *bound*, never from the
statement text, so a read that lost its tenant predicate finds nothing and the
isolation test below fails rather than quietly passing.

`test_llm_observability_live.py` runs the same statement against the real
ClickHouse, reading the fixtures `tools/gateseed` already seeds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_api.data.clickhouse.base import TENANT_BINDING, TenantScopedRepository
from nexovue_api.llm_observability import queries, service
from nexovue_api.llm_observability.schemas import LLMCallListResponse

TENANT_A: Final = "org-a"
TENANT_B: Final = "org-b"
PROJECT_A: Final = "project-a"
PROJECT_B: Final = "project-b"

_COLUMNS: Final = [
    "service_name",
    "span_name",
    "model_name",
    "call_count",
    "p50_ms",
    "p95_ms",
    "error_count",
    "input_tokens_reported",
    "input_tokens_sum",
    "output_tokens_reported",
    "output_tokens_sum",
]


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


# Each tenant owns exactly one project. A read that arrives with the wrong pair
# gets nothing back, which is what makes a dropped predicate visible as an empty
# answer rather than as somebody else's calls.
_OWNED_PROJECT: Final = {TENANT_A: PROJECT_A, TENANT_B: PROJECT_B}


class FakeLLMCallStore:
    """Serves a call list, honouring the tenant and project the query bound."""

    def __init__(self, rows_by_tenant: dict[str, list[tuple[Any, ...]]]) -> None:
        self._rows = rows_by_tenant
        self.last_parameters: dict[str, Any] = {}
        self.last_statement: str = ""

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.last_parameters = bound
        self.last_statement = sql

        tenant = bound["tenant_id"]
        if _OWNED_PROJECT.get(tenant) != bound.get("project_id"):
            return FakeQueryResult([], [])
        return FakeQueryResult(_COLUMNS, self._rows.get(tenant, []))


def _repository(tenant: str, store: FakeLLMCallStore) -> TenantScopedRepository:
    return TenantScopedRepository(store, tenant_id=tenant)  # type: ignore[arg-type]


def _window() -> tuple[datetime, datetime]:
    end = datetime(2026, 10, 9, 17, 0, tzinfo=UTC)
    return end - timedelta(hours=1), end


def _row(
    service_name: str,
    span_name: str,
    model_name: str,
    *,
    call_count: int = 1,
    p50_ms: float = 10.0,
    p95_ms: float = 20.0,
    error_count: int = 0,
    input_tokens_reported: int = 1,
    input_tokens_sum: float = 100.0,
    output_tokens_reported: int = 1,
    output_tokens_sum: float = 50.0,
) -> tuple[Any, ...]:
    return (
        service_name,
        span_name,
        model_name,
        call_count,
        p50_ms,
        p95_ms,
        error_count,
        input_tokens_reported,
        input_tokens_sum,
        output_tokens_reported,
        output_tokens_sum,
    )


def _store(rows: list[tuple[Any, ...]] | None = None) -> FakeLLMCallStore:
    return FakeLLMCallStore(
        {
            TENANT_A: (
                rows
                if rows is not None
                else [_row("payment", "chat claude-fable-5-1", "claude-fable-5-1")]
            ),
            TENANT_B: [_row("other-service", "chat gpt-4", "gpt-4")],
        }
    )


async def _calls(
    store: FakeLLMCallStore,
    tenant: str = TENANT_A,
    project: str = PROJECT_A,
    **narrowing: Any,
) -> LLMCallListResponse:
    start, end = _window()
    return await service.list_llm_calls(
        _repository(tenant, store),
        project_id=project,
        from_time=start,
        to_time=end,
        **narrowing,
    )


# ------------------------------------------------------------------- statement


def test_the_statement_binds_the_tenant_exactly_once() -> None:
    """One table, one predicate — unlike the five-branch metrics catalogue."""
    assert queries.LLM_CALLS_SQL.count(TENANT_BINDING) == 1


def test_identification_is_generic_not_a_list_of_known_models() -> None:
    """The whole point of F29's scope: no provider or model name is hardcoded.

    If this ever starts failing because someone added a literal "claude" or
    "gpt" to the statement, that is the frozen metric contract the spec and
    design both explicitly rejected, creeping back in.
    """
    assert "startsWith(p, {gen_ai_prefix:String})" in queries.LLM_CALLS_SQL
    assert "JSONAllPaths(span_attrs)" in queries.LLM_CALLS_SQL
    for literal_name in ("claude", "gpt", "gemini", "llama", "anthropic", "openai"):
        assert literal_name not in queries.LLM_CALLS_SQL.lower()


def test_the_model_and_token_attributes_are_named_constants() -> None:
    """The entire cost of the next rename (`AC29.6`) is editing these three lines."""
    assert queries.GEN_AI_MODEL_ATTRIBUTE == "gen_ai.request.model"
    assert queries.GEN_AI_INPUT_TOKENS_ATTRIBUTE == "gen_ai.usage.input_tokens"
    assert queries.GEN_AI_OUTPUT_TOKENS_ATTRIBUTE == "gen_ai.usage.output_tokens"


def test_cost_has_no_source_yet() -> None:
    """Deliberately unset — see `queries.py`'s own comment. Not a missed field."""
    assert queries.GEN_AI_COST_ATTRIBUTE is None


# ---------------------------------------------------------- has_gen_ai_attribute


def test_has_gen_ai_attribute_reads_the_nested_shape_the_store_actually_produces() -> None:
    """`values.json_object` nests a dotted key by segment — found live, against
    a real span, after a flat-key version of this check matched nothing real."""
    assert service.has_gen_ai_attribute({"gen_ai": {"request": {"model": "claude-fable-5-1"}}})
    assert service.has_gen_ai_attribute({"gen_ai": {"provider": {"name": "anthropic"}}})


def test_has_gen_ai_attribute_is_false_for_ordinary_attributes() -> None:
    assert not service.has_gen_ai_attribute({"tier": "gold"})
    assert not service.has_gen_ai_attribute({})


# ------------------------------------------------------------------- isolation


async def test_another_tenant_sees_none_of_these_calls() -> None:
    store = _store()

    stolen = await _calls(store, tenant=TENANT_B, project=PROJECT_A)

    assert stolen.calls == []


async def test_the_read_binds_the_session_tenant_and_the_proved_project() -> None:
    store = _store()

    await _calls(store)

    assert store.last_parameters["tenant_id"] == TENANT_A
    assert store.last_parameters["project_id"] == PROJECT_A


# ----------------------------------------------------------------- unlabeled


async def test_a_group_with_no_model_attribute_is_unlabeled_not_dropped() -> None:
    """AC29.2/AC29.3 — present as gen_ai, absent a model name, never hidden."""
    store = _store([_row("payment", "chat", "unlabeled model", input_tokens_reported=0)])

    calls = await _calls(store)

    assert len(calls.calls) == 1
    assert calls.calls[0].model_name == "unlabeled model"


# ------------------------------------------------------------- missing tokens


async def test_a_group_where_nothing_reported_tokens_is_none_not_zero() -> None:
    """A missing reading is an absent value, never a false zero (design principle 2)."""
    store = _store(
        [
            _row(
                "payment",
                "chat",
                "unlabeled model",
                input_tokens_reported=0,
                input_tokens_sum=0.0,
                output_tokens_reported=0,
                output_tokens_sum=0.0,
            )
        ]
    )

    calls = await _calls(store)

    assert calls.calls[0].input_tokens is None
    assert calls.calls[0].output_tokens is None
    assert calls.calls[0].cost is None


async def test_a_group_with_reported_tokens_carries_the_real_sum() -> None:
    store = _store(
        [_row("payment", "chat claude-fable-5-1", "claude-fable-5-1", input_tokens_sum=842.0)]
    )

    calls = await _calls(store)

    assert calls.calls[0].input_tokens == 842


async def test_a_group_where_every_span_reported_zero_tokens_is_a_real_zero() -> None:
    """The one case the presence count exists to tell apart from the one above:
    every span said "0 tokens" on purpose, which is a reading, not an absence."""
    store = _store(
        [
            _row(
                "payment",
                "chat",
                "claude-fable-5-1",
                input_tokens_reported=3,
                input_tokens_sum=0.0,
            )
        ]
    )

    calls = await _calls(store)

    assert calls.calls[0].input_tokens == 0


# -------------------------------------------------------------------- content


async def test_a_project_that_reported_no_model_calls_is_an_empty_list() -> None:
    store = _store([])

    calls = await _calls(store)

    assert calls.calls == []


# ------------------------------------------------------------------- narrowing


def test_an_unfiltered_read_carries_neither_narrowing_clause() -> None:
    sql = queries.llm_calls_sql(service_match=False, model_match=False)

    assert queries.SERVICE_MATCH_CLAUSE not in sql
    assert queries.MODEL_MATCH_CLAUSE not in sql


async def test_an_unfiltered_read_binds_neither_narrowing_parameter() -> None:
    store = _store()

    await _calls(store)

    assert "service_match" not in store.last_parameters
    assert "model_match" not in store.last_parameters


async def test_a_service_filter_reaches_the_statement_rather_than_the_answer() -> None:
    store = _store()

    await _calls(store, service_match="payment")

    assert queries.SERVICE_MATCH_CLAUSE in store.last_statement
    assert store.last_parameters["service_match"] == "payment"


async def test_a_model_filter_narrows_after_grouping() -> None:
    store = _store()

    await _calls(store, model_match="claude")

    assert queries.MODEL_MATCH_CLAUSE in store.last_statement
    assert store.last_parameters["model_match"] == "claude"


# ------------------------------------------------------------------------- cap


async def test_more_groups_than_one_read_returns_are_cut_and_say_so() -> None:
    """A span name carrying an id makes every call its own group; uncapped, the
    read grew until ClickHouse refused it as an error instead of a short list."""
    store = _store([_row("payment", f"chat {n}", "claude-fable-5-1") for n in range(501)])

    calls = await _calls(store)

    assert store.last_parameters["limit"] == service.MAX_LLM_CALLS + 1
    assert len(calls.calls) == service.MAX_LLM_CALLS
    assert calls.truncated


async def test_a_list_within_the_cap_is_not_marked_truncated() -> None:
    calls = await _calls(_store())

    assert not calls.truncated
