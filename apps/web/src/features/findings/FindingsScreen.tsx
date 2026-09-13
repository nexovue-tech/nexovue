import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Sparkline } from "@/shared/components/Sparkline";
import { formatDuration } from "@/shared/format/duration";
import { formatCount, formatPercent, UNKNOWN } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import { formatRatePerMinute } from "@/shared/format/rate";
import { formatRelativeTime } from "@/shared/format/time";
import { useLiveWindow, useSelection } from "@/shared/selection";

import type { Finding, ProjectTotals } from "./api";
import { useCheckCounts, useFindings, useProjectSummary } from "./api";
import { orderFindings } from "./ordering";

/**
 * The feed of things that need you, written as sentences.
 *
 * **The healthy state is the one that matters**, and today it is the whole screen: no
 * engine produces findings yet, which is exactly why Overview holds `/` and this does
 * not. F1 AC1.7 forbids a blank page and a spinner here by name — when nothing is wrong,
 * silence has to read as information rather than as a page that failed.
 *
 * **AC18 is the single most dangerous requirement on this screen.** "All systems normal"
 * and "we could not read the feed" must never look alike, because conflating them tells a
 * customer their system is healthy when the truth is that we cannot see it. The two
 * states share no copy, no icon and no layout, and a test asserts they differ.
 *
 * The feed's structure, ordering and actions are committed (OD1). The card's *contents*
 * are provisional and marked so, to keep a drawing from becoming the specification for an
 * insight engine nobody has designed.
 */
export function FindingsScreen() {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useLiveWindow(rangeId);

  const feed = useFindings(projectId, timeWindow);
  const summary = useProjectSummary(projectId, timeWindow);
  // The checks read waits for the summary: four telemetry reads may be in flight and
  // the fifth is refused rather than queued.
  const checks = useCheckCounts(projectId, timeWindow, summary.data !== undefined);

  return (
    <FindingsView
      findings={feed.findings}
      isPending={feed.isPending || summary.isPending}
      isError={feed.isError || summary.isError}
      error={summary.error}
      onRetry={() => void summary.refetch()}
      totals={summary.data}
      checks={checks.data}
    />
  );
}

export interface FindingsViewProps {
  findings: readonly Finding[];
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  totals?: ProjectTotals;
  checks?: { total: number; passing: number };
}

export function FindingsView({
  findings,
  isPending,
  isError,
  error,
  onRetry,
  totals,
  checks,
}: FindingsViewProps) {
  const ordered = orderFindings(findings);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Findings</h1>
        <p className="mt-1 text-ink-dim text-sm">
          Things that need you, newest first. Anything still wrong comes before anything already
          fixed.
        </p>
      </header>

      {isPending ? (
        // Statements, not rows: the feed is sentences, and a skeleton shaped like a
        // table would promise the wrong screen.
        <div className="px-4">
          <SkeletonRows rows={3} label="Loading findings" />
        </div>
      ) : isError ? (
        <div className="px-4">
          {/* AC18. Nothing here may read as reassurance: the feed could not be read,
              and saying anything about the system's health would be a claim we cannot
              support. */}
          <InlineFailure title="The findings could not be loaded" error={error} onRetry={onRetry} />
          {/* Said in the screen's own words, because `InlineFailure` builds its
              detail from the error and cannot carry this. It is the sentence AC18
              exists for: the absence of findings here means we could not look, and
              a reader must not take it for good news. */}
          <p className="mt-3 text-ink-dim text-sm">
            This is not the same as nothing being wrong. We could not read the feed, so we cannot
            tell you either way.
          </p>
        </div>
      ) : ordered.length === 0 ? (
        <HealthyState totals={totals} checks={checks} />
      ) : (
        <ul className="shrink-0 border-line border-t">
          {ordered.map((found) => (
            <li key={found.id}>
              <FindingRow finding={found} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * When nothing is wrong, the RED summary is the content.
 *
 * A page that says only "all systems normal" gives a reader no way to confirm it, which
 * is how a reassuring screen becomes one nobody trusts. The numbers are the evidence for
 * the sentence above them.
 */
function HealthyState({
  totals,
  checks,
}: {
  totals?: ProjectTotals;
  checks?: { total: number; passing: number };
}) {
  return (
    <section className="shrink-0 border-line border-t px-4 py-6">
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">All systems normal</h2>

      <p className="mt-2 text-ink-dim text-sm">
        {totals ? (
          <>
            {formatCount(totals.service_count)} {plural(totals.service_count, "service")} reporting
            {checks && checks.total > 0
              ? ` · ${formatCount(checks.passing)} of ${formatCount(checks.total)} ${plural(checks.total, "synthetic check")} passing`
              : ""}
          </>
        ) : (
          "Nothing has reported to this project yet."
        )}
      </p>

      {/* The evidence. Overview's own summary, carried here so "normal" is checkable. */}
      {totals ? (
        <dl className="mt-5 flex flex-wrap gap-x-8 gap-y-2 border-line-subtle border-t pt-4">
          <Reading term="Requests/min" value={formatRatePerMinute(totals.requests_per_minute)} />
          <Reading
            term="Errors"
            value={formatPercent(totals.error_rate)}
            critical={totals.error_count > 0}
          />
          <Reading term="p95" value={formatDuration(totals.p95_ms)} />
        </dl>
      ) : null}
    </section>
  );
}

function Reading({
  term,
  value,
  critical = false,
}: {
  term: string;
  value: string;
  critical?: boolean;
}) {
  return (
    <div>
      <dt className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{term}</dt>
      <dd className={`mt-0.5 font-mono text-base ${critical ? "text-critical-text" : "text-ink"}`}>
        {value}
      </dd>
    </div>
  );
}

/**
 * One finding.
 *
 * **The card's contents are provisional** — impact, probable cause and their wording
 * belong to an engine that does not exist. What is committed is the shape: a sentence
 * leading, the entity under it, the evidence inline, and the two actions on the right.
 *
 * A resolved finding is one quiet line: no sparkline, no actions, nothing to do.
 */
function FindingRow({ finding }: { finding: Finding }) {
  if (finding.state === "resolved") {
    return (
      <div className="flex items-baseline gap-3 border-line-subtle border-b px-4 py-2 text-sm">
        <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Resolved</span>
        <span className="min-w-0 flex-1 truncate text-ink-dim">{finding.title}</span>
        <span className="shrink-0 font-mono text-2xs text-ink-dim">
          {finding.resolved_at ? formatRelativeTime(finding.resolved_at) : UNKNOWN}
        </span>
      </div>
    );
  }

  return (
    <article className="flex gap-3 border-line-subtle border-b px-4 py-3">
      <span className="flex w-2 shrink-0 justify-center pt-1.5">
        <span
          aria-hidden="true"
          className={`size-1.5 rounded-full ${
            finding.severity === "critical" ? "bg-critical-solid" : "bg-warning-solid"
          }`}
        />
      </span>

      <div className="min-w-0 flex-1">
        {/* The title is the product: a sentence, not a metric expression. */}
        <h3 className="text-ink text-sm">{finding.title}</h3>
        <p className="mt-0.5 font-mono text-2xs text-ink-dim">{finding.subject}</p>
        {finding.impact ? <p className="mt-1 text-ink-dim text-xs">{finding.impact}</p> : null}
        {finding.probable_cause ? (
          <p className="mt-1 text-ink-dim text-xs">Probably: {finding.probable_cause}</p>
        ) : null}
      </div>

      <div className="flex shrink-0 items-start gap-3">
        <Sparkline
          points={finding.trend}
          max={Math.max(...finding.trend, 1)}
          tone={finding.severity === "critical" ? "critical" : "series"}
          label={`How ${finding.subject} has been trending.`}
        />
        <span className="font-mono text-2xs text-ink-dim">
          {finding.state === "acknowledged" ? "Acked" : formatRelativeTime(finding.started_at)}
        </span>
      </div>
    </article>
  );
}
