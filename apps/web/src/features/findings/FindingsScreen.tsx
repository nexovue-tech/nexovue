import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Sparkline } from "@/shared/components/Sparkline";
import { formatDuration } from "@/shared/format/duration";
import { formatCount, formatPercent, UNKNOWN } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import { formatRatePerMinute } from "@/shared/format/rate";
import { formatRelativeTime } from "@/shared/format/time";
import { useLiveWindow, useSelection } from "@/shared/selection";

import type { CheckCounts, DetectionStatus, Finding, ProjectTotals } from "./api";
import { useCheckCounts, useDetectionStatus, useFindings, useProjectSummary } from "./api";
import { CLOSED_AS } from "./finding-words";
import { detectionHeadlineFor, headlineFor } from "./headline";
import { orderFindings } from "./ordering";

/**
 * The feed of things that need you, written as sentences.
 *
 * **The healthy state is the one that matters.** F1 AC1.7 forbids a blank page and a
 * spinner here by name — when nothing is wrong, silence has to read as information
 * rather than as a page that failed.
 *
 * **AC18 is the single most dangerous requirement on this screen.** A quiet feed
 * and "we could not read the feed" must never look alike, because conflating them tells a
 * customer their system is healthy when the truth is that we cannot see it. The two
 * states share no copy, no icon and no layout, and a test asserts they differ.
 *
 * Detection's own line leads the feed (`headline.ts`), and a failed read of it is the
 * screen's error state like any other: a status we could not read must not leave the
 * checks' line standing alone as if nothing had been asked.
 *
 * Muted findings stay out of the feed. A quiet link under it opens them instead, because
 * the feed has no filters and inventing one for a single toggle would be a second
 * control to learn.
 */
export function FindingsScreen() {
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const [showingMuted, setShowingMuted] = useState(false);

  const feed = useFindings(projectId, showingMuted, timeWindow);
  const summary = useProjectSummary(projectId, timeWindow);
  // The checks read waits for the summary: four telemetry reads may be in flight and
  // the fifth is refused rather than queued.
  const checks = useCheckCounts(projectId, timeWindow, summary.data !== undefined);
  const detection = useDetectionStatus(projectId, timeWindow);

  return (
    <FindingsView
      findings={feed.data?.findings ?? []}
      mutedCount={feed.data?.muted_count ?? 0}
      showingMuted={showingMuted}
      onToggleMuted={() => setShowingMuted((showing) => !showing)}
      isPending={feed.isPending || summary.isPending || detection.isPending}
      isError={feed.isError || summary.isError || detection.isError}
      error={feed.error ?? summary.error ?? detection.error}
      onRetry={() => {
        void feed.refetch();
        void summary.refetch();
        void detection.refetch();
      }}
      totals={summary.data}
      checks={checks.data}
      detection={detection.data}
    />
  );
}

export interface FindingsViewProps {
  findings: readonly Finding[];
  /** How many findings are muted, and so left out of the feed. */
  mutedCount: number;
  /** True when the list holds the muted findings instead of the feed. */
  showingMuted: boolean;
  onToggleMuted: () => void;
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  totals?: ProjectTotals;
  checks?: CheckCounts;
  detection?: DetectionStatus;
  /** The IANA zone times are read on: the browser's until the project names its own. */
  zone?: string;
}

export function FindingsView({
  findings,
  mutedCount,
  showingMuted,
  onToggleMuted,
  isPending,
  isError,
  error,
  onRetry,
  totals,
  checks,
  detection,
  zone,
}: FindingsViewProps) {
  const ordered = orderFindings(findings);
  const detectionLine = detectionHeadlineFor(detection, { zone });

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
      ) : (
        <>
          {/* Above everything in the feed, findings included: "detection is not
              running" matters most when the list looks quiet, and still matters when
              it does not. The muted list says nothing about the system, so not there. */}
          {detectionLine && !showingMuted ? (
            <section className="shrink-0 border-line border-t px-4 py-4">
              <h2
                className={`font-medium font-serif text-lg tracking-tight ${
                  detectionLine.wrong ? "text-critical-text" : "text-ink"
                }`}
              >
                {detectionLine.title}
              </h2>
            </section>
          ) : null}
          {ordered.length > 0 ? (
            <ul className="shrink-0 border-line border-t">
              {ordered.map((found) => (
                <li key={found.id}>
                  <FindingRow finding={found} />
                </li>
              ))}
            </ul>
          ) : showingMuted ? (
            // Not the summary: an empty muted list says nothing about the system.
            <p className="shrink-0 border-line border-t px-4 py-6 text-ink-dim text-sm">
              No finding is muted.
            </p>
          ) : (
            <SummaryState totals={totals} checks={checks} />
          )}
          <MutedLink count={mutedCount} showing={showingMuted} onToggle={onToggleMuted} />
        </>
      )}
    </div>
  );
}

/**
 * When the feed is empty, the RED summary is the content.
 *
 * Named for what it is rather than for what it says: it used to be `HealthyState`,
 * which was the assumption behind the defect — an empty feed was taken for a
 * healthy system, and the headline said so over evidence that disagreed.
 *
 * The sentence reports only what the checks said (`headline.ts`), and it never speaks for
 * the whole system. The numbers beneath it are there to be read, because nothing else
 * reads them yet.
 */
function SummaryState({ totals, checks }: { totals?: ProjectTotals; checks?: CheckCounts }) {
  const headline = headlineFor(totals, checks);

  return (
    <section className="shrink-0 border-line border-t px-4 py-6">
      <h2
        className={`font-medium font-serif text-lg tracking-tight ${
          headline.wrong ? "text-critical-text" : "text-ink"
        }`}
      >
        {headline.title}
      </h2>

      <p className="mt-2 text-ink-dim text-sm">
        {totals ? (
          <>
            {formatCount(totals.service_count)} {plural(totals.service_count, "service")} reporting
            {checks && checks.total > 0
              ? ` · ${formatCount(checks.passing)} of ${formatCount(checks.total)} ${plural(checks.total, "synthetic check")} passing`
              : ""}
            {headline.wrong ? ". Synthetics has the failing ones." : ""}
          </>
        ) : (
          "Send a span, a log or a metric and this fills in."
        )}
      </p>

      {/* Overview's own summary. Nothing assesses these yet, so the reader has to. */}
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
 * The way to the muted findings, and back.
 *
 * Quiet on purpose: a muted finding is one somebody already decided to stop hearing
 * about, so it earns a link under the feed, not a place in it.
 */
function MutedLink({
  count,
  showing,
  onToggle,
}: {
  count: number;
  showing: boolean;
  onToggle: () => void;
}) {
  if (!showing && count === 0) return null;

  return (
    <div className="shrink-0 px-4 py-3">
      <button
        type="button"
        onClick={onToggle}
        className="text-brand-text text-xs underline-offset-2 hover:underline"
      >
        {showing ? "Back to the feed" : `Muted (${formatCount(count)})`}
      </button>
    </div>
  );
}

/** The card's sentence, which opens the finding's own page (D12). */
function FindingLink({ finding }: { finding: Finding }) {
  return (
    <Link
      to="/findings/$findingId"
      params={{ findingId: finding.id }}
      className="underline-offset-2 hover:underline"
    >
      {finding.title}
    </Link>
  );
}

/** What a card is about. A project finding has no single service: it spans several. */
function subjectOf(finding: Finding): string {
  return finding.service_name ?? "This project";
}

/**
 * One finding.
 *
 * What is committed is the shape: a sentence leading, the entity under it, the evidence
 * inline. *Probable cause* stays off the card until Stage 2 can name one, and the v1
 * engine reports no impact figure, so neither has a line here.
 *
 * A closed finding is one quiet line naming how it closed: no sparkline, nothing to do.
 */
function FindingRow({ finding }: { finding: Finding }) {
  if (finding.state === "closed") {
    return (
      <div className="border-line-subtle border-b px-4 py-2 text-sm">
        <p className="truncate text-ink-dim">
          <FindingLink finding={finding} />
        </p>
        <p className="mt-0.5 text-2xs text-ink-dim">
          {finding.closed_reason ? CLOSED_AS[finding.closed_reason] : "Closed"}
          {" · "}
          <span className="font-mono">
            {finding.closed_at ? formatRelativeTime(finding.closed_at) : UNKNOWN}
          </span>
        </p>
      </div>
    );
  }

  const subject = subjectOf(finding);

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
        <h3 className="text-ink text-sm">
          <FindingLink finding={finding} />
        </h3>
        <p className="mt-0.5 font-mono text-2xs text-ink-dim">{subject}</p>
      </div>

      <div className="flex shrink-0 items-start gap-3">
        <Sparkline
          points={finding.trend}
          max={Math.max(...finding.trend, 1)}
          tone={finding.severity === "critical" ? "critical" : "series"}
          label={`How ${subject} has been trending.`}
        />
        <span className="font-mono text-2xs text-ink-dim">
          {finding.state === "acknowledged" ? "Acked" : formatRelativeTime(finding.started_at)}
        </span>
      </div>
    </article>
  );
}
