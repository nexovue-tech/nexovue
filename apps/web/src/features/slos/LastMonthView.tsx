import { Link } from "@tanstack/react-router";

import { CopyButton } from "@/shared/components/CopyButton";
import { type Column, DataTable } from "@/shared/components/DataTable";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";

import type { SloLastMonth, SloMonthRow } from "./api";
import { monthsCsvUrl, useLastMonth } from "./api";
import {
  copyTextOf,
  dayIn,
  lastMonthHeadline,
  leavesOutOf,
  MONTHS_KEPT,
  monthName,
  NO_SINGLE_FIGURE,
  ONE_PROJECT,
  RESULT_WORDS,
} from "./month-words";

/**
 * `/slos` *Last month* (F19 FR27b, design *Last month*): how did we do last month, and
 * what does that leave out. "N of M targets met" with missed and not judged named, and
 * no single blended figure (AC78). A target deleted after the month ended still counts
 * (AC112). The table is a record, so `DataTable` is right here; the headline and the
 * copy action stay outside its scroll.
 */
export function LastMonthView({ projectId }: { projectId: string }) {
  const last = useLastMonth(projectId);
  if (last.isPending) return <SkeletonRows rows={4} label="Loading last month" />;
  if (last.isError) {
    return (
      <InlineFailure
        title="Last month could not be loaded"
        error={last.error}
        onRetry={() => last.refetch()}
      />
    );
  }
  return <Month last={last.data} projectId={projectId} />;
}

function Month({ last, projectId }: { last: SloLastMonth; projectId: string }) {
  if (last.targets === 0) {
    return (
      <p className="py-3 text-ink-dim text-sm">
        No target held a counted day in {monthName(last.month)}.
      </p>
    );
  }
  const columns: Column<SloMonthRow>[] = [
    { key: "target", header: "Target", cell: (row) => <TargetCell row={row} zone={last.zone} /> },
    {
      key: "figure",
      header: "Figure",
      numeric: true,
      cell: (row) => (row.figure_percent === null ? "—" : `${row.figure_percent}%`),
    },
    {
      key: "objective",
      header: "Target",
      numeric: true,
      cell: (row) => `${Number(row.objective_percent)}%`,
    },
    { key: "result", header: "Result", cell: (row) => RESULT_WORDS[row.result] },
    { key: "leaves-out", header: "Leaves out", cell: leavesOutOf },
    {
      key: "copy",
      header: "Copy",
      cell: (row) => (
        <CopyButton
          value={copyTextOf({
            label: row.label,
            month: row,
            kind: row.kind,
            sampling: row.upstream_sampling,
            reportLabel: row.report_label,
            audience: last.audience,
            zone: last.zone,
          })}
          label={`${monthName(row.month)} for ${row.label}`}
        />
      ),
    },
  ];
  return (
    <div className="flex flex-col gap-3 py-3">
      <p className="max-w-prose text-ink text-sm">{lastMonthHeadline(last)}</p>
      <p className="max-w-prose text-ink-dim text-sm">{NO_SINGLE_FIGURE}</p>
      <p className="max-w-prose text-ink-dim text-sm">{ONE_PROJECT}</p>
      <p className="max-w-prose text-ink-dim text-sm">{last.audience}</p>
      <DataTable
        columns={columns}
        rows={last.rows}
        rowKey={(row) => row.id}
        caption={`Each target in ${monthName(last.month)}`}
        empty="No target held a counted day this month."
      />
      <a
        href={monthsCsvUrl(projectId, MONTHS_KEPT)}
        download
        className="self-start font-medium text-brand text-sm"
      >
        Download complete months as CSV
      </a>
    </div>
  );
}

function TargetCell({ row, zone }: { row: SloMonthRow; zone: string }) {
  const title =
    row.deleted_at === null ? (
      <Link to="/slos/$sloId" params={{ sloId: row.id }} className="text-brand">
        {row.name ?? row.label}
      </Link>
    ) : (
      <span className="text-ink">{row.name ?? row.label}</span>
    );
  return (
    <span className="flex flex-col">
      {title}
      {row.name ? <span className="text-ink-dim">{row.label}</span> : null}
      {row.deleted_at ? (
        <span className="text-ink-dim">Deleted on {dayIn(row.deleted_at, zone)}.</span>
      ) : null}
    </span>
  );
}
