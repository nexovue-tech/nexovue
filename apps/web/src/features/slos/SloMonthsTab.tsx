import { Link } from "@tanstack/react-router";

import { CopyButton } from "@/shared/components/CopyButton";
import { type Column, DataTable } from "@/shared/components/DataTable";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";

import type { SloMonth, SloMonths, SloOutage, SloView } from "./api";
import { monthsCsvUrl, useSloMonths } from "./api";
import {
  copyTextOf,
  differentPeriodsOf,
  MONTHS_KEPT,
  monthName,
  monthNotes,
  monthSentence,
  outageLine,
  RESULT_WORDS,
} from "./month-words";
import { durationOf } from "./slo-words";

const grouped = new Intl.NumberFormat("en-GB");

/**
 * *By month* on a target's page (F19 FR27, FR27a, FR27c, FR27b; AC77, AC78, AC117): each
 * month's sentence first, what it leaves out, a copy action, then the raw counts beneath.
 */
export function SloMonthsTab({ view }: { view: SloView }) {
  const months = useSloMonths(view.id);
  if (months.isPending) return <SkeletonRows rows={3} label="Loading the months" />;
  if (months.isError) {
    return (
      <InlineFailure
        title="The months could not be loaded"
        error={months.error}
        onRetry={() => months.refetch()}
      />
    );
  }
  const shown = months.data.months.slice(0, MONTHS_KEPT);
  if (shown.length === 0) {
    return (
      <p className="py-3 text-ink-dim text-sm">
        No month holds a counted day yet. Months build up from the day figures start.
      </p>
    );
  }
  const latestComplete = shown.find((each) => each.month !== currentMonthIn(months.data.zone));
  const periods = latestComplete ? differentPeriodsOf(view, latestComplete) : null;
  return (
    <div className="flex flex-col gap-4 py-3">
      <p className="max-w-prose text-ink-dim text-sm">{months.data.audience}</p>
      {periods ? <p className="max-w-prose text-ink text-sm">{periods}</p> : null}
      <ol className="flex flex-col divide-y divide-line">
        {shown.map((each) => (
          <MonthItem key={each.month} month={each} months={months.data} />
        ))}
      </ol>
      <a
        href={monthsCsvUrl(view.project_id, MONTHS_KEPT, view.id)}
        download
        className="self-start font-medium text-brand text-sm"
      >
        Download complete months as CSV
      </a>
      <RawCounts months={shown} kind={view.kind} />
    </div>
  );
}

/** This month's first day in the project's zone, the month still under way. */
function currentMonthIn(zone: string): string {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: zone, dateStyle: "short" }).format(
    new Date(),
  );
  return `${today.slice(0, 7)}-01`;
}

function MonthItem({ month, months }: { month: SloMonth; months: SloMonths }) {
  const notes = monthNotes(month, months.kind, months.zone);
  const copied = copyTextOf({
    label: months.label,
    month,
    kind: months.kind,
    sampling: months.upstream_sampling,
    reportLabel: months.report_label,
    audience: months.audience,
    zone: months.zone,
  });
  return (
    <li data-month={month.month} className="flex flex-col gap-1 py-3 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold text-ink">
          {monthName(month.month)} · {RESULT_WORDS[month.result]}
        </h3>
        <CopyButton value={copied} label={monthName(month.month)} />
      </div>
      <p className="max-w-prose text-ink">{monthSentence(month, months.kind)}</p>
      {month.outages.length > 0 || notes.length > 0 ? (
        <ul className="flex max-w-prose flex-col gap-1 text-ink-dim">
          {month.outages.map((outage) => (
            <li key={outage.id}>
              {outageLine(outage, months.zone)} <OutageLink outage={outage} />
            </li>
          ))}
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function OutageLink({ outage }: { outage: SloOutage }) {
  if (outage.kind === "traffic_finding") {
    return (
      <Link to="/findings/$findingId" params={{ findingId: outage.id }} className="text-brand">
        Open the finding
      </Link>
    );
  }
  return (
    <Link
      to="/alerts/incidents/$incidentId"
      params={{ incidentId: outage.id }}
      className="text-brand"
    >
      Open the incident
    </Link>
  );
}

function RawCounts({ months, kind }: { months: SloMonth[]; kind: SloView["kind"] }) {
  const isCheck = kind === "check_availability";
  const columns: Column<SloMonth>[] = [
    { key: "month", header: "Month", cell: (row) => monthName(row.month) },
    { key: "good", header: "Good", numeric: true, cell: (row) => grouped.format(row.good) },
    { key: "total", header: "All", numeric: true, cell: (row) => grouped.format(row.total) },
    {
      key: "measured",
      header: "Measured",
      numeric: true,
      cell: (row) => (row.coverage === null ? "—" : `${Math.floor(row.coverage * 100)}%`),
    },
    isCheck
      ? {
          key: "missed",
          header: "Runs missed",
          numeric: true,
          cell: (row) => grouped.format(row.missed_runs ?? 0),
        }
      : {
          key: "quiet",
          header: "Quiet",
          numeric: true,
          cell: (row) => durationOf(row.quiet_minutes),
        },
    {
      key: "lost",
      header: "May be lost",
      numeric: true,
      cell: (row) => durationOf(row.lost_minutes),
    },
    {
      key: "excluded",
      header: "Left out",
      numeric: true,
      cell: (row) => durationOf(row.excluded_minutes),
    },
    {
      key: "days",
      header: "Days",
      numeric: true,
      cell: (row) => `${row.days_held} of ${row.days_in_month}`,
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={months}
      rowKey={(row) => row.month}
      caption="Each month's counts"
      empty="No month holds a counted day yet."
    />
  );
}
