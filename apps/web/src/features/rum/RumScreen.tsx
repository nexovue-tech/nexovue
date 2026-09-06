import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatCount } from "@/shared/format/number";
import { timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";

import { useWebVitals } from "./api";
import type { ReadableVital } from "./vitals";
import { describeVitals, MIN_READINGS, readableVitals } from "./vitals";

/* Every rating's colour, named once so a card and its footnote cannot disagree. */
const RATING_STYLES: Record<ReadableVital["rating"], string> = {
  good: "text-ok-text",
  "needs-improvement": "text-warn-text",
  poor: "text-critical-text",
  insufficient: "text-ink-dim",
};

/* What each rating is called in words. Colour never carries the meaning alone. */
const RATING_WORDS: Record<ReadableVital["rating"], string> = {
  good: "Good",
  "needs-improvement": "Needs improvement",
  poor: "Poor",
  insufficient: "Not enough data",
};

/**
 * What real browsers experienced: each Core Web Vital at the 75th percentile.
 *
 * The route component owns the selection and the query; everything visible is in
 * `RumView`, which is a function of its props and nothing else.
 */
export function RumScreen() {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useLiveWindow(rangeId);
  const vitals = useWebVitals(projectId, timeWindow);

  return (
    <RumView
      windowLabel={timeRangeById(rangeId).label}
      vitals={readableVitals(vitals.data?.vitals ?? [])}
      isPending={vitals.isPending}
      isError={vitals.isError}
      error={vitals.error}
      onRetry={() => void vitals.refetch()}
    />
  );
}

interface RumViewProps {
  /** "hour", "24 hours" — the range picker's own word, so the two agree. */
  windowLabel: string;
  vitals: readonly ReadableVital[];
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
}

/** Everything the screen shows, with no query client and no clock behind it. */
export function RumView({ windowLabel, vitals, isPending, isError, error, onRetry }: RumViewProps) {
  const anyReadings = vitals.some((vital) => vital.readings > 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">
          Real user monitoring
        </h1>
        <p className="mt-1 text-ink-dim text-sm">
          {isPending || isError
            ? `What real browsers measured in the last ${windowLabel}.`
            : describeVitals(vitals, windowLabel)}
        </p>
      </header>

      {isPending ? (
        <div className="px-4">
          <SkeletonRows rows={5} label="Loading web vitals" />
        </div>
      ) : isError ? (
        <div className="px-4">
          <InlineFailure title="Web vitals could not be loaded" error={error} onRetry={onRetry} />
        </div>
      ) : !anyReadings ? (
        <EmptyState
          title="No browser has reported yet"
          detail="Core Web Vitals arrive from the browser SDK on real page loads, not from your servers. Add the script to your site, or widen the time range — a quiet hour on a low-traffic page looks exactly like this."
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-6">
          <VitalGroup
            heading="Core Web Vitals"
            note="The three Google measures a site on."
            vitals={vitals.filter((vital) => vital.core)}
          />
          <VitalGroup
            heading="Diagnostics"
            note="Not scored, but they explain a slow LCP."
            vitals={vitals.filter((vital) => !vital.core)}
          />
        </div>
      )}
    </div>
  );
}

function VitalGroup({
  heading,
  note,
  vitals,
}: {
  heading: string;
  note: string;
  vitals: readonly ReadableVital[];
}) {
  return (
    <section className="mt-4 first:mt-2">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {heading} <span className="normal-case tracking-normal">— {note}</span>
      </h2>
      <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {vitals.map((vital) => (
          <VitalCard key={vital.name} vital={vital} />
        ))}
      </div>
    </section>
  );
}

/**
 * One vital: the number, what it means, and how many page loads are behind it.
 *
 * The sample size is on the card rather than in a tooltip because it is what decides
 * whether the number should be believed, and a p75 over nine readings looks exactly
 * like a p75 over nine thousand.
 */
function VitalCard({ vital }: { vital: ReadableVital }) {
  const thin = vital.readings > 0 && vital.readings < MIN_READINGS;

  return (
    <article className="rounded-md border border-line bg-surface px-3.5 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium font-mono text-ink text-sm">{vital.label}</span>
        <span className={`text-2xs ${RATING_STYLES[vital.rating]}`}>
          {RATING_WORDS[vital.rating]}
        </span>
      </div>

      <p
        className={`mt-1.5 font-semibold font-serif text-2xl tabular-nums ${
          vital.rating === "insufficient" ? "text-ink-dim" : "text-ink"
        }`}
      >
        {vital.display}
      </p>

      <p className="mt-1 text-ink-dim text-xs">{vital.meaning}</p>

      <p className="mt-2 font-mono text-2xs text-ink-dim">
        {vital.readings === 0
          ? "no readings in this window"
          : thin
            ? `${formatCount(vital.readings)} readings — too few for a percentile`
            : `${formatCount(vital.readings)} readings`}
      </p>
    </article>
  );
}
