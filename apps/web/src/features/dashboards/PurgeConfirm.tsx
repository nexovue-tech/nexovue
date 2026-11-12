import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { visibleEscapes } from "@/shared/format/visible-escapes";

import { usePurgeDashboard } from "./api";

/**
 * Remove text at once, an owner's or an admin's (F20, FR68, AC120): a dashboard whole, or
 * one kept version of it. It says what goes and what stays, since this is the answer to a
 * pasted secret and nobody may believe it reaches further than it does: backups are outside
 * it. The shared inline `ConfirmPanel`, as every dangerous action here.
 */
export function PurgeConfirm({
  dashboardId,
  title,
  version,
  onDone,
  onCancel,
}: {
  dashboardId: string;
  title: string;
  /** The kept version to remove; undefined removes the dashboard and every version. */
  version?: number;
  onDone: () => void;
  onCancel: () => void;
}) {
  const purge = usePurgeDashboard(dashboardId);
  const what =
    version === undefined
      ? `${visibleEscapes(title)} and every version it keeps`
      : `version ${version} of ${visibleEscapes(title)}`;

  return (
    <ConfirmPanel label={`Remove ${what} at once`}>
      <p>
        This removes <em>{what}</em> now, for everyone, and cannot be undone. Its title is replaced
        with [removed] in the audit log.
      </p>
      <p className="text-ink-dim">
        Backups are outside this: a copy taken earlier is removed when that backup expires.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          pending={purge.isPending}
          pendingLabel="Removing"
          onClick={() => purge.mutate(version, { onSuccess: onDone })}
        >
          Remove at once
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Keep it
        </Button>
      </div>
      {purge.isError ? (
        <p role="alert" className="text-critical-text">
          Could not remove it. Try again.
        </p>
      ) : null}
    </ConfirmPanel>
  );
}
