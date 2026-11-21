import { Link } from "@tanstack/react-router";

import { SloBadge } from "@/shared/slo/SloBadge";

import type { SloBudget } from "./api";
import { budgetWords } from "./slo-budget-words";

/**
 * One reliability target on a dashboard (T10.2d): its state, what is still allowed, and
 * while it is running out how fast it goes, drawn from F19's Postgres-only figures. The
 * badge is the target page's own, so a panel says and colours a state as that page does,
 * and On track carries none there either.
 */
export function SloBudgetPanel({ budget, now }: { budget: SloBudget; now: number }) {
  const words = budgetWords(budget, now);
  return (
    <div className="flex flex-col items-start gap-1 text-sm">
      <SloBadge
        view={{ state: words.state, severity: budget.severity, attention: budget.attention }}
      />
      <p className="text-ink">{words.allowance}</p>
      {words.burn ? <p className="text-ink">{words.burn}</p> : null}
      <p className="text-ink-dim text-xs">{words.asOf}</p>
      <Link
        to="/slos/$sloId"
        params={{ sloId: budget.id }}
        className="inline-flex min-h-11 items-center text-brand underline md:min-h-0"
      >
        Open the target
      </Link>
    </div>
  );
}
