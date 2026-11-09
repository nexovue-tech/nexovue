import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api-client";
import type { ReleaseMark } from "../deploy-markers";
import type { LiveWindow, TimeWindow } from "../selection";
import { liveOptions, windowKeyOf } from "../selection";

/**
 * The release marks for a window (F28 FR35): in shared/ because Overview, a service's
 * page and a finding's page each draw them, and none may import another feature.
 * Postgres alone answers it, so the marks stay through a telemetry outage.
 */

export interface ReleaseMarkList {
  markers: ReleaseMark[];
  /** More marks than `cap` fell in the window; these are the newest. */
  truncated: boolean;
  cap: number;
}

export function useReleaseMarkers(
  projectId: string | undefined,
  timeWindow: TimeWindow | LiveWindow,
  serviceName?: string,
) {
  return useQuery({
    queryKey: ["release-markers", projectId ?? "", serviceName ?? "", windowKeyOf(timeWindow)],
    enabled: projectId !== undefined,
    ...liveOptions(timeWindow),
    queryFn: ({ signal }) =>
      apiClient.get<ReleaseMarkList>("releases/markers", {
        searchParams: {
          project_id: projectId,
          from: timeWindow.from,
          to: timeWindow.to,
          service: serviceName,
        },
        signal,
      }),
  });
}
