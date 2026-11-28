import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import type { ApiError } from "@/shared/api-client";
import { isConflict } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatRelativeTime } from "@/shared/format/time";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import { useCan } from "@/shared/identity";

import type { DashboardSummary } from "./api";
import { useDeletedDashboards, useRestoreDashboard } from "./api";
import { PurgeConfirm } from "./PurgeConfirm";

/**
 * Recently deleted (F20, FR51, AC11, AC57, AC78): what a delete hid, for 30 days. An editor
 * restores one whole; a title now in use is said, naming what holds it, and asks for another
 * (AC11); a restore at the cap says why in the API's words (AC78). A viewer reads the list
 * and is offered nothing that would fail. An owner or admin may also remove one at once.
 *
 * `now` is the clock "deleted 2 days ago" and the days left count from.
 */
const RESTORABLE_DAYS = 30;
const MS_PER_DAY = 86_400_000;

export function RecentlyDeleted({ now = Date.now() }: { now?: number }) {
  const deleted = useDeletedDashboards();
  const [restored, setRestored] = useState<string | undefined>();

  if (deleted.isPending) return <SkeletonRows rows={3} label="Loading recently deleted" />;
  if (deleted.isError) {
    return (
      <InlineFailure
        title="Could not read the list"
        error={deleted.error}
        onRetry={() => void deleted.refetch()}
      />
    );
  }
  return (
    <section aria-label="Recently deleted" className="flex flex-col gap-3">
      <p role="status" className="text-ink text-sm">
        {restored ? (
          <>
            Restored <em>{visibleEscapes(restored)}</em>
          </>
        ) : null}
      </p>
      {deleted.data.length === 0 ? (
        <p className="py-3 text-ink-dim text-sm leading-relaxed">
          Nothing deleted in the last {RESTORABLE_DAYS} days.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-line-subtle">
          {deleted.data.map((row) => (
            <DeletedRow key={row.id} row={row} now={now} onRestored={setRestored} />
          ))}
        </ul>
      )}
    </section>
  );
}

function DeletedRow({
  row,
  now,
  onRestored,
}: {
  row: DashboardSummary;
  now: number;
  onRestored: (title: string) => void;
}) {
  const canRestore = useCan("edit_dashboards");
  const canPurge = useCan("purge_dashboards");
  const restore = useRestoreDashboard(row.id);
  const [asking, setAsking] = useState(false);
  const [title, setTitle] = useState(row.title);
  const [removing, setRemoving] = useState(false);
  const left = daysLeft(row, now);

  const send = (newTitle?: string) =>
    restore.mutate(newTitle, {
      onSuccess: (dashboard) => {
        setAsking(false);
        onRestored(dashboard.title);
      },
      onError: (error) => {
        // A title in use is the one refusal that has an answer here: another title (AC11).
        if (isConflict(error) && titleIsTaken(error)) setAsking(true);
      },
    });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    send(title.trim());
  };

  return (
    <li className="flex flex-col gap-2 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div>
          <span className="text-ink">{visibleEscapes(row.title)}</span>
          <span className="ml-2 text-ink-dim text-sm">
            deleted {row.deleted_at ? formatRelativeTime(row.deleted_at, now) : "recently"}, can be
            restored for {left} more {left === 1 ? "day" : "days"}
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {canRestore ? (
            <Button
              variant="secondary"
              pending={restore.isPending && !asking}
              pendingLabel="Restoring"
              onClick={() => send()}
            >
              Restore
            </Button>
          ) : null}
          {canPurge && !removing ? (
            <Button variant="secondary" onClick={() => setRemoving(true)}>
              Remove at once
            </Button>
          ) : null}
        </div>
      </div>
      {asking ? (
        <form onSubmit={submit} className="flex flex-col gap-2">
          <p role="alert" className="text-ink text-sm">
            {(restore.error as ApiError | null)?.problem?.detail ??
              "A dashboard with this title exists. Choose another title."}
          </p>
          <TextField
            label="Restore under this title"
            name="title"
            value={title}
            onChange={setTitle}
          />
          <div>
            <Button type="submit" pending={restore.isPending} pendingLabel="Restoring">
              Restore under this title
            </Button>
          </div>
        </form>
      ) : restore.isError ? (
        <p role="alert" className="text-critical-text text-sm">
          {(restore.error as ApiError).problem?.detail ?? "Could not restore it. Try again."}
        </p>
      ) : null}
      {removing ? (
        <PurgeConfirm
          dashboardId={row.id}
          title={row.title}
          onDone={() => setRemoving(false)}
          onCancel={() => setRemoving(false)}
        />
      ) : null}
      {restore.isSuccess ? (
        <Link
          to="/dashboards/$dashboardId"
          params={{ dashboardId: row.id }}
          className="text-ink-dim text-sm underline"
        >
          Open it
        </Link>
      ) : null}
    </li>
  );
}

/** The API names the dashboard in the way when a title is taken. */
function titleIsTaken(error: unknown): boolean {
  return (error as ApiError).problem?.existing_dashboard_id !== undefined;
}

/** Whole days left to restore it, never below one while it is listed. */
function daysLeft(row: DashboardSummary, now: number): number {
  if (!row.deleted_at) return RESTORABLE_DAYS;
  const elapsed = Math.floor((now - Date.parse(row.deleted_at)) / MS_PER_DAY);
  return Math.max(RESTORABLE_DAYS - elapsed, 1);
}
