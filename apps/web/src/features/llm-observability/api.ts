import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import { filterCacheKey } from "@/shared/filters";
import type { TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";

/**
 * Every model call this project reported, grouped by service, span name and model.
 *
 * Wire field names unconverted, per `docs/engineering/frontend.md` — `llm_observability/schemas.py`
 * is the source of truth for this shape.
 */
export interface LLMCallSummary {
  service_name: string;
  span_name: string;
  /** "unlabeled model" when the group's spans carried no model-name attribute. */
  model_name: string;
  call_count: number;
  p50_ms: number;
  p95_ms: number;
  error_count: number;
  /** `null`, never `0`, when nothing in the group reported a token count. */
  input_tokens: number | null;
  output_tokens: number | null;
  /** Always `null` today — see `llm_observability/queries.py`'s `GEN_AI_COST_ATTRIBUTE`. */
  cost: number | null;
}

export interface LLMCallListResponse {
  window_start: string;
  window_end: string;
  calls: LLMCallSummary[];
}

export const llmCallsQueryKey = (projectId: string, timeWindow: TimeWindow, narrowing: string) =>
  ["llm-observability", "calls", projectId, windowKeyOf(timeWindow), narrowing] as const;

/**
 * The call list, narrowed by whatever the URL asks for.
 *
 * `narrowing` reaches the request rather than the rows that come back — both
 * `service` and `model` are substring matches the statement itself applies. See
 * `filters.ts`.
 */
export function useLLMCalls(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  narrowing: Record<string, string>,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: llmCallsQueryKey(projectId ?? "", timeWindow, filterCacheKey(narrowing)),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<LLMCallListResponse>("llm/calls", {
        searchParams: {
          project_id: projectId,
          from: timeWindow.from,
          to: timeWindow.to,
          ...narrowing,
        },
        signal,
      }),
  });
}
