/** `GET /findings/open-count`: what waits for someone, for the sidebar (sketch G). */
export interface OpenFindingCount {
  open: number;
  critical: number;
}

/** Past this the badge says "9+": a sidebar is not where a count is read exactly. */
const BADGE_MOST = 9;

/**
 * The sidebar's badge beside Findings, as Alerts has (approved sketch G, 2026-11-28).
 * Nothing at zero and nothing while unknown: a guessed zero would read as "all clear".
 */
export function openFindingsBadge(
  count: OpenFindingCount,
): { text: string; label: string; severity: "critical" | "warning" } | null {
  if (count.open === 0) return null;
  return {
    text: count.open > BADGE_MOST ? `${BADGE_MOST}+` : `${count.open} open`,
    label: count.open === 1 ? "1 open finding" : `${count.open} open findings`,
    severity: count.critical > 0 ? "critical" : "warning",
  };
}
