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
  const slowest = Math.max(...ordered.map((run) => run.duration_ms ?? 0), 1);

  return (
    <div className="flex h-10 items-end gap-px" aria-hidden="true">
      {ordered.map((run) => (
        <span
          key={`${run.timestamp}-${run.run_id}`}
          title={[
            formatTimestamp(run.timestamp),
            run.status_code > 0 ? String(run.status_code) : undefined,
            run.duration_ms === null ? undefined : formatDuration(run.duration_ms),
          ]
            .filter((fact) => fact !== undefined)
            .join(" · ")}
          style={{ height: `${markHeightPercent(run.duration_ms, slowest)}%` }}
          className={`min-w-px flex-1 ${run.up ? "bg-solid" : "bg-critical-solid"}`}
        />
      ))}
    </div>
  );
}

/**
 * A fast probe is still a probe: it gets a visible mark rather than a zero-height one.
 * A run that timed nothing — a heartbeat, or one refused before its request — has no
 * height to draw, so it gets the same smallest mark.
 */
function markHeightPercent(durationMs: number | null, slowestMs: number): number {
  if (durationMs === null) return MINIMUM_MARK_PERCENT;
  const share = (Math.max(0, durationMs) / slowestMs) * FULL_PERCENT;
  return Math.max(MINIMUM_MARK_PERCENT, Math.min(FULL_PERCENT, share));
}
