"""The ClickHouse read behind the LLM-calls screen.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

**Identification is generic, by design, not by a list of known attribute names.**
A span is a model call because it carries any key under `gen_ai.`, found with
`JSONAllPaths` (ClickHouse >= 24.8, confirmed against the pinned 26.3 this repo
runs) rather than enumerated — `market-analysis-2026-09.md` §13 and this
feature's own spec are both explicit that the convention is still Development
status and has already renamed one field once.

**The four constants below are the whole migration the next rename costs.**
`GEN_AI_MODEL_ATTRIBUTE` points at the field this screen reads for a model's
name today; moving it to match a renamed convention is one line here, not a
schema change. `GEN_AI_COST_ATTRIBUTE` is unset (see its own comment) rather
than pointed at a guess.
"""

from __future__ import annotations

from typing import Final

# Any key under this prefix makes a span a model call. Never a list of known
# provider/model attribute names — that is exactly the "frozen metric contract"
# `design.md`'s *Alternatives* rejected.
GEN_AI_ATTRIBUTE_PREFIX: Final = "gen_ai."

# The field this screen reads for a model's name, confirmed current against the
# semantic-conventions registry 2026-10-09. `gen_ai.system` was the field before
# the August 2025 rename to `gen_ai.provider.name`; this one is next in line to
# move, which is the entire reason it lives here as one name and not six places.
GEN_AI_MODEL_ATTRIBUTE: Final = "gen_ai.request.model"
GEN_AI_INPUT_TOKENS_ATTRIBUTE: Final = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS_ATTRIBUTE: Final = "gen_ai.usage.output_tokens"

# **Deliberately no value.** OTel's `gen_ai.*` convention standardises token
# counts; it has no standard cost attribute, confirmed while building this
# feature's own fixtures. Pointing this at a guessed vendor-specific name would
# be exactly the unverified-claim-in-the-same-confident-voice `AGENTS.md`'s
# research standard exists to prevent. Left unset: `service.py` never reads a
# cost field while this is `None`, so `LLMCallSummary.cost` stays `None` for
# every row until real evidence names one.
GEN_AI_COST_ATTRIBUTE: Final[str | None] = None

# The window every read shares, bounded on both clocks — the same shape
# `metrics/queries.py` uses, for the same reason: the customer's own `timestamp`
# cannot prune a partition, `received_at` is ours and is what the tables are
# partitioned by.
_WINDOW: Final = """
  AND timestamp >= {from_time:DateTime64(3)}
  AND timestamp < {to_time:DateTime64(3)}
  AND received_at >= {received_from:DateTime64(3)}
  AND received_at < {received_to:DateTime64(3)}"""

# The optional predicates, spliced on comment markers: ClickHouse's own
# parameter syntax is braces, so `str.format` cannot be used here. The same
# shape `metrics/queries.py` and `synthetics/queries.py` both use, for the same
# reason — a predicate not asked for must not appear, including as a bound
# parameter nothing supplies.
_SERVICE_MATCH_PLACEHOLDER: Final = "/* service match */"
_MODEL_MATCH_PLACEHOLDER: Final = "/* model match */"

SERVICE_MATCH_CLAUSE: Final = (
    "AND positionCaseInsensitive(service_name, {service_match:String}) > 0"
)
# Matched after grouping, not before: a customer narrowing to "claude" wants
# every group that model was busiest in, not only the spans that literally say
# so inside one group's own `getSubcolumn` read a second time.
MODEL_MATCH_CLAUSE: Final = "HAVING positionCaseInsensitive(model_name, {model_match:String}) > 0"

# One row per service+span-name+model-name group. `countIf(... != '')` beside
# each sum is the presence count `service.py` uses to decide `None` versus a
# real value — a group where nothing reported tokens must not read as a zero.
LLM_CALLS_SQL: Final = f"""
SELECT
    service_name,
    span_name,
    ifNull(
        nullIf(toString(getSubcolumn(span_attrs, {{model_attr:String}})), ''),
        'unlabeled model'
    ) AS model_name,
    count() AS call_count,
    quantile(0.5)(duration_ms) AS p50_ms,
    quantile(0.95)(duration_ms) AS p95_ms,
    countIf(status_code = 'ERROR') AS error_count,
    countIf(toString(getSubcolumn(span_attrs, {{input_tokens_attr:String}})) != '')
        AS input_tokens_reported,
    sum(toFloat64OrNull(toString(getSubcolumn(span_attrs, {{input_tokens_attr:String}}))))
        AS input_tokens_sum,
    countIf(toString(getSubcolumn(span_attrs, {{output_tokens_attr:String}})) != '')
        AS output_tokens_reported,
    sum(toFloat64OrNull(toString(getSubcolumn(span_attrs, {{output_tokens_attr:String}}))))
        AS output_tokens_sum
FROM spans
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}{_WINDOW}
  AND arrayExists(p -> startsWith(p, {{gen_ai_prefix:String}}), JSONAllPaths(span_attrs))
  {_SERVICE_MATCH_PLACEHOLDER}
GROUP BY service_name, span_name, model_name
{_MODEL_MATCH_PLACEHOLDER}
ORDER BY call_count DESC
LIMIT {{limit:UInt32}}
"""


def llm_calls_sql(*, service_match: bool, model_match: bool) -> str:
    """The statement, narrowed to what the caller asked for.

    Both reach the statement rather than the answer: `LIMIT` cuts the list at
    `service.MAX_LLM_CALLS`, so narrowing the rows that came back would answer
    "the matches among the busiest five hundred" rather than the question.
    """
    return LLM_CALLS_SQL.replace(
        _SERVICE_MATCH_PLACEHOLDER, SERVICE_MATCH_CLAUSE if service_match else ""
    ).replace(_MODEL_MATCH_PLACEHOLDER, MODEL_MATCH_CLAUSE if model_match else "")
