import { Link } from "@tanstack/react-router";

import { formatZonedTime } from "@/shared/format/zoned";

import type { StepView } from "./api";

/**
 * Where a step may lead: the catalog's own screens (design, *Where each step leads*). The
 * API builds the link; a path outside this list is shown as no link at all.
 */
const STEP_TARGETS = ["/", "/services", "/logs"] as const;
type StepTarget = (typeof STEP_TARGETS)[number];

function target(path: string): StepTarget | null {
  return (STEP_TARGETS as readonly string[]).includes(path) ? (path as StepTarget) : null;
}

const MONO = "font-mono text-xs text-ink-dim";

export function StepRow({
  step,
  zone,
  now,
  onLeave,
}: {
  step: StepView;
  zone: string | undefined;
  now: number;
  onLeave?: () => void;
}) {
  const to = step.link ? target(step.link.path) : null;
  const range =
    step.from_time && step.to_time
      ? `${formatZonedTime(step.from_time, { zone, now })} to ${formatZonedTime(step.to_time, { zone, now })}`
      : null;
  const failed = step.state !== "read" && step.state !== "running";
  return (
    <li id={`step-${step.key}`} tabIndex={-1} className="flex flex-col gap-1 py-2">
      <p className="text-ink text-sm">
        <span className={MONO}>{step.key} </span>
        {step.words}
        {range ? <span className="text-ink-dim">, {range}</span> : null}
      </p>
      {step.display_arguments ? (
        <p className="text-ink-dim text-xs">
          The AI searched for:{" "}
          <span className="break-words font-mono text-ink" inert>
            {step.display_arguments}
          </span>
        </p>
      ) : null}
      {step.search_hidden ? (
        <p className="text-ink-dim text-xs">The AI searched for text that is not shown here.</p>
      ) : null}
      {step.state === "read" ? (
        <p className="text-ink-dim text-xs">
          {step.row_count ?? 0} rows read
          {step.was_cut ? `. Only the first ${step.row_count ?? 0} rows were read` : ""}
        </p>
      ) : null}
      {failed ? (
        <p className="text-critical-text text-xs">
          This read {step.state === "refused" ? "was refused" : "failed"}
          {step.failure ? ` (${step.failure.replaceAll("_", " ")})` : ""}.
        </p>
      ) : null}
      {to && step.link ? (
        <p className="text-xs">
          <Link
            to={to}
            search={step.link.query as never}
            onClick={onLeave}
            className="font-medium text-brand-text underline-offset-2 hover:underline"
          >
            Open what it read
          </Link>
          {step.link.caveat ? <span className="text-ink-dim"> {step.link.caveat}</span> : null}
        </p>
      ) : null}
    </li>
  );
}
