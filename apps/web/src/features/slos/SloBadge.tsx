import type { SloView } from "./api";
import { badgeOf, type Tone, toneOf } from "./slo-words";

/**
 * The design's tones, from the severity tokens: a critical filled with its badge fill, a
 * warning outlined, the rest words only (rule 6). The words always say the state.
 */
const TONE_CLASS: Record<Tone, string> = {
  critical: "border border-critical-border bg-critical-subtle text-critical-text",
  warning: "border border-warning-border text-warning-text",
  neutral: "border border-line text-ink-dim",
  none: "",
};

export function SloBadge({ view }: { view: Pick<SloView, "state" | "severity" | "attention"> }) {
  const badge = badgeOf(view);
  if (badge === null) return null;
  return (
    <span
      className={`inline-block rounded px-1.5 py-0.5 font-medium text-xs ${TONE_CLASS[toneOf(view)]}`}
    >
      {badge}
    </span>
  );
}
