import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { SelectField } from "@/shared/components/SelectField";
import { fromLocalInput, isUsableRange, toLocalInput } from "@/shared/local-instant";
import {
  clearAbsoluteWindow,
  setAbsoluteWindow,
  setSelectedRangeId,
  TIME_RANGES,
  useLiveWindow,
  useSelection,
} from "@/shared/selection";

/**
 * Which window every screen is read against — "the last hour", or an incident.
 *
 * **Global, and that is the point.** An investigation goes services → a service →
 * traces → logs, and re-entering the window on each is friction exactly where a reader
 * can least afford it. The window travelling with them is what makes that work.
 *
 * **Absolute is what makes retention reachable.** The relative options stop at seven
 * days; traces are kept for thirty and metrics for over a year, so most of what a
 * customer pays to store could not be asked for from any screen until now.
 */
export function WindowControl() {
  const { window: choice } = useSelection();
  const live = useLiveWindow();
  const [picking, setPicking] = useState(false);

  if (choice.kind === "absolute") {
    return (
      <span className="flex items-center gap-2">
        <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Window</span>
        <span className="text-ink text-sm">
          {new Date(choice.from).toLocaleString()} — {new Date(choice.to).toLocaleString()}
        </span>
        {/* The way back is on the control itself. A window somebody pinned during an
            incident is one they will want to leave, and hunting for how is its own
            small failure. */}
        <button
          type="button"
          onClick={clearAbsoluteWindow}
          className="text-brand-text text-sm underline-offset-2 hover:underline"
        >
          Back to live
        </button>
      </span>
    );
  }

  return (
    <span className="flex items-center gap-3">
      <SelectField
        label="Last"
        value={live.isAbsolute ? "" : choice.rangeId}
        options={TIME_RANGES}
        onChange={setSelectedRangeId}
        optionLabel={(range) => range.label}
      />
      <button
        type="button"
        onClick={() => setPicking(true)}
        className="text-ink-dim text-xs underline-offset-2 hover:text-ink hover:underline"
      >
        Pick a range
      </button>
      {picking ? <RangePicker onClose={() => setPicking(false)} /> : null}
    </span>
  );
}

function RangePicker({ onClose }: { onClose: () => void }) {
  const live = useLiveWindow();
  const [from, setFrom] = useState(() => toLocalInput(live.from));
  const [to, setTo] = useState(() => toLocalInput(live.to));

  const chosen = { from: fromLocalInput(from), to: fromLocalInput(to) };
  const usable = isUsableRange(chosen.from, chosen.to);

  // Seeded from the window already on screen rather than from empty fields: somebody
  // pinning a range is almost always narrowing the one they are looking at.
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-scrim p-4 pt-24">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="range-picker-title"
        className="w-full max-w-md rounded border border-line bg-raised p-5 shadow-overlay"
      >
        <h2 id="range-picker-title" className="font-medium font-serif text-base text-ink">
          Pick a range
        </h2>
        <p className="mt-1 text-ink-dim text-sm">
          Times are yours, and are read against telemetry in UTC. A pinned window stops refreshing
          on its own — nothing new can arrive in a period that has ended.
        </p>

        <div className="mt-4 flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">From</span>
            <input
              type="datetime-local"
              value={from}
              onChange={(event) => setFrom(event.target.value)}
              className="rounded border border-line bg-transparent px-2 py-1.5 font-mono text-ink text-sm"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">To</span>
            <input
              type="datetime-local"
              value={to}
              onChange={(event) => setTo(event.target.value)}
              className="rounded border border-line bg-transparent px-2 py-1.5 font-mono text-ink text-sm"
            />
          </label>
        </div>

        {/* Said before the button is pressed rather than after. The two ways to get
            this wrong are running it backwards and giving it no length, and both look
            on a screen like a filter that found nothing. */}
        {!usable && (from || to) ? (
          <p className="mt-3 text-warning-text text-sm">
            A range needs a start and an end, and the start has to come first.
          </p>
        ) : null}

        <div className="mt-4 flex gap-2">
          <Button
            disabled={!usable}
            onClick={() => {
              if (chosen.from && chosen.to) setAbsoluteWindow(chosen.from, chosen.to);
              onClose();
            }}
          >
            Use this range
          </Button>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}
