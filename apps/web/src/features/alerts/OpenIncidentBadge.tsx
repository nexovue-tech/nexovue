import { useSelection } from "@/shared/selection";

import { openIncidentBadge } from "./alert-words";
import { useOpenIncidentCount } from "./api";

const SEVERITY_BADGE = {
  critical: "bg-critical-subtle text-critical-text",
  warning: "bg-warning-subtle text-warning-text",
} as const;

/**
 * How many incidents are open, beside Alerts on every screen — so someone already in the
 * console sees one open without going to look. Nothing while the count is unknown: a
 * guess of zero would say "all clear" when it is not known.
 */
export function OpenIncidentBadge() {
  const { projectId } = useSelection();
  const count = useOpenIncidentCount(projectId);
  const badge = count.data ? openIncidentBadge(count.data) : null;
  if (!badge) return null;

  return (
    <>
      <span
        aria-hidden="true"
        className={`rounded-full px-1.5 font-medium font-mono text-2xs leading-4 ${SEVERITY_BADGE[badge.severity]}`}
      >
        {badge.text}
      </span>
      <span className="sr-only">({badge.label})</span>
    </>
  );
}
