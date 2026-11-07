import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { useCan } from "@/shared/identity";
import { useProjectSelection } from "@/shared/projects";

import type { DeletedSlo, SloView } from "./api";
import { useRestoreSlo, useSlos } from "./api";
import { SloBadge } from "./SloBadge";
import {
  deletedLineOf,
  headlineOf,
  liveState,
  REFRESH_MS,
  sentenceOf,
  staleBannerOf,
} from "./slo-words";

/**
 * `/slos`: which targets are in trouble, and which need me now (F19 design, *The list*).
 *
 * A linked list, not `DataTable`, so state and sentence stay visible at 375px. Rows
 * come in FR10a's order from the API; a row this browser sees go stale joins the
 * counted ones, so the top N rows are the headline's N.
 */
export function SlosScreen() {
  const { projectId } = useProjectSelection();
  const slos = useSlos(projectId);
  const now = useNow();
  const canConfigure = useCan("configure_slos");

  return (
    <div className="flex flex-col gap-4 px-4 py-5">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">
          Reliability targets (SLOs)
        </h1>
        {canConfigure ? (
          <Link to="/slos/new" className="font-medium text-brand text-sm">
            New target
          </Link>
        ) : null}
      </header>
      {slos.isPending ? <SkeletonRows rows={4} label="Loading targets" /> : null}
      {slos.isError ? (
        <InlineFailure
          title="The targets could not be loaded"
          error={slos.error}
          onRetry={() => slos.refetch()}
        />
      ) : null}
      {slos.data ? (
        <TargetList
          targets={slos.data.targets}
          deleted={slos.data.deleted}
          zone={slos.data.zone}
          hasServices={slos.data.has_services}
          canConfigure={canConfigure}
          now={now}
          refreshFailedAt={slos.isRefetchError ? slos.dataUpdatedAt : null}
        />
      ) : null}
    </div>
  );
}

/** The browser's clock, ticking with the refresh, so staleness shows without a read (AC107). */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), REFRESH_MS);
    return () => window.clearInterval(tick);
  }, []);
  return now;
}

function TargetList({
  targets,
  deleted,
  zone,
  hasServices,
  canConfigure,
  now,
  refreshFailedAt,
}: {
  targets: SloView[];
  deleted: DeletedSlo[];
  zone: string;
  hasServices: boolean;
  canConfigure: boolean;
  now: number;
  refreshFailedAt: number | null;
}) {
  if (targets.length === 0 && deleted.length === 0) {
    return <NoTargets hasServices={hasServices} canConfigure={canConfigure} />;
  }
  const banner = staleBannerOf(targets, now);
  const ordered = [...targets].sort(
    (one, other) =>
      counted(other, now) - counted(one, now) || targets.indexOf(one) - targets.indexOf(other),
  );
  return (
    <section aria-label="Targets" className="flex flex-col gap-3">
      <p aria-live="polite" className="max-w-prose text-ink text-sm">
        {headlineOf(targets, now)}
      </p>
      {banner ? (
        <p
          role="status"
          className="max-w-prose rounded border border-warning-border bg-warning-subtle p-2 text-sm text-warning-text"
        >
          {banner}
        </p>
      ) : null}
      {refreshFailedAt !== null ? (
        <p className="text-ink-dim text-sm">
          Could not refresh, last read at{" "}
          {new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(
            refreshFailedAt,
          )}
          .
        </p>
      ) : null}
      <ul className="flex flex-col divide-y divide-line border-line border-y">
        {ordered.map((view) => (
          <TargetRow key={view.id} view={view} zone={zone} now={now} />
        ))}
      </ul>
      {deleted.length > 0 ? (
        <ul aria-label="Deleted targets" className="flex flex-col gap-2">
          {deleted.map((line) => (
            <DeletedLine key={line.id} line={line} zone={zone} canConfigure={canConfigure} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function counted(view: SloView, now: number): number {
  const { state } = liveState(view, now);
  if (state === "disabled") return -1;
  return state === "not_up_to_date" || view.attention ? 1 : 0;
}

function TargetRow({ view, zone, now }: { view: SloView; zone: string; now: number }) {
  const { state } = liveState(view, now);
  const shown = { ...view, state, attention: state === "not_up_to_date" || view.attention };
  return (
    <li>
      <Link
        to="/slos/$sloId"
        params={{ sloId: view.id }}
        className="grid gap-1 py-3 text-sm md:grid-cols-[minmax(12rem,1fr)_auto_minmax(16rem,2fr)_auto] md:items-baseline md:gap-4"
      >
        <span className="font-medium text-ink">{view.name}</span>
        <span>
          <SloBadge view={shown} />
        </span>
        <span className="max-w-prose text-ink">{sentenceOf(view, zone, now)}</span>
        <span className="font-mono text-ink-dim">
          {view.figures.figure_percent === null ? "—" : `${view.figures.figure_percent}%`} against{" "}
          {Number(view.objective_percent)}%
        </span>
      </Link>
    </li>
  );
}

function DeletedLine({
  line,
  zone,
  canConfigure,
}: {
  line: DeletedSlo;
  zone: string;
  canConfigure: boolean;
}) {
  const restore = useRestoreSlo();
  return (
    <li className="flex flex-wrap items-baseline gap-3 text-ink-dim text-sm">
      <span className="max-w-prose">{deletedLineOf(line, zone)}</span>
      {canConfigure ? (
        <Button
          variant="secondary"
          pending={restore.isPending}
          onClick={() => restore.mutate({ sloId: line.id })}
        >
          Restore
        </Button>
      ) : null}
      {restore.isError ? (
        <InlineFailure title="The target could not be restored" error={restore.error} />
      ) : null}
    </li>
  );
}

function NoTargets({ hasServices, canConfigure }: { hasServices: boolean; canConfigure: boolean }) {
  if (!hasServices) {
    return (
      <EmptyState
        title="Nothing is reporting yet"
        detail="A target counts a service's requests, so it needs one sending them first."
        action={
          <Link to="/settings/general" className="font-medium text-brand text-sm">
            Set up sending
          </Link>
        }
      />
    );
  }
  return (
    <EmptyState
      title="No reliability targets yet"
      detail="A target is how much failure your team accepts for a service, such as 99.9% of requests succeeding over 28 days. Nexovue then says how much is still allowed."
      action={
        canConfigure ? (
          <Link to="/slos/new" className="font-medium text-brand text-sm">
            New target
          </Link>
        ) : (
          <span className="text-ink-dim text-sm">Ask an owner, admin or member to add one.</span>
        )
      }
    />
  );
}
