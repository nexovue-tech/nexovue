import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useMemo } from "react";

import { EmptyState } from "@/shared/components/EmptyState";
import { appliedFilters, FilterBar } from "@/shared/components/FilterBar";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows, SkeletonText } from "@/shared/components/Skeleton";
import { Sparkline } from "@/shared/components/Sparkline";
import { filterParams, parseFilterValues, withFilter } from "@/shared/filters";
import { formatDuration } from "@/shared/format/duration";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useCan } from "@/shared/identity";
import { useLiveWindow, useSelection, useWindowPhrase } from "@/shared/selection";

import type { CheckDefinition, CheckResultSummary, CheckTrendPoint } from "./api";
import { useCheckDefinitions, useCheckResults, useCheckTrend } from "./api";
import type { Urgency } from "./check-health";
import { certificateUrgency, formatCertificateAge, formatUptime } from "./check-health";
import { SYNTHETIC_FILTERS, visibleChecks } from "./filters";
import { KIND_DESCRIPTIONS } from "./NewCheckScreen";
import { STATE_WORDS, stateUrgency } from "./outcomes";

/**
 * Every check, every kind, with the state its runs confirmed (US18.14, AC29).
 *
 * Two reads, because they answer different questions: the definitions say what exists
 * and what it is confirmed to be, the readings say how it behaved over the window. A
 * check with no readings is still listed — it is paused, in maintenance, not run yet or
 * overdue, and its own page says which.
 */

const URGENCY_RULE: Record<Urgency, string> = {
  critical: "border-l-critical-solid",
  warning: "border-l-warning-solid",
  none: "border-l-transparent",
};

const URGENCY_TEXT: Record<Urgency, string> = {
  critical: "font-medium text-critical-text",
  warning: "font-medium text-warning-text",
  none: "text-ink-dim",
};

const ACTION =
  "rounded-sm px-3 py-1.5 font-medium text-sm transition-colors focus-visible:outline-2 focus-visible:outline-brand";

export function SyntheticsScreen() {
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const windowPhrase = useWindowPhrase();
  const navigate = useNavigate();

  const search = useSearch({ from: "/shell/synthetics" });
  const filters = useMemo(() => parseFilterValues(SYNTHETIC_FILTERS, search), [search]);
  const show = (next: Record<string, string | undefined>) =>
    void navigate({ to: "/synthetics", search: () => filterParams(next), replace: true });

  const definitions = useCheckDefinitions(projectId);
  const results = useCheckResults(projectId, timeWindow);
  const trendByCheck = useCheckTrend(projectId, timeWindow);

  const all = definitions.data ?? [];
  const checks = visibleChecks(all, filters);
  const readings = new Map((results.data ?? []).map((result) => [result.check_id, result]));
  const narrowed = appliedFilters(SYNTHETIC_FILTERS, filters).length > 0;
  const canOperate = useCan("operate_checks");

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="flex shrink-0 flex-wrap items-end justify-between gap-3 px-4 pt-5 pb-3">
        <div className="min-w-0">
          <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Synthetics</h1>
          <p className="mt-1 text-ink-dim text-sm">
            What we check for you, from outside: whether it is up, since when, and how it behaved in{" "}
            {windowPhrase}.
          </p>
        </div>
        {canOperate ? (
          <div className="flex gap-2">
            <Link
              to="/synthetics/import"
              className={`${ACTION} border border-line text-ink-dim hover:border-line-strong hover:text-ink`}
            >
              Import
            </Link>
            <Link
              to="/synthetics/new"
              className={`${ACTION} bg-brand text-brand-contrast hover:bg-brand-hover`}
            >
              New check
            </Link>
          </div>
        ) : null}
      </header>

      <FilterBar
        definitions={SYNTHETIC_FILTERS}
        values={filters}
        onChange={(key, value) => show(withFilter(filters, key, value))}
        onClearAll={() => show({})}
      />

      {definitions.isPending ? (
        <div className="px-4">
          <SkeletonRows rows={6} label="Loading checks" />
        </div>
      ) : definitions.isError ? (
        <div className="px-4">
          <InlineFailure
            title="Checks could not be loaded"
            error={definitions.error}
            onRetry={() => definitions.refetch()}
          />
        </div>
      ) : all.length === 0 ? (
        <EmptyState
          title="No checks yet"
          detail="A check calls your API, port, domain or job from outside and tells you when it stops answering the way it should. Start from a URL, or import the requests you already have."
          action={
            canOperate ? (
              <div className="flex gap-2">
                <Link
                  to="/synthetics/new"
                  className={`${ACTION} bg-brand text-brand-contrast hover:bg-brand-hover`}
                >
                  New check
                </Link>
                <Link
                  to="/synthetics/import"
                  className={`${ACTION} border border-line text-ink-dim hover:text-ink`}
                >
                  Import cURL, OpenAPI or Postman
                </Link>
              </div>
            ) : undefined
          }
        />
      ) : checks.length === 0 && narrowed ? (
        <EmptyState
          title="No checks match these filters"
          detail={`${all.length} ${all.length === 1 ? "check exists" : "checks exist"}, but none match. If you asked for what is down, nothing is.`}
          action={
            <button
              type="button"
              onClick={() => show({})}
              className={`${ACTION} border border-line text-ink-dim hover:text-ink`}
            >
              Show every check
            </button>
          }
        />
      ) : (
        <>
          {results.isError ? (
            <div className="px-4 pb-2">
              <InlineFailure
                title="Uptime and timings could not be loaded"
                error={results.error}
                onRetry={() => results.refetch()}
              />
            </div>
          ) : null}
          <CheckTable
            checks={checks}
            readings={readings}
            trendByCheck={trendByCheck}
            readingsPending={results.isPending}
          />
        </>
      )}
    </div>
  );
}

/* 104px of sparkline plus its 12px gutter, reserved on every row. */
const TREND_COLUMN = "w-29 shrink-0 pl-3";

/**
 * One scale for the screen, in milliseconds, so two checks are comparable.
 *
 * Buckets with no successful run carry no duration and contribute nothing to the
 * ceiling — a failure must not raise or lower the scale the successes are drawn against.
 */
export function trendCeiling(
  checks: readonly CheckResultSummary[],
  trendByCheck: ReadonlyMap<string, readonly CheckTrendPoint[]> | undefined,
): number {
  let ceiling = 0;
  for (const check of checks) {
    for (const point of trendByCheck?.get(check.check_id) ?? []) {
      if (point.mean_duration_ms !== null && point.mean_duration_ms > ceiling) {
        ceiling = point.mean_duration_ms;
      }
    }
  }
  return ceiling;
}

function CheckTable({
  checks,
  readings,
  trendByCheck,
  readingsPending,
}: {
  checks: readonly CheckDefinition[];
  readings: ReadonlyMap<string, CheckResultSummary>;
  trendByCheck: ReadonlyMap<string, readonly CheckTrendPoint[]>;
  readingsPending: boolean;
}) {
  const ceiling = trendCeiling([...readings.values()], trendByCheck);
  return (
    <div className="shrink-0">
      <div className="flex items-center border-line border-b bg-surface px-4 py-1.5 pl-6 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        <span className="w-28 shrink-0">State</span>
        <span className="min-w-0 flex-1">Check</span>
        <span className="w-20 text-right">Uptime</span>
        <span className="hidden w-20 text-right sm:block">p95</span>
        <span className="hidden w-32 text-right lg:block">Certificate</span>
        <span className={`hidden md:block ${TREND_COLUMN}`}>Trend</span>
      </div>
      {checks.map((check) => (
        <CheckRow
          key={check.id}
          check={check}
          reading={readings.get(check.id)}
          readingsPending={readingsPending}
          trend={trendByCheck.get(check.id)}
          ceiling={ceiling}
        />
      ))}
    </div>
  );
}

function CheckRow({
  check,
  reading,
  readingsPending,
  trend,
  ceiling,
}: {
  check: CheckDefinition;
  reading: CheckResultSummary | undefined;
  readingsPending: boolean;
  trend?: readonly CheckTrendPoint[];
  ceiling: number;
}) {
  const state = check.displayed_state;
  const urgency = stateUrgency(state);
  // Still loading is a placeholder; loaded with nothing to show is the em dash (principle 2).
  const missingReading = readingsPending ? <SkeletonText width="full" /> : "—";
  return (
    <Link
      to="/synthetics/$checkId"
      params={{ checkId: check.id }}
      className={`flex w-full items-center border-line-subtle border-b border-l-2 px-4 py-2 text-left text-sm hover:bg-hover focus-visible:outline-2 focus-visible:outline-brand ${URGENCY_RULE[urgency]}`}
    >
      <span className="flex w-28 shrink-0 items-baseline gap-1.5">
        {urgency === "critical" ? (
          <span aria-hidden="true" className="h-1.5 w-1.5 translate-y-px bg-critical-solid" />
        ) : null}
        <span
          className={`font-mono text-2xs ${URGENCY_TEXT[urgency]}`}
          title={check.state_since ? `Since ${formatTimestamp(check.state_since)}` : undefined}
        >
          {state ? STATE_WORDS[state] : "not judged yet"}
        </span>
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-ink">{check.name}</span>
        <span className="truncate font-mono text-2xs text-ink-dim">
          {KIND_DESCRIPTIONS[check.type].title}
          {/* A heartbeat is called, never run: the two words mean different things
              to whoever has to act on the row. */}
          {check.last_run_at
            ? ` · ${check.type === "heartbeat" ? "last judged" : "last run"} ${formatRelativeTime(check.last_run_at)}`
            : ""}
          {check.locations.length ? ` · ${check.locations.join(", ")}` : ""}
        </span>
      </span>
      <span className="w-20 text-right font-mono text-2xs text-ink">
        {reading ? formatUptime(reading) : missingReading}
      </span>
      <span className="hidden w-20 text-right font-mono text-2xs text-ink sm:block">
        {reading && reading.runs > 0 ? formatDuration(reading.p95_duration_ms) : missingReading}
      </span>
      <span className="hidden w-32 truncate text-right font-mono text-2xs lg:block">
        {reading?.tls ? (
          <span
            className={URGENCY_TEXT[certificateUrgency(reading.tls.days_remaining)]}
            title={`Issued by ${reading.tls.issuer}`}
          >
            {formatCertificateAge(reading.tls.days_remaining)}
          </span>
        ) : (
          <span className="text-ink-dim">—</span>
        )}
      </span>
      <span className={`hidden md:block ${TREND_COLUMN}`}>
        {/* A bucket nothing succeeded in has no duration, and the line breaks there. */}
        <Sparkline
          points={(trend ?? []).map((point) => point.mean_duration_ms)}
          max={ceiling}
          absent="gap"
          label={`${check.name} response time over the window`}
        />
      </span>
    </Link>
  );
}
