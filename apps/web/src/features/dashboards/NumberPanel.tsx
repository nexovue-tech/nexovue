import { Delta } from "@/shared/components/Delta";

import type { NumberReading, StoredPanel } from "./api";
import { comparisonOf, formatMeasure, NOT_ENOUGH_HISTORY } from "./number-reading";
import { MEASURE_NAMES } from "./panel-words";

/**
 * One number, and how it moved against the window before when asked (F20, T5.3, T5.6a).
 * A number nobody measured never reaches here: an `empty` panel says so in words, so `0`
 * on this panel is a real zero (principles rule 2).
 */
export function NumberPanel({ panel, number }: { panel: StoredPanel; number: NumberReading }) {
  const comparison = comparisonOf(number, panel.settings.compare === true);
  const label = MEASURE_NAMES[number.measure] ?? number.measure;
  return (
    <div className="flex flex-col gap-1">
      <p className="font-mono text-2xl text-ink">
        {formatMeasure(number.measure, number.value)}
        {comparison.kind === "compared" && comparison.delta && comparison.worse ? (
          <Delta delta={comparison.delta} worse={comparison.worse} label={label} />
        ) : null}
      </p>
      {comparison.kind === "compared" ? (
        <p className="text-ink-dim text-xs">Previous window: {comparison.previous}</p>
      ) : null}
      {comparison.kind === "not_enough_history" ? (
        <p className="text-ink-dim text-xs">{NOT_ENOUGH_HISTORY}</p>
      ) : null}
    </div>
  );
}
