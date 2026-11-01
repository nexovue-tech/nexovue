import type { ServiceDetection } from "./api";
import type { BadgeTone } from "./badge";
import { describeBadge } from "./badge";

const TONE: Record<BadgeTone, string> = {
  quiet: "border-line text-ink-dim",
  attention: "border-warning-border text-warning-text",
  critical: "border-critical-solid text-critical-text",
};

/**
 * A service's detection state in a word (FR25), with its sentence beside it. The word
 * carries the state, not the colour alone (design principle 6).
 */
export function DetectionBadge({
  detection,
  failed,
  zone,
}: {
  detection: ServiceDetection | undefined;
  failed: boolean;
  zone?: string;
}) {
  const badge = describeBadge(detection, { failed, zone });
  if (badge === null) return null;
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2">
      <span
        className={`rounded border px-1.5 py-0.5 font-mono text-2xs uppercase tracking-caps ${TONE[badge.tone]}`}
      >
        {badge.label}
      </span>
      {badge.detail ? <span className="text-ink-dim text-sm">{badge.detail}</span> : null}
    </span>
  );
}
