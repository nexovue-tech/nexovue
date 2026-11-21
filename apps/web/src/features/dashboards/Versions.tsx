import { useEffect, useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatRelativeTime } from "@/shared/format/time";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import { useCan } from "@/shared/identity";

import type { DashboardDocument, DashboardVersion } from "./api";
import { useDashboardVersion, useDashboardVersions } from "./api";
import { PurgeConfirm } from "./PurgeConfirm";

/**
 * A dashboard's history (F20, FR51, AC56): the versions its saves kept, who saved each and
 * when. "Restore this version" opens one as a **draft**: nothing is written until Save,
 * and saving makes a new version, so a restore can itself be undone (AC56). An owner or
 * admin may also remove one version at once (FR68). A viewer reads the list and is offered
 * nothing that would fail.
 *
 * `now` is the clock "2 hours ago" counts from.
 */
export function Versions({
  dashboardId,
  title,
  onRestore,
  now = Date.now(),
}: {
  dashboardId: string;
  title: string;
  /** Hands the version to the builder as its draft. Absent where there is no builder. */
  onRestore?: (document: DashboardDocument) => void;
  now?: number;
}) {
  const history = useDashboardVersions(dashboardId);
  const canEdit = useCan("edit_dashboards");
  const canPurge = useCan("purge_dashboards");
  const [restoring, setRestoring] = useState<number | undefined>();
  const [removing, setRemoving] = useState<number | undefined>();
  const picked = useDashboardVersion(dashboardId, restoring);

  // The version arrives, and becomes the draft once.
  useEffect(() => {
    if (picked.data && picked.data.version === restoring) {
      onRestore?.(documentOf(picked.data));
      setRestoring(undefined);
    }
  }, [picked.data, restoring, onRestore]);

  if (history.isPending) return <SkeletonRows rows={3} label="Loading the history" />;
  if (history.isError) {
    return (
      <InlineFailure
        title="Could not read the history"
        error={history.error}
        onRetry={() => void history.refetch()}
      />
    );
  }
  const earlier = history.data.filter((entry) => !entry.current);

  return (
    <section aria-label="Versions" className="flex flex-col gap-3">
      <h2 className="font-medium text-ink text-sm">Versions</h2>
      {earlier.length === 0 ? (
        <p className="text-ink-dim text-sm">No earlier versions yet.</p>
      ) : null}
      <ul className="flex flex-col divide-y divide-line-subtle">
        {history.data.map((entry) => (
          <li
            key={entry.version}
            className="flex flex-wrap items-baseline justify-between gap-2 py-2"
          >
            <span className="text-sm">
              <span className="text-ink">Version {entry.version}</span>
              <span className="text-ink-dim">
                {" "}
                · {entry.saved_by ? visibleEscapes(entry.saved_by) : "Former member"} ·{" "}
                {formatRelativeTime(entry.saved_at, now)}
                {entry.title === title ? "" : ` · titled ${visibleEscapes(entry.title)}`}
              </span>
              {entry.current ? <span className="ml-2 text-ink-dim">In use</span> : null}
            </span>
            {entry.current ? null : (
              <span className="flex flex-wrap gap-2">
                {canEdit && onRestore ? (
                  <Button
                    variant="secondary"
                    pending={restoring === entry.version}
                    pendingLabel="Opening"
                    onClick={() => setRestoring(entry.version)}
                  >
                    Restore this version
                  </Button>
                ) : null}
                {canPurge && removing !== entry.version ? (
                  <Button variant="secondary" onClick={() => setRemoving(entry.version)}>
                    Remove at once
                  </Button>
                ) : null}
              </span>
            )}
          </li>
        ))}
      </ul>
      {picked.isError ? (
        <p role="alert" className="text-critical-text text-sm">
          Could not open that version. Try again.
        </p>
      ) : null}
      {removing !== undefined ? (
        <PurgeConfirm
          dashboardId={dashboardId}
          title={history.data.find((entry) => entry.version === removing)?.title ?? title}
          version={removing}
          onDone={() => setRemoving(undefined)}
          onCancel={() => setRemoving(undefined)}
        />
      ) : null}
    </section>
  );
}

function documentOf(version: DashboardVersion): DashboardDocument {
  return {
    title: version.title,
    description: version.description,
    definition: version.definition,
  };
}
