import { formatDuration } from "@/shared/format/duration";
import { formatTimestamp } from "@/shared/format/time";

import type { CheckRun } from "./api";
import { runsOldestFirst } from "./check-health";

/**
 * The individual probes, one mark each, oldest on the left.
 *
 * Height is the duration against the slowest probe in the strip, so a slow patch has a
 * shape before it has a number. Colour is the only thing that means failure — a healthy
 * strip is neutral, which is the rule that keeps red meaning something.
 */

const MINIMUM_MARK_PERCENT = 8;
const FULL_PERCENT = 100;

export function RunStrip({ runs }: { runs: readonly CheckRun[] }) {
  if (runs.length === 0) return null;

  const ordered = runsOldestFirst(runs);
  const slowest = Math.max(...ordered.map((run) => run.duration_ms), 1);

  return (
    <div className="flex h-10 items-end gap-px" aria-hidden="true">
      {ordered.map((run) => (
        <span
          key={run.timestamp}
          title={`${formatTimestamp(run.timestamp)} · ${run.status_code} · ${formatDuration(run.duration_ms)}`}
          style={{ height: `${markHeightPercent(run.duration_ms, slowest)}%` }}
          className={`min-w-px flex-1 ${run.up ? "bg-solid" : "bg-critical-solid"}`}
        />
      ))}
    </div>
  );
}

/** A fast probe is still a probe: it gets a visible mark rather than a zero-height one. */
function markHeightPercent(durationMs: number, slowestMs: number): number {
  const share = (Math.max(0, durationMs) / slowestMs) * FULL_PERCENT;
  return Math.max(MINIMUM_MARK_PERCENT, Math.min(FULL_PERCENT, share));
}
