import { Link } from "@tanstack/react-router";

import type { StepView } from "./api";
import { stepOutcome, stepRange, stepTarget } from "./step-links";

const MONO = "font-mono text-xs text-ink-dim";
const QUIET_STATES = ["read", "running", "aged_out"];

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
  const to = step.link ? stepTarget(step.link) : null;
  const range = stepRange(step, zone, now);
  const outcome = stepOutcome(step);
  const failed = !QUIET_STATES.includes(step.state);
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
      {outcome ? (
        <p className={failed ? "text-critical-text text-xs" : "text-ink-dim text-xs"}>{outcome}</p>
      ) : null}
      {to && step.link ? (
        <p className="text-xs">
          <Link
            to={to}
            params={step.link.params as never}
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
