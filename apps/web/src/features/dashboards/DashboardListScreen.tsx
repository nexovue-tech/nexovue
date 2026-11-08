import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatRelativeTime } from "@/shared/format/time";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import { useCan } from "@/shared/identity";

import type { DashboardSummary, DashboardTemplate } from "./api";
import { useDashboards, useDashboardTemplates } from "./api";
import { TitleDialog } from "./TitleDialog";

/**
 * The organization's dashboards (F20, Step 2): one action, *Start from Overview*, which
 * opens a draft to name and save. Sorting, the filter, "not opened for 30 days" and
 * Recently deleted arrive with plan Step 7. A viewer is offered nothing that would fail
 * (AC7).
 */
export function DashboardListScreen() {
  const dashboards = useDashboards();
  const templates = useDashboardTemplates();
  const canEdit = useCan("edit_dashboards");
  const [draft, setDraft] = useState<DashboardTemplate | undefined>();
  const start = templates.data?.[0];

  const startAction =
    canEdit && start ? <Button onClick={() => setDraft(start)}>{start.name}</Button> : undefined;

  return (
    <div className="flex flex-col gap-4 p-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-semibold text-ink text-lg">Dashboards</h1>
        {dashboards.data && dashboards.data.length > 0 ? startAction : null}
      </header>
      {draft ? (
        <TitleDialog document={draft.document} onCancel={() => setDraft(undefined)} />
      ) : null}
      <Body
        pending={dashboards.isPending}
        error={dashboards.isError ? dashboards.error : undefined}
        rows={dashboards.data}
        canEdit={canEdit}
        startAction={startAction}
        onRetry={() => void dashboards.refetch()}
      />
    </div>
  );
}

function Body({
  pending,
  error,
  rows,
  canEdit,
  startAction,
  onRetry,
}: {
  pending: boolean;
  error: unknown;
  rows: DashboardSummary[] | undefined;
  canEdit: boolean;
  startAction: ReactNode;
  onRetry: () => void;
}) {
  if (pending) return <SkeletonRows rows={4} label="Loading dashboards" />;
  if (error !== undefined) {
    return <InlineFailure title="Could not list dashboards" error={error} onRetry={onRetry} />;
  }
  if (!rows || rows.length === 0) {
    return canEdit ? (
      <EmptyState
        title="No dashboards yet"
        detail="Start from Overview: its three charts, as a dashboard to name and keep."
        action={startAction}
      />
    ) : (
      <EmptyState title="No dashboards yet" detail="Ask a member to create one." />
    );
  }
  return (
    <table className="w-full text-left text-sm">
      <thead>
        <tr className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          <th className="py-2 font-normal">Title</th>
          <th className="py-2 font-normal">Owner</th>
          <th className="py-2 font-normal">Panels</th>
          <th className="py-2 font-normal">Last opened</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id} className="border-line-subtle border-t">
            <td className="py-2">
              <Link
                to="/dashboards/$dashboardId"
                params={{ dashboardId: row.id }}
                className="text-ink hover:underline"
              >
                {visibleEscapes(row.title)}
              </Link>
            </td>
            <td className="py-2 text-ink-dim">
              {row.owner ? visibleEscapes(row.owner) : "Former member"}
            </td>
            <td className="py-2 text-ink-dim">{row.panel_count}</td>
            <td className="py-2 text-ink-dim">
              {row.last_viewed_at ? formatRelativeTime(row.last_viewed_at) : "Never"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
