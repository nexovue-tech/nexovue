import type { ChainStep } from "./chain";

const STATUS_CLASS: Record<ChainStep["status"], string> = {
  done: "",
  now: "bg-brand-surface shadow-[inset_0_-2px_0_var(--color-brand)]",
  later: "opacity-60",
  skipped: "opacity-60",
};

const STATUS_WORDS: Record<ChainStep["status"], string> = {
  done: "done",
  now: "next",
  later: "not yet",
  skipped: "skipped",
};

/**
 * The five steps from a finding to a pull request, under the title (approved drawing C).
 * Each step links to its section; the step marked next is where the page's one filled
 * button lives. Stacked one step a row below `@3xl`.
 */
export function ChainStrip({ steps }: { steps: readonly ChainStep[] }) {
  return (
    <nav aria-label="From finding to pull request">
      <ol className="grid border border-line bg-surface @3xl:grid-cols-5">
        {steps.map((step, index) => (
          <li
            key={step.name}
            className={`min-w-0 border-line ${index > 0 ? "border-t @3xl:border-t-0 @3xl:border-l" : ""} ${STATUS_CLASS[step.status]}`}
          >
            <a
              href={`#${step.anchor}`}
              aria-current={step.status === "now" ? "step" : undefined}
              className="flex min-h-11 flex-col gap-1 px-4 py-3 hover:bg-hover"
            >
              <span
                className={`flex items-center gap-2 font-mono text-2xs uppercase tracking-caps ${step.status === "now" ? "text-brand-text" : "text-ink-dim"}`}
              >
                {step.status === "done" ? (
                  <span aria-hidden="true" className="font-sans text-brand-text">
                    ✓
                  </span>
                ) : null}
                {step.name}
                <span className="sr-only">, {STATUS_WORDS[step.status]}</span>
              </span>
              <span className="font-semibold text-ink text-sm [overflow-wrap:anywhere]">
                {step.state}
              </span>
              {step.detail ? (
                <span className="text-ink-dim text-xs [overflow-wrap:anywhere]">{step.detail}</span>
              ) : null}
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}
