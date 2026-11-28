import { badgeOf, type SloStanding, type Tone, toneOf } from "./words";

/**
 * A reliability target's state as a badge, on its own page, on the list and on a
 * dashboard's panel, so all three say and colour it alike. The design's tones, from the
 * severity tokens: a critical filled with its badge fill, a warning outlined, the rest
 * words only (rule 6). The words always say the state.
 */
const TONE_CLASS: Record<Tone, string> = {
  critical: "border border-critical-border bg-critical-subtle text-critical-text",
  warning: "border border-warning-border text-warning-text",
  neutral: "border border-line text-ink-dim",
  none: "",
};

export function SloBadge({ view }: { view: SloStanding }) {
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
