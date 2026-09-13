/**
 * How a reading changed, shown beside it — and only when it is worth saying.
 *
 * Direction is not judgement. A rising request rate is usually good and a rising
 * error rate never is, so the caller says which direction is the bad one and
 * this colours accordingly. A change in the harmless direction is shown in the
 * dim ink every other secondary number uses: it is context, not a finding.
 */

import type { Delta as DeltaReading } from "@/shared/delta";

export function Delta({
  delta,
  worse,
  label,
}: {
  delta: DeltaReading | null;
  /** Which direction is the bad one for this measure. */
  worse: "up" | "down";
  /** What changed, for a reader who cannot see the column header. */
  label: string;
}) {
  if (delta === null) return null;

  const harmful = delta.direction === worse;
  return (
    <span
      className={`ml-1.5 font-mono text-2xs ${harmful ? "text-critical-text" : "text-ink-dim"}`}
      title={`${label} ${delta.direction === "up" ? "rose" : "fell"} against the previous period`}
    >
      {delta.display}
    </span>
  );
}
