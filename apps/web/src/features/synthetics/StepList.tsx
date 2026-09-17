import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";

import { Button } from "@/shared/components/Button";

import type { FieldProblems, RequestDraft } from "./check-draft";
import { emptyRequest, problemsUnder } from "./check-draft";
import { LIMITS } from "./check-model";
import { GroupProblem } from "./fields";

/**
 * A multistep check's requests, in the one place where order carries meaning: a request
 * may use what an earlier one extracted and never a later one (FR10). The selected
 * request is edited beside the list.
 */
export function StepList({
  steps,
  selected,
  onSelect,
  onChange,
  problems,
}: {
  steps: readonly RequestDraft[];
  selected: number;
  onSelect: (index: number) => void;
  onChange: (steps: RequestDraft[]) => void;
  problems: FieldProblems;
}) {
  const move = (from: number, to: number) => {
    const next = [...steps];
    const [step] = next.splice(from, 1);
    if (!step) return;
    next.splice(to, 0, step);
    onChange(next);
    onSelect(to);
  };

  return (
    <div className="flex flex-col gap-2">
      <GroupProblem message={problems["config.steps"]} />
      <ol aria-label="Requests, in order" className="flex flex-col gap-1">
        {steps.map((step, index) => {
          const invalid = problemsUnder(problems, `config.steps.${index}`).length > 0;
          const title =
            step.name || `${step.method} ${step.url.replace(/^https?:\/\//, "") || "new request"}`;
          return (
            <li
              key={step.key}
              className={`flex items-center gap-1 border-l-2 ${
                index === selected ? "border-l-brand bg-raised" : "border-l-transparent"
              }`}
            >
              <button
                type="button"
                aria-current={index === selected ? "step" : undefined}
                onClick={() => onSelect(index)}
                className="flex min-w-0 flex-1 items-baseline gap-2 px-2 py-1.5 text-left text-sm hover:bg-hover"
              >
                <span className="font-mono text-2xs text-ink-dim">{index + 1}</span>
                <span className="truncate text-ink">{title}</span>
                {invalid ? (
                  <span className="shrink-0 text-critical-text text-xs">has a problem</span>
                ) : null}
              </button>
              <IconButton
                label={`Move request ${index + 1} up`}
                disabled={index === 0}
                onClick={() => move(index, index - 1)}
              >
                <ArrowUp size={14} aria-hidden="true" />
              </IconButton>
              <IconButton
                label={`Move request ${index + 1} down`}
                disabled={index === steps.length - 1}
                onClick={() => move(index, index + 1)}
              >
                <ArrowDown size={14} aria-hidden="true" />
              </IconButton>
              <IconButton
                label={`Remove request ${index + 1}`}
                disabled={steps.length === 1}
                onClick={() => {
                  onChange(steps.filter((_, position) => position !== index));
                  onSelect(Math.max(0, Math.min(selected, steps.length - 2)));
                }}
              >
                <Trash2 size={14} aria-hidden="true" />
              </IconButton>
            </li>
          );
        })}
      </ol>
      <div>
        <Button
          variant="secondary"
          disabled={steps.length >= LIMITS.maxSteps}
          onClick={() => {
            onChange([...steps, emptyRequest()]);
            onSelect(steps.length);
          }}
        >
          Add request
        </Button>
        {steps.length >= LIMITS.maxSteps ? (
          <p className="mt-1 text-ink-dim text-xs">
            A check runs at most {LIMITS.maxSteps} requests.
          </p>
        ) : null}
      </div>
    </div>
  );
}

function IconButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="rounded-sm p-1 text-ink-dim hover:bg-hover hover:text-ink disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-brand"
    >
      {children}
    </button>
  );
}
