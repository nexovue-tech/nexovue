import { useEffect, useRef, useState } from "react";

import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { formatRelativeTime } from "@/shared/format/time";
import { visibleEscapes } from "@/shared/format/visible-escapes";

import type { Dashboard, DashboardDocument } from "./api";
import { useDeleteDashboard } from "./api";

/**
 * What can be done to a whole dashboard (F20, T3.6). Delete goes through the shared inline
 * `ConfirmPanel`, saying what goes in real numbers and that it can be restored for 30 days
 * (FR25, AC136), with export offered first, since a file is a copy nobody can delete.
 *
 * Its own component so the read view can mount it as well (plan TF.1). Duplicate and
 * versions join it in Step 7.
 */

const EXPORT_TYPE = "application/json";
const RESTORABLE_DAYS = 30;

export function DashboardActions({
  dashboard,
  onDeleted,
}: {
  dashboard: Dashboard;
  onDeleted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const remove = useDeleteDashboard(dashboard.id);
  const opener = useRef<HTMLDivElement>(null);
  const panels = dashboard.definition.panels.length;
  const opened = dashboard.last_viewed_at
    ? `last opened ${formatRelativeTime(dashboard.last_viewed_at)}`
    : "never opened";

  const givingBack = useRef(false);
  // Back to the button that asked, once it exists again: where a keyboard user expects to be.
  useEffect(() => {
    if (confirming || !givingBack.current) return;
    givingBack.current = false;
    opener.current?.querySelector("button")?.focus();
  }, [confirming]);

  const keep = () => {
    givingBack.current = true;
    setConfirming(false);
  };
  const sentence = `, ${panels} ${panels === 1 ? "panel" : "panels"}, ${opened}. It can be restored for ${RESTORABLE_DAYS} days.`;

  return (
    <div className="flex flex-col gap-3">
      <div ref={opener}>
        {confirming ? null : (
          <Button variant="secondary" onClick={() => setConfirming(true)}>
            Delete this dashboard
          </Button>
        )}
      </div>
      {confirming ? (
        <ConfirmPanel label={`Delete ${visibleEscapes(dashboard.title)}`}>
          <p>
            <em>{visibleEscapes(dashboard.title)}</em>
            {sentence}
          </p>
          <p className="text-ink-dim">
            Export it first to keep a copy. The file includes notes, links, service names and host
            names.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              pending={remove.isPending}
              pendingLabel="Deleting"
              onClick={() => remove.mutate(undefined, { onSuccess: onDeleted })}
            >
              Delete
            </Button>
            <Button variant="secondary" onClick={() => exportDocument(dashboard)}>
              Export it first
            </Button>
            <Button variant="secondary" onClick={keep}>
              Keep it
            </Button>
          </div>
          {remove.isError ? (
            <p className="text-critical-text">Could not delete this dashboard. Try again.</p>
          ) : null}
        </ConfirmPanel>
      ) : null}
    </div>
  );
}

/**
 * The dashboard document alone: no organization, owner, dashboard id or version, so a
 * file works in another organization and says nothing about this one (FR42).
 */
function exportDocument(dashboard: Dashboard): void {
  const document_: DashboardDocument = {
    title: dashboard.title,
    description: dashboard.description,
    definition: dashboard.definition,
  };
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(document_, null, 2)], { type: EXPORT_TYPE }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `${dashboard.title.replace(/[^\w.-]+/g, "-") || "dashboard"}.json`;
  link.click();
  URL.revokeObjectURL(url);
}
