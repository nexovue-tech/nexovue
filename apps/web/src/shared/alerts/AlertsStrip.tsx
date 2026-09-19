import { Link } from "@tanstack/react-router";

import { formatRelativeTime } from "../format/time";
import type { Coverage } from "./api";

/**
 * A subject's alerting at a glance: any open incident, the rules that would tell
 * someone, and the way to add one — from the thing a person is already looking at,
 * rather than from a settings screen they would have to come back from (FR24).
 */
export function AlertsStrip({
  coverage,
  newRuleSearch,
}: {
  coverage: Coverage | undefined;
  /** Prefills the new rule with this subject. */
  newRuleSearch: { check?: string; service?: string };
}) {
  if (coverage === undefined) return null;
  const [open] = coverage.open_incidents;
  const active = coverage.rules.filter((rule) => rule.enabled && !rule.notifying_nobody);

  return (
    <section
      aria-label="Alerts"
      className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-line-subtle border-b py-2 text-sm"
    >
      <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Alerts</span>
      {open ? (
        <Link to="/alerts" className="font-medium text-critical-text hover:underline">
          Open incident — {open.severity}, since {formatRelativeTime(open.opened_at)}
        </Link>
      ) : null}
      <span className="text-ink-dim">
        {active.length === 0 ? (
          "No rule tells anyone when this breaks."
        ) : (
          <>
            {active.length === 1 ? "Watched by " : `Watched by ${active.length} rules: `}
            {active.map((rule, index) => (
              <span key={rule.id}>
                {index > 0 ? ", " : ""}
                <Link
                  to="/alerts/rules/$ruleId"
                  params={{ ruleId: rule.id }}
                  className="text-ink hover:underline"
                >
                  {rule.name}
                </Link>
                {isMuted(rule.muted_until) ? " (muted)" : ""}
              </span>
            ))}
          </>
        )}
      </span>
      <Link
        to="/alerts/rules/new"
        search={newRuleSearch}
        className="font-medium text-brand-text hover:underline"
      >
        Alert me about this
      </Link>
    </section>
  );
}

/** Still watching, but telling nobody until the mute ends. */
const isMuted = (until: string | null) => until !== null && new Date(until).getTime() > Date.now();
