import { useState } from "react";

/**
 * Project and time range, the two selections every screen is read against.
 *
 * Local state on purpose: the real project list comes from the control plane (P1-59)
 * and the range belongs in the URL so a link carries what the sender was looking at.
 * Both are P1-40's problem; this holds the shape so the top bar is not a mock.
 */

export interface Project {
  id: string;
  name: string;
}

export interface TimeRange {
  id: string;
  label: string;
  minutes: number;
}

/** Placeholder until the control plane serves the real list (P1-59). */
export const PROJECTS: Project[] = [{ id: "production", name: "production" }];

export const TIME_RANGES: TimeRange[] = [
  { id: "15m", label: "15 minutes", minutes: 15 },
  { id: "1h", label: "hour", minutes: 60 },
  { id: "6h", label: "6 hours", minutes: 360 },
  { id: "24h", label: "24 hours", minutes: 1440 },
  { id: "7d", label: "7 days", minutes: 10080 },
];

const DEFAULT_RANGE_ID = "1h";

export function useShellControls() {
  const [projectId, setProjectId] = useState(PROJECTS[0].id);
  const [rangeId, setRangeId] = useState(DEFAULT_RANGE_ID);

  return { projectId, setProjectId, rangeId, setRangeId };
}
