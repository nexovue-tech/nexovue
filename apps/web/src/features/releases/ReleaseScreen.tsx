import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useState } from "react";

import { isNotFound } from "@/shared/api-client";
import { CopyButton } from "@/shared/components/CopyButton";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Tabs } from "@/shared/components/Tabs";
import { TrendChart } from "@/shared/components/TrendChart";
import { useDetectionSettings } from "@/shared/detection/api";
import { formatPercent } from "@/shared/format/number";
import { shortVersion } from "@/shared/format/version";
import { formatZonedTime } from "@/shared/format/zoned";

import type { Block, Comparison, DeployFact, Period, Release, WindowHours } from "./api";
import {
  useComparison,
  useRelease,
  useRollout,
  useSenderDetails,
  useVersionErrors,
  WINDOW_HOURS,
} from "./api";
import {
  BELOW_THE_NUMBERS,
  findingWindowSentence,
  measureLines,
  NO_SAMPLING,
  openingSentence,
  readingTone,
  readsAboutTheSame,
  WARM_UP_RULE,
  WHOLE_SERVICE_CAUTION,
  warningSentences,
  windowWords,
} from "./comparison";
import { activityWords, basisWords, stateWords } from "./release-words";
import { latestShares, rolloutDescription, seriesLabel, shareRows } from "./rollout";
import type { ReleaseSearch } from "./search";
import { parseReleaseSearch } from "./search";
import {
  byKind,
  ERRORS_CAUTION,
  ERRORS_MAY_BE_INCOMPLETE,
  errorsAbsence,
  FROM_THE_BROWSER,
  kindTitle,
  occurrenceWords,
} from "./version-errors";

const BLOCK_TITLES: Record<Block["kind"], string> = {
  rollout: "During the rollout",
  after_rollout: "Since the rollout finished",
  around_deploy: "Before and after the deploy",
};
const WINDOW_TABS = WINDOW_HOURS.map((hours) => ({
  id: String(hours),
  label: `${hours} ${hours === 1 ? "hour" : "hours"}`,
}));
const ERRORS_ANCHOR = "errors-by-version";
/** A sender's time this far from our arrival time is shown beside it (spec, error cases). */
const CLAIM_GAP_SHOWN_MS = 15 * 60_000;
const SECTION_HEADING = "font-medium text-ink text-sm";
const LINK = "text-brand-text hover:underline";

/**
 * `/releases/$releaseId` (F28 design): the sentence first, from the comparison, then the
 * warnings, the comparison, the rollout share, errors by version, findings since, and the
 * facts. Each section loads and fails on its own, so a failed comparison leaves the facts
 * readable (AC68). Times are on the project's clock, named. Sender text is plain text, and
 * its link carries no referrer (AC43, AC44).
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
        isNotFound(release.error) ? (
          <EmptyState
            title="No such release"
            detail="It may have aged out, or it belongs to another organization or project."
          />
        ) : (
          <InlineFailure
            title="This release could not be loaded"
            error={release.error}
            onRetry={() => release.refetch()}
          />
        )
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
  // Unset until a tab is chosen: the API then chooses, from the finding if there is one.
  const [chosen, setChosen] = useState<WindowHours | undefined>(undefined);
  const comparison = useComparison(release.id, period?.id, chosen, search.finding);
  const windowHours = chosen ?? comparison.data?.window_hours;
  const now = Date.now();
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
        <>
          <ComparisonSection
            release={release}
            period={period}
            comparison={comparison}
            windowHours={windowHours}
            onWindow={setChosen}
            zone={zone}
            now={now}
          />
          {windowHours !== undefined ? (
            <>
              <RolloutSection
                release={release}
                period={period}
                windowHours={windowHours}
                zone={zone}
              />
              <ErrorsSection
                release={release}
                period={period}
                windowHours={windowHours}
                zone={zone}
                now={now}
              />
            </>
          ) : null}
          <FindingsSince period={period} zone={zone} now={now} />
        </>
      ) : null}
      <Facts release={release} period={period} zone={zone} now={now} />
      <SenderSection releaseId={release.id} zone={zone} now={now} />
    </>
  );
}

function ComparisonSection({
  release,
  period,
  comparison,
  windowHours,
  onWindow,
  zone,
  now,
}: {
  release: Release;
  period: Period;
  comparison: ReturnType<typeof useComparison>;
  windowHours: number | undefined;
  onWindow: (hours: WindowHours) => void;
  zone: string | undefined;
  now: number;
}) {
  const data = comparison.data;
  const windowSentence =
    data === undefined
      ? null
      : data.finding !== null
        ? findingWindowSentence(
            data.window_hours,
            data.finding.reaches,
            formatZonedTime(data.finding.started_at, { zone, now }),
          )
        : windowWords(data.window_hours);
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
      {data ? (
        <>
          <p role="status" className="max-w-prose text-base text-ink">
            {openingSentence(data, { startedAt: period.started_at, now })}
          </p>
          <p className="max-w-prose text-ink-dim text-sm">{BELOW_THE_NUMBERS}</p>
          {data.as_of ? (
            <p className="text-ink-dim text-sm">
              As of {formatZonedTime(data.as_of, { zone, now })}
            </p>
          ) : null}
          <Warnings comparison={data} />
          {windowSentence ? (
            <p className="max-w-prose text-ink-dim text-sm">{windowSentence}</p>
          ) : null}
        </>
      ) : null}
      <Tabs
        label="Window"
        items={WINDOW_TABS}
        selected={String(windowHours ?? WINDOW_HOURS[0])}
        onSelect={(id) => onWindow(Number(id) as WindowHours)}
      >
        {data && data.blocks.length > 0 ? (
          <div className="flex flex-col gap-5 pt-3">
            {data.blocks.map((block) => (
              <BlockTable
                key={block.kind}
                block={block}
                serviceName={release.service_name}
                zone={zone}
                now={now}
              />
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

function Warnings({ comparison }: { comparison: Comparison }) {
  const warnings = warningSentences(comparison);
  if (warnings.length === 0) return null;
  return (
    <ul aria-label="Warnings" className="flex max-w-prose flex-col gap-1 text-sm text-warning-text">
      {warnings.map((warning) => (
        <li key={warning}>{warning}</li>
      ))}
    </ul>
  );
}

function BlockTable({
  block,
  serviceName,
  zone,
  now,
}: {
  block: Block;
  serviceName: string;
  zone: string | undefined;
  now: number;
}) {
  const lines = measureLines(block);
  const withLastWeek = block.last_week !== null;
  return (
    <div className="flex flex-col gap-2">
      <h2 className={SECTION_HEADING}>
        {BLOCK_TITLES[block.kind]}{" "}
        <span className="font-normal text-ink-dim">
          {formatZonedTime(block.from, { zone, now })} to {formatZonedTime(block.to, { zone, now })}
        </span>
      </h2>
      {/* Phone: one stacked line per measure, last week on its own line (AC75). */}
      <ul className="flex flex-col gap-1 text-sm md:hidden">
        {lines.map((line) => (
          <li key={line.label}>
            <span className="text-ink">{line.label}</span>
            {line.word ? <span className={readingTone(line.reading)}>: {line.word}</span> : null}.{" "}
            <span className="break-words font-mono text-ink-dim">{line.sides.join(", ")}</span>
            {line.lastWeek !== null ? (
              <span className="block text-ink-dim">Last week: {line.lastWeek}</span>
            ) : null}
          </li>
        ))}
      </ul>
      <table className="hidden text-sm md:table">
        <thead>
          <tr className="text-left text-ink-dim">
            <th className="py-1 pr-6 font-normal">Measure</th>
            <th className="py-1 pr-6 font-normal">This version</th>
            <th className="py-1 pr-6 font-normal">Previous version</th>
            {withLastWeek ? <th className="py-1 pr-6 font-normal">Last week</th> : null}
            <th className="py-1 font-normal">Reading</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.label} className="border-line border-t">
              <td className="py-1 pr-6 text-ink">{line.label}</td>
              <td className="py-1 pr-6 font-mono text-ink">{line.sides[0]}</td>
              <td className="py-1 pr-6 font-mono text-ink">{line.sides[1]}</td>
              {withLastWeek ? (
                <td className="py-1 pr-6 font-mono text-ink-dim">{line.lastWeek ?? "—"}</td>
              ) : null}
              <td className={`py-1 ${readingTone(line.reading)}`}>{line.word}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {readsAboutTheSame(block) ? (
        <p className="max-w-prose text-ink-dim text-sm">
          {WHOLE_SERVICE_CAUTION}{" "}
          <a href={`#${ERRORS_ANCHOR}`} className={LINK}>
            Errors by version
          </a>
          {" · "}
          <Link
            to="/services/$name"
            params={{ name: serviceName }}
            search={{ from: block.from, to: block.to }}
            className={LINK}
          >
            This service's endpoints over the same minutes
          </Link>
        </p>
      ) : null}
    </div>
  );
}

function RolloutSection({
  release,
  period,
  windowHours,
  zone,
}: {
  release: Release;
  period: Period;
  windowHours: number;
  zone: string | undefined;
}) {
  const rollout = useRollout(release.id, period.id, windowHours);
  return (
    <section aria-label="Rollout" className="flex flex-col gap-3">
      <h2 className={SECTION_HEADING}>Share of traffic by version</h2>
      {rollout.isPending ? <SkeletonRows rows={2} label="Reading the rollout" /> : null}
      {rollout.isError ? (
        <InlineFailure
          title="The rollout could not be read"
          error={rollout.error}
          onRetry={() => rollout.refetch()}
        />
      ) : null}
      {rollout.data ? (
        rollout.data.minutes.length === 0 ? (
          <p className="max-w-prose text-ink-dim text-sm">{rolloutDescription(rollout.data)}</p>
        ) : (
          <>
            <TrendChart
              label={rollout.data.series.map(seriesLabel)}
              seriesToken={1}
              distinctColours
              data={shareRows(rollout.data)}
              formatValue={(value) => formatPercent(value)}
              description={rolloutDescription(rollout.data)}
              zone={zone}
              yRange="fit"
            />
            <table className="text-sm">
              <caption className="text-left text-ink-dim">Share in the latest minute</caption>
              <tbody>
                {latestShares(rollout.data).map((row) => (
                  <tr key={row.label} className="border-line border-t">
                    <td className="break-all py-1 pr-6 font-mono text-ink">{row.label}</td>
                    <td className="py-1 text-ink">{row.share}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )
      ) : null}
    </section>
  );
}

function ErrorsSection({
  release,
  period,
  windowHours,
  zone,
  now,
}: {
  release: Release;
  period: Period;
  windowHours: number;
  zone: string | undefined;
  now: number;
}) {
  const errors = useVersionErrors(release.id, period.id, windowHours);
  const data = errors.data;
  const absence =
    data === undefined
      ? null
      : errorsAbsence(
          data,
          data.aged_out_on === null ? null : formatZonedTime(data.aged_out_on, { zone, now }),
        );
  // A group gone here happened before the start: the link reaches back as far.
  const linkFrom =
    data === undefined
      ? undefined
      : new Date(2 * Date.parse(data.from) - Date.parse(data.to)).toISOString();
  return (
    <section id={ERRORS_ANCHOR} aria-label="Errors by version" className="flex flex-col gap-3">
      <h2 className={SECTION_HEADING}>Errors by version</h2>
      {errors.isPending ? <SkeletonRows rows={3} label="Reading errors by version" /> : null}
      {errors.isError ? (
        <InlineFailure
          title="Errors by version could not be read"
          error={errors.error}
          onRetry={() => errors.refetch()}
        />
      ) : null}
      {data ? (
        <>
          <p className="max-w-prose text-ink-dim text-sm">{ERRORS_CAUTION}</p>
          {data.may_be_incomplete ? (
            <p className="max-w-prose text-sm text-warning-text">{ERRORS_MAY_BE_INCOMPLETE}</p>
          ) : null}
          {absence !== null ? (
            <p className="max-w-prose text-ink text-sm">{absence}</p>
          ) : (
            byKind(data.groups).map(([kind, groups]) => (
              <div key={kind} className="flex flex-col gap-1">
                <h3 className="text-ink-dim text-sm">{kindTitle(kind)}</h3>
                <ul className="flex flex-col gap-1 text-sm">
                  {groups.map((group) => (
                    <li key={group.group_id} className="min-w-0 break-words">
                      <Link
                        to="/errors"
                        search={{ from: linkFrom ?? data.from, to: data.to, group: group.group_id }}
                        className={LINK}
                      >
                        {group.exception_type}
                      </Link>{" "}
                      <span className="text-ink">{group.message_template}</span>{" "}
                      <span className="text-ink-dim">
                        · {occurrenceWords(group)}
                        {group.from_browser ? ` · ${FROM_THE_BROWSER}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}
          {data.truncated ? (
            <p className="text-ink-dim text-sm">Showing the 100 most frequent groups.</p>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function FindingsSince({
  period,
  zone,
  now,
}: {
  period: Period;
  zone: string | undefined;
  now: number;
}) {
  return (
    <section aria-label="Findings since" className="flex flex-col gap-2">
      <h2 className={SECTION_HEADING}>Started after this release</h2>
      {period.later_findings.length === 0 ? (
        <p className="text-ink-dim text-sm">
          No finding on this service started while this version served.
        </p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          {period.later_findings.map((finding) => (
            <li key={finding.id} className="min-w-0 break-words">
              <Link to="/findings/$findingId" params={{ findingId: finding.id }} className={LINK}>
                {finding.title}
              </Link>{" "}
              <span className="text-ink-dim">
                · started {formatZonedTime(finding.started_at, { zone, now })}
              </span>
            </li>
          ))}
        </ul>
      )}
      {period.later_findings_truncated ? (
        <p className="text-ink-dim text-sm">Showing the first 50.</p>
      ) : null}
    </section>
  );
}

function Facts({
  release,
  period,
  zone,
  now,
}: {
  release: Release;
  period: Period | undefined;
  zone: string | undefined;
  now: number;
}) {
  const navigate = useNavigate();
  const told = stateWords(release.last_state);
  return (
    <section aria-label="Facts" className="flex flex-col gap-3">
      <h2 className={SECTION_HEADING}>Facts</h2>
      <CiLink releaseId={release.id} />
      <dl className="grid gap-x-6 gap-y-1 text-sm md:grid-cols-[max-content_1fr]">
        <dt className="text-ink-dim">Service</dt>
        <dd className="break-words text-ink">{release.service_name}</dd>
        <dt className="text-ink-dim">Version</dt>
        <dd className="flex flex-wrap items-baseline gap-2 break-words font-mono text-ink">
          <span className="min-w-0 break-all">{release.version}</span>
          <CopyButton value={release.version} label="Copy the version" />
        </dd>
        <dt className="text-ink-dim">Previous version</dt>
        <dd className="break-all font-mono text-ink">{period?.previous_version ?? "—"}</dd>
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
      {release.deploys.length > 0 ? (
        <div className="flex flex-col gap-1">
          <h3 className="text-ink-dim text-sm">Deploys your CI reported</h3>
          <ul className="flex flex-col gap-1 text-sm">
            {release.deploys.map((deploy) => (
              <li key={deploy.id} className="min-w-0 break-words text-ink">
                {deployWords(deploy, zone, now)}
                {deploy.commit_sha ? (
                  <span className="break-all font-mono text-ink-dim">
                    {" "}
                    · commit {deploy.commit_sha}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

const STATE_WORDS: Record<DeployFact["state"], string> = {
  started: "Started",
  finished: "Finished",
  failed: "Failed",
  rolled_back: "Rolled back",
};

/** The sender's time; our arrival time beside it when the two are over 15 minutes apart. */
function deployWords(deploy: DeployFact, zone: string | undefined, now: number): string {
  const claimed = formatZonedTime(deploy.deployed_at, { zone, now });
  const apart = Math.abs(Date.parse(deploy.received_at) - Date.parse(deploy.deployed_at));
  const arrived =
    apart > CLAIM_GAP_SHOWN_MS
      ? ` (the time your CI gave; it arrived ${formatZonedTime(deploy.received_at, { zone, now })})`
      : "";
  return `${STATE_WORDS[deploy.state]} ${claimed}${arrived}`;
}

/** Only an https link becomes one, and it carries no referrer (BR10, AC44). */
function safeLink(url: string | null): string | null {
  return url?.startsWith("https://") ? url : null;
}

function CiLink({ releaseId }: { releaseId: string }) {
  const details = useSenderDetails(releaseId);
  const latest = [...(details.data?.deploys ?? [])]
    .reverse()
    .find((deploy) => safeLink(deploy.url));
  const href = latest ? safeLink(latest.url) : null;
  if (href === null) return null;
  return (
    <p className="min-w-0 break-all text-sm">
      <span className="text-ink-dim">Link from your CI: </span>
      <a
        href={href}
        rel="noopener noreferrer nofollow"
        referrerPolicy="no-referrer"
        className={LINK}
      >
        {href}
      </a>
    </p>
  );
}

function SenderSection({
  releaseId,
  zone,
  now,
}: {
  releaseId: string;
  zone: string | undefined;
  now: number;
}) {
  const details = useSenderDetails(releaseId);
  if (details.isPending) return <SkeletonRows rows={1} label="Reading what your CI sent" />;
  if (details.isError) {
    return (
      <InlineFailure
        title="What your CI sent could not be read"
        error={details.error}
        onRetry={() => details.refetch()}
      />
    );
  }
  if (details.data.deploys.length === 0) return null;
  return (
    <section aria-label="Sender's details" className="flex flex-col gap-2">
      <h2 className={SECTION_HEADING}>What your CI sent</h2>
      <ul className="flex flex-col gap-2 text-sm">
        {details.data.deploys.map((deploy) => (
          <li key={deploy.deploy_id} className="flex min-w-0 flex-col gap-1 break-words">
            <span className="text-ink">
              {formatZonedTime(deploy.deployed_at, { zone, now })} · source{" "}
              <span className="break-all font-mono">{deploy.source}</span>
            </span>
            {deploy.metadata_withheld ? (
              <span className="text-ink-dim">
                Only members and above can read the details your CI sent.
              </span>
            ) : deploy.metadata && Object.keys(deploy.metadata).length > 0 ? (
              <details>
                <summary className="cursor-pointer text-ink-dim">Details your CI sent</summary>
                <dl className="grid gap-x-4 font-mono text-ink md:grid-cols-[max-content_1fr]">
                  {Object.entries(deploy.metadata).map(([key, value]) => (
                    <div key={key} className="contents">
                      <dt className="break-all text-ink-dim">{key}</dt>
                      <dd className="break-all">{value}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            ) : null}
          </li>
        ))}
      </ul>
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
