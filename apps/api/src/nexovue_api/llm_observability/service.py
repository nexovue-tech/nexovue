"""Turning gen_ai-tagged spans into one list of model calls."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.llm_observability import queries
from nexovue_api.llm_observability.schemas import LLMCallListResponse, LLMCallSummary
from nexovue_api.window import receive_window


async def list_llm_calls(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    service_match: str | None = None,
    model_match: str | None = None,
) -> LLMCallListResponse:
    """Every model-call group this project reported in the window.

    The two filters reach the statement rather than the answer — see
    `queries.llm_calls_sql`. Every binding here is added only when its clause
    was spliced in, so the two cannot come apart (the same rule
    `metrics.list_metrics` follows).
    """
    received_from, received_to = receive_window(from_time, to_time)
    parameters: dict[str, Any] = {
        "project_id": project_id,
        "from_time": from_time,
        "to_time": to_time,
        "received_from": received_from,
        "received_to": received_to,
        "gen_ai_prefix": queries.GEN_AI_ATTRIBUTE_PREFIX,
        "model_attr": queries.GEN_AI_MODEL_ATTRIBUTE,
        "input_tokens_attr": queries.GEN_AI_INPUT_TOKENS_ATTRIBUTE,
        "output_tokens_attr": queries.GEN_AI_OUTPUT_TOKENS_ATTRIBUTE,
    }
    if service_match:
        parameters["service_match"] = service_match
    if model_match:
        parameters["model_match"] = model_match

    rows = await repository.query(
        queries.llm_calls_sql(service_match=bool(service_match), model_match=bool(model_match)),
        parameters,
    )

    return LLMCallListResponse(
        window_start=from_time,
        window_end=to_time,
        calls=[_to_summary(row) for row in rows],
    )


def _to_summary(row: dict[str, Any]) -> LLMCallSummary:
    return LLMCallSummary(
        service_name=values.text(row["service_name"]),
        span_name=values.text(row["span_name"]),
        model_name=values.text(row["model_name"]),
        call_count=int(row["call_count"]),
        p50_ms=float(row["p50_ms"]),
        p95_ms=float(row["p95_ms"]),
        error_count=int(row["error_count"]),
        input_tokens=_reported_sum(row, reported="input_tokens_reported", total="input_tokens_sum"),
        output_tokens=_reported_sum(
            row, reported="output_tokens_reported", total="output_tokens_sum"
        ),
        # No source exists yet — see `queries.GEN_AI_COST_ATTRIBUTE`.
        cost=None,
    )


def has_gen_ai_attribute(attributes: dict[str, Any]) -> bool:
    """Whether a span's already-parsed attributes carry any `gen_ai.*` key.

    **Not a flat-key prefix check.** `values.json_object` parses ClickHouse's
    own `toJSONString(span_attrs)`, and the JSON type nests a dotted OTLP key
    into a tree — `gen_ai.provider.name` arrives as
    `{"gen_ai": {"provider": {"name": ...}}}`, never as a literal
    `"gen_ai.provider.name"` key. The namespace is therefore always exactly the
    top-level key `"gen_ai"`, found live against a real span (2026-10-09) after
    the first version of this function checked `key.startswith("gen_ai.")` on
    the top level and silently matched nothing.

    This is the one place this rule is expressed in Python; `traces/service.py`
    imports it rather than repeating the check. The SQL side
    (`queries.LLM_CALLS_SQL`'s `JSONAllPaths`) is necessarily separate — it
    reads full dotted paths, which is a different representation of the same
    rule, not a second rule.
    """
    prefix = queries.GEN_AI_ATTRIBUTE_PREFIX.rstrip(".")
    return prefix in attributes


def _reported_sum(row: dict[str, Any], *, reported: str, total: str) -> int | None:
    """A token sum, or `None` when nothing in the group reported it.

    Decided from the presence count, not from the sum being falsy — a sum of
    `0` across spans that each reported zero tokens is a real reading and must
    not be confused with no span having reported one at all.
    """
    if int(row[reported]) == 0:
        return None
    return int(row[total] or 0)
