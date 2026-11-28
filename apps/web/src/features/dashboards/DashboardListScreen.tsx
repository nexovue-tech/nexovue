import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";

import { Button } from "@/shared/components/Button";
import type { Column } from "@/shared/components/DataTable";
import { DataTable } from "@/shared/components/DataTable";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatRelativeTime } from "@/shared/format/time";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import { useCan } from "@/shared/identity";

import type { DashboardSummary } from "./api";
import {
  handOverImported,
  useDashboards,
  useDashboardTemplates,
  useDeletedDashboards,
} from "./api";
import type { ListColumn, ListOrder } from "./dashboard-list";
import {
  DEFAULT_ORDER,
  FORMER_MEMBER,
  filterByTitle,
  isStale,
  nextOrder,
  notReadingCell,
  notReadingWords,
  STALE_AFTER_DAYS,
  sortRows,
  startingPoints,
} from "./dashboard-list";
import { ImportFile } from "./FileTransfer";
import { RecentlyDeleted } from "./RecentlyDeleted";

/**
 * The organization's dashboards (F20, FR36, FR58): which one do I want, and which can go.
 * It shows no panels, numbers or previews, only what the stored columns say.
 *
 * Sorted by any column through real header buttons, filtered by title in the browser (the
 * list holds at most 1,000), a dashboard not opened for 30 days marked, and "3 panels not
 * reading" where the last stamped read found some. Each state is designed: loading, failed,
 * empty for an editor (each starting point, then a blank dashboard, FR39), empty for a
 * viewer, and no results with a way to clear the filter. Recently deleted lists what a
 * delete hid. A viewer is offered nothing that would fail (AC7), import included (FR42).
 *
 * A starting point already saved reads "Open" and goes to it (FR39). A file that passes
 * the API's check opens in the builder as an unsaved draft; one that does not is refused
 * here in words, and nothing opens.
 *
 * `now` is the clock "2 hours ago" and "not opened for 30 days" count from.
 */
const ACTION_CLASS =
  "inline-flex min-h-11 items-center rounded-sm border border-line px-3 py-1.5 text-ink text-sm hover:bg-hover md:min-h-0";

export function DashboardListScreen({ now = Date.now() }: { now?: number }) {
  const dashboards = useDashboards();
  const deleted = useDeletedDashboards();
  const templates = useDashboardTemplates();
  const canEdit = useCan("edit_dashboards");
  const [order, setOrder] = useState<ListOrder>(DEFAULT_ORDER);
  const [typed, setTyped] = useState("");
  const [showDeleted, setShowDeleted] = useState(false);
  const client = useQueryClient();
  const navigate = useNavigate();

  const startAction = canEdit ? (
    <div className="flex flex-wrap gap-2">
      {startingPoints(templates.data ?? [], dashboards.data ?? []).map((point) =>
        point.kind === "open" ? (
          <Link
            key={point.templateId}
            to="/dashboards/$dashboardId"
            params={{ dashboardId: point.dashboardId }}
            className={ACTION_CLASS}
          >
            {visibleEscapes(point.label)}
          </Link>
        ) : (
          <Link
            key={point.templateId}
            to="/dashboards/new"
            search={{ template: point.templateId }}
            className={ACTION_CLASS}
          >
            {point.label}
          </Link>
        ),
      )}
      <Link to="/dashboards/new" search={{}} className={ACTION_CLASS}>
        New dashboard
      </Link>
    </div>
  ) : undefined;
  const hasRows = dashboards.data !== undefined && dashboards.data.length > 0;

  return (
    <div className="flex flex-col gap-4 p-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="heading-page">Dashboards</h1>
        {/* With dashboards listed, FR39's starting points sit here; empty, they lead the
            empty state, blank dashboard last (FR39). */}
        <div className="flex flex-wrap items-start gap-2">
          {hasRows ? startAction : null}
          {canEdit ? (
            <ImportFile
              label="Import"
              onOpened={(document) =>
                void navigate({
                  to: "/dashboards/new",
                  search: { file: handOverImported(client, document) },
                })
              }
            />
          ) : null}
        </div>
      </header>
      {hasRows ? (
        <input
          type="search"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder="Filter by title"
          aria-label="Filter by title"
          className="w-full rounded-sm sm:w-80 border border-line bg-surface px-2.5 py-1.5 text-ink text-sm outline-none placeholder:text-ink-dim hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
        />
      ) : null}
      <Body
        pending={dashboards.isPending}
        error={dashboards.isError ? dashboards.error : undefined}
        rows={dashboards.data}
        canEdit={canEdit}
        startAction={startAction}
        order={order}
        onOrder={(column) => setOrder((current) => nextOrder(current, column))}
        typed={typed}
        onClearFilter={() => setTyped("")}
        now={now}
        onRetry={() => void dashboards.refetch()}
      />
      <div className="flex flex-col gap-3">
        <div>
          <Button
            variant="quiet"
            aria-expanded={showDeleted}
            onClick={() => setShowDeleted((open) => !open)}
          >
            Recently deleted{deleted.data ? ` (${deleted.data.length})` : ""}
          </Button>
        </div>
        {showDeleted ? <RecentlyDeleted now={now} /> : null}
      </div>
    </div>
  );
}

function Body({
  pending,
  error,
  rows,
  canEdit,
  startAction,
  order,
  onOrder,
  typed,
  onClearFilter,
  now,
  onRetry,
}: {
  pending: boolean;
  error: unknown;
  rows: DashboardSummary[] | undefined;
  canEdit: boolean;
  startAction: ReactNode;
  order: ListOrder;
  onOrder: (column: ListColumn) => void;
  typed: string;
  onClearFilter: () => void;
  now: number;
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
        detail="Start from a template, as a draft to change, name and keep. Nothing is saved until you save it."
        action={startAction}
      />
    ) : (
      <EmptyState title="No dashboards yet" detail="Ask a member to create one." />
    );
  }
  const shown = sortRows(filterByTitle(rows, typed), order);
  if (shown.length === 0) {
    // A filter that excludes everything names itself and is never read as "no dashboards".
    return (
      <div className="flex flex-col items-start gap-2 py-3">
        <p className="text-ink text-sm">
          No dashboard has “{visibleEscapes(typed.trim())}” in its title.
        </p>
        <button
          type="button"
          onClick={onClearFilter}
          className="min-h-11 rounded-sm border border-line px-3 py-1.5 text-ink text-sm hover:bg-hover md:min-h-0"
        >
          Clear filter
        </button>
      </div>
    );
  }
  return (
    <DataTable
      caption="Dashboards"
      rows={shown}
      rowKey={(row) => row.id}
      empty=""
      sort={{
        key: order.column,
        direction: order.direction,
        onSort: (key) => onOrder(key as ListColumn),
      }}
      columns={columnsFor(now)}
    />
  );
}

function columnsFor(now: number): Column<DashboardSummary>[] {
  return [
    {
      key: "title",
      header: "Title",
      sortable: true,
      cell: (row) => <TitleCell row={row} now={now} />,
    },
    {
      key: "owner",
      header: "Owner",
      sortable: true,
      wideOnly: true,
      cell: (row) => (
        <span className="text-ink-dim">
          {row.owner ? visibleEscapes(row.owner) : FORMER_MEMBER}
        </span>
      ),
    },
    {
      key: "panels",
      header: "Panels",
      numeric: true,
      sortable: true,
      wideOnly: true,
      cell: (row) => <span className="text-ink-dim">{row.panel_count}</span>,
    },
    {
      key: "opened",
      header: "Last opened",
      sortable: true,
      cell: (row) => <OpenedCell row={row} now={now} />,
    },
    {
      key: "changed",
      header: "Last changed",
      sortable: true,
      wideOnly: true,
      cell: (row) => (
        <span className="text-ink-dim">{formatRelativeTime(row.updated_at, now)}</span>
      ),
    },
    {
      key: "not_reading",
      header: "Not reading",
      sortable: true,
      wideOnly: true,
      cell: (row) => <NotReadingCell row={row} />,
    },
  ];
}

/** Amber only for a count that needs attention; "None" and the dash are plain. */
function NotReadingCell({ row }: { row: DashboardSummary }) {
  const attention = row.last_viewed_at !== null && row.panels_not_reading > 0;
  return (
    <span className={attention ? "text-warning-text" : "text-ink-dim"}>{notReadingCell(row)}</span>
  );
}

/** On a phone the row is its title and last opened, and the rest sits under the title. */
function TitleCell({ row, now }: { row: DashboardSummary; now: number }) {
  const notReading = notReadingWords(row.panels_not_reading);
  return (
    <div className="flex flex-col gap-0.5">
      <Link
        to="/dashboards/$dashboardId"
        params={{ dashboardId: row.id }}
        className="text-ink hover:underline"
      >
        {visibleEscapes(row.title)}
      </Link>
      {row.description ? (
        <span className="text-ink-dim text-xs">{visibleEscapes(row.description)}</span>
      ) : null}
      <span className="text-ink-dim text-xs md:hidden">
        {row.owner ? visibleEscapes(row.owner) : FORMER_MEMBER} · {row.panel_count}{" "}
        {row.panel_count === 1 ? "panel" : "panels"} · changed{" "}
        {formatRelativeTime(row.updated_at, now)}
        {notReading ? <span className="text-warning-text"> · {notReading}</span> : null}
      </span>
    </div>
  );
}

function OpenedCell({ row, now }: { row: DashboardSummary; now: number }) {
  return (
    <span className="text-ink-dim">
      {row.last_viewed_at ? formatRelativeTime(row.last_viewed_at, now) : "Never"}
      {isStale(row, now) ? (
        <span className="text-warning-text"> · not opened for {STALE_AFTER_DAYS} days</span>
      ) : null}
    </span>
  );
}
