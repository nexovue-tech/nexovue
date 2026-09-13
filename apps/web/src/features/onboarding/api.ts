import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/**
 * "Has anything arrived yet?" asked of the ordinary trace list. There is no separate
 * endpoint, and there should not be: the first span is only the list becoming non-empty.
 *
 * The response shape is declared here rather than imported from the traces feature,
 * because features do not import each other — and this needs one field of it.
 */
export interface FirstTrace {
  trace_id: string;
  root_service: string;
  root_name: string;
}

interface FirstTraceResponse {
  traces: FirstTrace[];
}

/**
 * Polling with restraint: a customer pasting two variables into a deploy config may take
 * minutes, and a two-second poll for all of it is a request storm for one row. Each
 * attempt waits half again as long as the last, up to half a minute.
 */
const FIRST_POLL_MS = 2_000;
const MAX_POLL_MS = 30_000;
const BACKOFF_FACTOR = 1.5;

function nextPollDelayMs(attempts: number): number {
  return Math.min(MAX_POLL_MS, Math.round(FIRST_POLL_MS * BACKOFF_FACTOR ** attempts));
}

export function useFirstTrace(projectId: string | undefined) {
  return useQuery({
    queryKey: ["onboarding", "first-trace", projectId ?? ""],
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<FirstTraceResponse>("traces", {
        searchParams: { project_id: projectId, limit: 1 },
        signal,
      }),
    select: (response) => response.traces[0],
    staleTime: 0,
    refetchInterval: (query) =>
      query.state.data?.traces.length ? false : nextPollDelayMs(query.state.dataUpdateCount),
    // Default, but stated: a backgrounded tab is nobody watching, and it must stop asking.
    refetchIntervalInBackground: false,
  });
}
