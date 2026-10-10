"""What the LLM-observability endpoint puts on the wire."""

from __future__ import annotations

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp


class LLMCallSummary(BaseModel):
    """One service+span-name+model group, aggregated over the window.

    `input_tokens`/`output_tokens`/`cost` are `None` — never `0` — when nothing in
    the group reported them. A span that never sent one still counts toward
    `call_count`; it is absent from the sum, not a zero contributor to it. See
    `service.py`'s `_to_summary` for how the two are told apart.
    """

    service_name: str
    span_name: str
    # "unlabeled model" when the group's spans carry no
    # `queries.GEN_AI_MODEL_ATTRIBUTE` — grouped explicitly, never dropped.
    model_name: str
    call_count: int
    p50_ms: float
    p95_ms: float
    error_count: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    # No standard OTel attribute carries this yet — see `queries.py`'s
    # `GEN_AI_COST_ATTRIBUTE`. Always `None` until one is confirmed in the wild.
    cost: float | None = None


class LLMCallListResponse(BaseModel):
    """Every model-call group this project reported in the window."""

    window_start: IsoTimestamp
    window_end: IsoTimestamp
    calls: list[LLMCallSummary] = Field(default_factory=list)
    # True when the project reported more groups than one read returns; the
    # busiest are the ones shown.
    truncated: bool = False
