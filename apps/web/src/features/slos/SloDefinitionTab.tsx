import type { SloKind, SloView } from "./api";
import { DEFINITIONS } from "./slo-words";

/** FR27: how far back a target's figures reach, by the source its kind reads. */
const SERVICE_REACH =
  "Its figures start about a month before it was created: a service's per-minute counts are kept 35 days at most.";
const DERIVED_REACH =
  "Its figures start as far back as your plan keeps per-minute counts: 30 days, or 395 on a paid plan.";
const REACH: Record<SloKind, string> = {
  service_availability: SERVICE_REACH,
  service_latency: SERVICE_REACH,
  endpoint_availability: DERIVED_REACH,
  check_availability: DERIVED_REACH,
};

/**
 * *Definition* on a target's page: what it counts, in the kind's own words (FR5), and
 * the one place the screens say "error budget" (AC13, D16), so a reader who knows the
 * term finds what nexovue calls it.
 */
export function SloDefinitionTab({ view }: { view: SloView }) {
  return (
    <div className="flex max-w-prose flex-col gap-3 py-3 text-sm">
      <p className="text-ink">
        This target counts {view.label}, by whole days in the project's zone.
      </p>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-ink-dim">
        {DEFINITIONS[view.kind].map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <p className="text-ink-dim">{REACH[view.kind]}</p>
      <p className="text-ink-dim">
        What its target allows over the window is often called an error budget. Nexovue calls it{" "}
        {view.allowance_words}, so a target never says it has money to spend.
      </p>
    </div>
  );
}
