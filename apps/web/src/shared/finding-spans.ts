import type { ChartSpan } from "@/shared/components/TrendChart";

/**
 * A finding as the stretch of a chart's window it was open in (F6, moved to `shared/` by
 * F20 T5.5, since a feature may not import another). Each span keeps its finding's id and,
 * where the caller has it, the finding's own words, so a readout can name what is at the
 * cursor (FR32) and the "In this window" strip can mark it (FR47).
 */

const MS_PER_SECOND = 1000;

/** What a span needs of a finding: when it was open, and what it is called if known. */
export interface SpannedFinding {
  id: string;
  started_at: string;
  closed_at: string | null;
  title?: string;
}

/**
 * Each finding as the stretch of the window it was open in: an open one runs to the
 * window's end, and one that began before the window starts at its edge, since the
 * chart draws nothing outside it. One wholly outside draws nothing.
 */
export function findingSpans(
  findings: readonly SpannedFinding[],
  window: { from: string; to: string },
): ChartSpan[] {
  const from = Date.parse(window.from);
  const to = Date.parse(window.to);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return [];
  return findings.flatMap((finding) => {
    const start = Math.max(Date.parse(finding.started_at), from);
    const end = Math.min(finding.closed_at ? Date.parse(finding.closed_at) : to, to);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return [];
    return [
      {
        from: start / MS_PER_SECOND,
        to: end / MS_PER_SECOND,
        id: finding.id,
        ...(finding.title === undefined ? {} : { name: finding.title }),
      },
    ];
  });
}
