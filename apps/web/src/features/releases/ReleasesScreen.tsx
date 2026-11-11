import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { useDetectionSettings } from "@/shared/detection/api";
import { shortVersion } from "@/shared/format/version";
import { formatZonedTime } from "@/shared/format/zoned";
import { useProjectSelection } from "@/shared/projects";
import { useLiveWindow, useWindowPhrase } from "@/shared/selection";
import { SETUP_DOCS_PATH } from "@/shared/setup-docs";

import type { ReleaseFilters, ReleaseList, ReleaseRow } from "./api";
import { useReleases } from "./api";
import { activityWords, basisWords, latestWords, stateWords, windowCount } from "./release-words";

/**
 * `/releases` (F28 design): every release with a period in the window, newest first, so a
 * version serving since yesterday is in today's list. Postgres only (AC81).
 *
 * A linked list, not a table, so service, version, activity and flags stay readable at
 * 375px: one line each on a phone, a grid from `md`. Times are on the project's clock.
 */
export function ReleasesScreen() {
  const { projectId } = useProjectSelection();
  const timeWindow = useLiveWindow();
  const phrase = useWindowPhrase();
  const zone = useDetectionSettings(projectId).data?.timezone;
  const [filters, setFilters] = useState<ReleaseFilters>({});
  const [draft, setDraft] = useState<ReleaseFilters>({});
  // The cursors of the pages before this one, so "Previous page" can go back.
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const cursor = cursors[cursors.length - 1] ?? null;
  const releases = useReleases(projectId, timeWindow, filters, cursor);

  const apply = (next: ReleaseFilters) => {
    setFilters(next);
    setDraft(next);
    setCursors([null]);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    apply({
      service: draft.service?.trim() || undefined,
      version: draft.version?.trim() || undefined,
    });
  };

  return (
    <div className="flex flex-col gap-4 px-4 py-5">
      <header className="flex flex-col gap-1">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Releases</h1>
        {releases.data ? (
          <p aria-live="polite" className="max-w-prose text-ink text-sm">
            {windowCount(releases.data.total, releases.data.services)}
          </p>
        ) : null}
      </header>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <div className="w-full sm:w-56">
          <TextField
            label="Service"
            name="service"
            required={false}
            value={draft.service ?? ""}
            onChange={(service) => setDraft({ ...draft, service })}
          />
        </div>
        <div className="w-full sm:w-56">
          <TextField
            label="Version"
            name="version"
            required={false}
            value={draft.version ?? ""}
            onChange={(version) => setDraft({ ...draft, version })}
          />
        </div>
        <Button type="submit" variant="secondary">
          Filter
        </Button>
      </form>
      {releases.isPending ? <SkeletonRows rows={4} label="Loading releases" /> : null}
      {releases.isError ? (
        <InlineFailure
          title="The releases could not be loaded"
          error={releases.error}
          onRetry={() => releases.refetch()}
        />
      ) : null}
      {releases.data ? (
        <Listing
          list={releases.data}
          filters={filters}
          phrase={phrase}
          zone={zone}
          onClear={() => apply({})}
          onNext={
            releases.data.next_cursor
              ? () => setCursors([...cursors, releases.data.next_cursor])
              : undefined
          }
          onPrevious={cursors.length > 1 ? () => setCursors(cursors.slice(0, -1)) : undefined}
        />
      ) : null}
    </div>
  );
}

function Listing({
  list,
  filters,
  phrase,
  zone,
  onClear,
  onNext,
  onPrevious,
}: {
  list: ReleaseList;
  filters: ReleaseFilters;
  phrase: string;
  zone: string | undefined;
  onClear: () => void;
  onNext: (() => void) | undefined;
  onPrevious: (() => void) | undefined;
}) {
  const now = Date.now();
  if (!list.has_any) {
    return (
      <EmptyState
        title="No releases yet."
        detail="Pass a build id into service.version, or send deploys from CI."
        action={
          <Link to={SETUP_DOCS_PATH} className="font-medium text-brand text-sm">
            Setup documentation
          </Link>
        }
      />
    );
  }
  if (list.releases.length === 0) {
    const filtered = filters.service || filters.version;
    if (filtered) {
      const named = [filters.version, filters.service].filter(Boolean).join(" of ");
      return (
        <div className="flex flex-col items-start gap-3">
          <p className="max-w-prose text-ink text-sm">No releases of {named} in this window.</p>
          <Button variant="secondary" onClick={onClear}>
            Clear the filter
          </Button>
        </div>
      );
    }
    return (
      <p className="max-w-prose text-ink text-sm">
        No releases in {phrase}.{" "}
        {list.latest
          ? latestWords(list.latest.version, list.latest.service_name, list.latest.started_at, now)
          : null}{" "}
        A wider window in the top bar shows more.
      </p>
    );
  }
  return (
    <section aria-label="Releases" className="flex flex-col gap-3">
      <ul className="flex flex-col divide-y divide-line border-line border-y">
        {list.releases.map((release) => (
          <Row key={release.period_id} release={release} zone={zone} now={now} />
        ))}
      </ul>
      {onNext || onPrevious ? (
        <nav aria-label="Pages" className="flex gap-3">
          {onPrevious ? (
            <Button variant="secondary" onClick={onPrevious}>
              Previous page
            </Button>
          ) : null}
          {onNext ? (
            <Button variant="secondary" onClick={onNext}>
              Next page
            </Button>
          ) : null}
        </nav>
      ) : null}
    </section>
  );
}

function Row({
  release,
  zone,
  now,
}: {
  release: ReleaseRow;
  zone: string | undefined;
  now: number;
}) {
  const flags = [
    release.already_running ? "Running when we started counting" : null,
    release.is_return ? "Came back" : null,
    stateWords(release.last_state),
  ].filter((flag): flag is string => flag !== null);
  return (
    <li>
      <Link
        to="/releases/$releaseId"
        params={{ releaseId: release.id }}
        search={{ period: release.period_id }}
        className="grid gap-1 py-3 text-sm md:grid-cols-[minmax(10rem,1fr)_minmax(10rem,1fr)_minmax(12rem,1.5fr)_auto] md:items-baseline md:gap-4"
      >
        <span className="min-w-0 break-words">
          <span className="font-medium text-ink">{release.service_name}</span>{" "}
          <span className="font-mono text-ink" title={release.version}>
            {shortVersion(release.version)}
          </span>
        </span>
        <span className="text-ink-dim">{basisWords(release.basis)}</span>
        <span className="text-ink">
          {activityWords(release.activity, {
            startedAt: release.started_at,
            lastSeenAt: release.last_seen_at,
            zone,
            now,
          })}
          {flags.length > 0 ? <span className="text-ink-dim"> · {flags.join(" · ")}</span> : null}
        </span>
        <span className="text-ink-dim">
          Started {formatZonedTime(release.started_at, { zone, now })}
        </span>
      </Link>
    </li>
  );
}
