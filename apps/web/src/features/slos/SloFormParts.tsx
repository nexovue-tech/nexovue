import type { UseQueryResult } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { useCan } from "@/shared/identity";

import type { SloKind, SloSuggestion } from "./api";
import {
  ASK_WHO_CAN_ADD,
  CHECK_TOO_INFREQUENT,
  checkAllowanceLine,
  PRE_TICKED,
  STOPS_COMPLETELY,
  SUGGESTION_FAILED,
  suggestionSentence,
} from "./slo-words";

/**
 * The suggestion and its arithmetic (FR7, FR8), in each of its states (design, *Form
 * states*): loading is a skeleton and the objective stays usable; a failure says so
 * with a retry, and the form still saves (AC106).
 */
export function SuggestionPanel({
  suggestion,
  kind,
  objective,
  windowDays,
  onCheckTarget,
}: {
  suggestion: UseQueryResult<SloSuggestion>;
  kind: SloKind;
  objective: string;
  windowDays: number;
  onCheckTarget: () => void;
}) {
  if (suggestion.isError) {
    return (
      <InlineFailure
        title={SUGGESTION_FAILED}
        error={suggestion.error}
        onRetry={() => void suggestion.refetch()}
      />
    );
  }
  if (suggestion.fetchStatus === "idle" && suggestion.isPending) return null;
  if (suggestion.isPending) return <SkeletonRows rows={2} label="Reading the history" />;
  const read = suggestion.data;
  const window = read.check?.windows.find((each) => each.window_days === windowDays);
  return (
    <section aria-label="Suggestion" className="flex max-w-prose flex-col gap-2">
      <h2 className="font-semibold text-ink">Suggested</h2>
      <p className="text-ink">
        {suggestionSentence({
          outcome: read.outcome,
          objective: read.objective,
          worst: read.worst,
          typical: read.typical,
          boundaryMs: read.boundary_ms,
          kindIsLatency: kind === "service_latency",
        })}
      </p>
      {read.weeks.some((week) => week.figure !== null) ? (
        <p className="text-ink-dim">
          Weeks, newest first:{" "}
          {read.weeks
            .map((week) => (week.figure === null ? "—" : `${Number(week.figure)}%`))
            .join(", ")}
          .
        </p>
      ) : null}
      {read.check && window ? (
        <p className="text-ink">
          {checkAllowanceLine({
            intervalSeconds: read.check.interval_seconds,
            objective,
            windowDays,
            expectedRuns: window.expected_runs,
            fastestExpectedRuns: window.fastest_expected_runs,
            steps: read.steps,
            thinAllowed: read.thin_allowed,
          })}
        </p>
      ) : null}
      {read.check && !read.check.alerts_can_judge ? (
        <p className="text-warning-text">{CHECK_TOO_INFREQUENT}</p>
      ) : null}
      {read.offer_check ? (
        <div>
          <Button variant="secondary" onClick={onCheckTarget}>
            Add a check-based target
          </Button>
        </div>
      ) : null}
    </section>
  );
}

/** FR34: a service's endpoints to leave out, health checks pre-ticked and named. */
export function LeftOutEndpoints({
  endpoints,
  chosen,
  error,
  onChange,
}: {
  endpoints: ReadonlyArray<{ name: string; health_check: boolean }>;
  chosen: readonly string[];
  error: string | undefined;
  onChange: (names: string[]) => void;
}) {
  const preTicked = endpoints.filter((each) => each.health_check).map((each) => each.name);
  const toggle = (name: string) =>
    onChange(chosen.includes(name) ? chosen.filter((each) => each !== name) : [...chosen, name]);
  return (
    <fieldset className="flex flex-col gap-1 text-sm">
      <legend className="font-medium text-ink">Leave out of this target</legend>
      {preTicked.length > 0 ? (
        <p className="max-w-prose text-ink-dim">
          {PRE_TICKED} {preTicked.join(", ")}.
        </p>
      ) : null}
      {endpoints.map((endpoint) => (
        <label key={endpoint.name} className="flex items-center gap-2 break-all">
          <input
            type="checkbox"
            name="left_out_endpoints"
            value={endpoint.name}
            checked={chosen.includes(endpoint.name)}
            onChange={() => toggle(endpoint.name)}
          />
          {endpoint.name}
        </label>
      ))}
      {error ? <p className="text-critical-text text-xs">{error}</p> : null}
    </fieldset>
  );
}

/**
 * FR25a, AC79: no silence rule watches this service, so a target alone does not alert
 * when it stops. The links prefill a form for those allowed to make one.
 */
export function StopsCompletely({ service }: { service: string }) {
  const mayAddRule = useCan("operate_alerts");
  const mayAddCheck = useCan("operate_checks");
  return (
    <div className="flex max-w-prose flex-col gap-1">
      <p className="text-warning-text">{STOPS_COMPLETELY}</p>
      {mayAddRule || mayAddCheck ? (
        <p className="flex flex-wrap gap-x-3 text-ink-dim">
          {mayAddRule ? (
            <Link
              to="/alerts/rules/new"
              search={{ service }}
              className="font-medium text-brand-text hover:underline"
            >
              Add a silence rule
            </Link>
          ) : null}
          {mayAddCheck ? (
            <Link to="/synthetics/new" className="font-medium text-brand-text hover:underline">
              Add a check
            </Link>
          ) : null}
        </p>
      ) : (
        <p className="text-ink-dim">{ASK_WHO_CAN_ADD}</p>
      )}
    </div>
  );
}
