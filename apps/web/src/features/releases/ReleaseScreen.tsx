import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useState } from "react";

import { CopyButton } from "@/shared/components/CopyButton";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Tabs } from "@/shared/components/Tabs";
import { useDetectionSettings } from "@/shared/detection/api";
import { shortVersion } from "@/shared/format/version";
import { formatZonedTime } from "@/shared/format/zoned";

import type { Block, Period, Release, WindowHours } from "./api";
import { useComparison, useRelease, WINDOW_HOURS } from "./api";
import {
  BELOW_THE_NUMBERS,
  findingWindowSentence,
  measureLines,
  NO_SAMPLING,
  openingSentence,
  readingTone,
  WARM_UP_RULE,
  windowForFinding,
  windowWords,
} from "./comparison";
import { activityWords, basisWords, stateWords } from "./release-words";
import type { ReleaseSearch } from "./search";
import { parseReleaseSearch } from "./search";

const BLOCK_TITLES: Record<Block["kind"], string> = {
  rollout: "During the rollout",
  after_rollout: "Since the rollout finished",
  around_deploy: "Before and after the deploy",
};
const WINDOW_TABS = WINDOW_HOURS.map((hours) => ({
  id: String(hours),
  label: `${hours} ${hours === 1 ? "hour" : "hours"}`,
}));

/**
 * `/releases/$releaseId` (F28 design): the sentence first, from the comparison, then the
 * facts. Each section loads and fails on its own, so a failed comparison leaves the facts
 * readable (AC68). Times are on the project's clock, named.
 */
export function ReleaseScreen() {
  const { releaseId } = useParams({ strict: false }) as { releaseId: string };
  const search = parseReleaseSearch(useSearch({ strict: false }));
  const release = useRelease(releaseId);
  const zone = useDetectionSettings(release.data?.project_id).data?.timezone;

  return (
    <div className="flex flex-col gap-6 px-4 pt-5 pb-10">
      <Link to="/releases" className="text-ink-dim text-sm hover:text-ink">
        Releases
      </Link>
      {release.isPending ? (
        <SkeletonRows rows={4} label="Loading the release" />
      ) : release.isError ? (
        <InlineFailure
          title="This release could not be loaded"
          error={release.error}
          onRetry={() => release.refetch()}
        />
      ) : (
        <ReleasePage release={release.data} search={search} zone={zone} />
      )}
    </div>
  );
}

function ReleasePage({
  release,
  search,
  zone,
}: {
  release: Release;
  search: ReleaseSearch;
  zone: string | undefined;
}) {
  const period = release.periods.find((each) => each.id === search.period) ?? release.periods[0];
  return (
    <>
      {search.finding !== undefined ? (
        <Link
          to="/findings/$findingId"
          params={{ findingId: search.finding }}
          className="text-ink-dim text-sm hover:text-ink"
        >
          Back to the finding
        </Link>
      ) : null}
      <header className="flex flex-col gap-1">
        <h1 className="break-words font-semibold font-serif text-ink text-xl tracking-tight">
          {release.service_name}{" "}
          <span className="font-mono" title={release.version}>
            {shortVersion(release.version)}
          </span>
        </h1>
      </header>
      {period ? (
        <ComparisonSection
          release={release}
          period={period}
          findingAt={search.finding_at}
          zone={zone}
        />
      ) : null}
      <Facts release={release} period={period} zone={zone} />
    </>
  );
}

function ComparisonSection({
  release,
  period,
  findingAt,
  zone,
}: {
  release: Release;
  period: Period;
  /** The start of the finding the page was opened from (FR22). */
  findingAt: string | undefined;
  zone: string | undefined;
}) {
  // Opened from a finding that started after this period, the window reaches it.
  const fromFinding =
    findingAt !== undefined && Date.parse(findingAt) >= Date.parse(period.started_at)
      ? windowForFinding(period.started_at, findingAt)
      : undefined;
  const [windowHours, setWindowHours] = useState<WindowHours>(
    fromFinding?.hours ?? WINDOW_HOURS[0],
  );
  const comparison = useComparison(release.id, period.id, windowHours);
  const now = Date.now();
  const windowSentence =
    fromFinding !== undefined && findingAt !== undefined && windowHours === fromFinding.hours
      ? findingWindowSentence(fromFinding, formatZonedTime(findingAt, { zone, now }))
      : windowWords(windowHours);

  return (
    <section aria-label="Comparison" className="flex flex-col gap-3">
      {comparison.isPending ? <SkeletonRows rows={3} label="Reading the comparison" /> : null}
      {comparison.isError ? (
        <InlineFailure
          title="The comparison could not be read"
          error={comparison.error}
          onRetry={() => comparison.refetch()}
        />
      ) : null}
      {comparison.data ? (
        <>
          <p role="status" className="max-w-prose text-base text-ink">
            {openingSentence(comparison.data, { startedAt: period.started_at, now })}
          </p>
          <p className="max-w-prose text-ink-dim text-sm">{BELOW_THE_NUMBERS}</p>
          {comparison.data.as_of ? (
            <p className="text-ink-dim text-sm">
              As of {formatZonedTime(comparison.data.as_of, { zone, now })}
            </p>
          ) : null}
        </>
      ) : null}
      <p className="max-w-prose text-ink-dim text-sm">{windowSentence}</p>
      <Tabs
        label="Window"
        items={WINDOW_TABS}
        selected={String(windowHours)}
        onSelect={(id) => setWindowHours(Number(id) as WindowHours)}
      >
        {comparison.data && comparison.data.blocks.length > 0 ? (
          <div className="flex flex-col gap-5 pt-3">
            {comparison.data.blocks.map((block) => (
              <BlockTable key={block.kind} block={block} zone={zone} now={now} />
            ))}
            <p className="max-w-prose text-ink-dim text-sm">
              {NO_SAMPLING} {WARM_UP_RULE}
            </p>
          </div>
        ) : null}
      </Tabs>
    </section>
  );
}

function BlockTable({ block, zone, now }: { block: Block; zone: string | undefined; now: number }) {
  const lines = measureLines(block);
  return (
    <div className="flex flex-col gap-2">
      <h2 className="font-medium text-ink text-sm">
        {BLOCK_TITLES[block.kind]}{" "}
        <span className="font-normal text-ink-dim">
          {formatZonedTime(block.from, { zone, now })} to {formatZonedTime(block.to, { zone, now })}
        </span>
      </h2>
      {/* Phone: one stacked line per measure, so nothing scrolls sideways (AC75). */}
      <ul className="flex flex-col gap-1 text-sm md:hidden">
        {lines.map((line) => (
          <li key={line.label}>
            <span className="text-ink">{line.label}</span>
            {line.word ? <span className={readingTone(line.reading)}>: {line.word}</span> : null}.{" "}
            <span className="font-mono text-ink-dim">{line.sides.join(", ")}</span>
          </li>
        ))}
      </ul>
      <table className="hidden text-sm md:table">
        <thead>
          <tr className="text-left text-ink-dim">
            <th className="py-1 pr-6 font-normal">Measure</th>
            <th className="py-1 pr-6 font-normal">This version</th>
            <th className="py-1 pr-6 font-normal">Previous version</th>
            <th className="py-1 font-normal">Reading</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.label} className="border-line border-t">
              <td className="py-1 pr-6 text-ink">{line.label}</td>
              <td className="py-1 pr-6 font-mono text-ink">{line.sides[0]}</td>
              <td className="py-1 pr-6 font-mono text-ink">{line.sides[1]}</td>
              <td className={`py-1 ${readingTone(line.reading)}`}>{line.word}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Facts({
  release,
  period,
  zone,
}: {
  release: Release;
  period: Period | undefined;
  zone: string | undefined;
}) {
  const navigate = useNavigate();
  const now = Date.now();
  const told = stateWords(release.last_state);
  return (
    <section aria-label="Facts" className="flex flex-col gap-3">
      <h2 className="font-medium text-ink text-sm">Facts</h2>
      <dl className="grid gap-x-6 gap-y-1 text-sm md:grid-cols-[max-content_1fr]">
        <dt className="text-ink-dim">Service</dt>
        <dd className="break-words text-ink">{release.service_name}</dd>
        <dt className="text-ink-dim">Version</dt>
        <dd className="flex flex-wrap items-baseline gap-2 break-words font-mono text-ink">
          <span className="min-w-0 break-all">{release.version}</span>
          <CopyButton value={release.version} label="Copy the version" />
        </dd>
        <dt className="text-ink-dim">Previous version</dt>
        <dd className="break-words font-mono text-ink">{period?.previous_version ?? "—"}</dd>
        <dt className="text-ink-dim">Started</dt>
        <dd className="text-ink">
          {period ? formatZonedTime(period.started_at, { zone, now }) : "—"}
        </dd>
        <dt className="text-ink-dim">Source</dt>
        <dd className="text-ink">{basisWords(release.basis)}</dd>
        <dt className="text-ink-dim">Activity</dt>
        <dd className="text-ink">
          {activityWords(release.activity, {
            startedAt: period?.started_at ?? release.first_reported_at ?? "",
            lastSeenAt: release.last_seen_at,
            replacedBy: release.replaced_by,
            zone,
            now,
          })}
          {told ? <span className="text-ink-dim"> · {told}</span> : null}
        </dd>
      </dl>
      {release.periods.length > 0 ? (
        <div className="flex flex-col gap-1">
          <h3 className="text-ink-dim text-sm">Periods in service</h3>
          <ul className="flex flex-col gap-1 text-sm">
            {release.periods.map((each) => (
              <li key={each.id}>
                <button
                  type="button"
                  className={`text-left ${each.id === period?.id ? "text-ink" : "text-brand-text hover:underline"}`}
                  onClick={() =>
                    void navigate({
                      to: "/releases/$releaseId",
                      params: { releaseId: release.id },
                      search: { period: each.id },
                    })
                  }
                >
                  {periodWords(each, zone, now)}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function periodWords(period: Period, zone: string | undefined, now: number): string {
  const from = formatZonedTime(period.started_at, { zone, now });
  const to = period.ended_at === null ? "now" : formatZonedTime(period.ended_at, { zone, now });
  const what = period.already_running
    ? "Running when we started counting"
    : period.is_return
      ? "Came back"
      : "Started";
  return `${what}: ${from} to ${to}`;
}
