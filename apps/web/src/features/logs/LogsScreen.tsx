import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatCount } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import { formatClockTime, formatTimestamp } from "@/shared/format/time";
import { useDeclaredStream } from "@/shared/refresh";
import { useLiveWindow, useSelection, useWindowPhrase } from "@/shared/selection";
import type { LogSearchFilters, ServiceFacet, SeverityFacet } from "./api";
import { logExportUrl, useLogFacets, useLogSearch } from "./api";
import { bufferLineCount, downloadBuffer } from "./log-export";
import type { LiveBuffer, LogLine, LogRow, TailNarration } from "./log-feed";

import { buildLogRows, describeLogs } from "./log-feed";
import {
  emitterSaidSomethingElse,
  SEVERITY_FILTERS,
  severityFilterById,
  severityLabel,
  severityStyle,
} from "./severity";

/** A service named in the URL, or nothing. A blank string is not a filter. */
export function scopedService(raw: unknown): string | undefined {
  const named = typeof raw === "string" ? raw.trim() : "";
  return named === "" ? undefined : named;
}

import type { LogTail, TailStatus } from "./use-log-tail";
import { useLogTail } from "./use-log-tail";

const LINE_HEIGHT = 26;
const GAP_HEIGHT = 30;
const DETAIL_LINE_HEIGHT = 20;
const DETAIL_PADDING = 20;

/* The message block is sized from the message, because the rows are virtualized:
   the height has to be known before the panel is drawn, and a block tall enough
   for the longest body would leave a hundred pixels of nothing under the median
   one, which is eighty-two characters.

   The estimate is deliberately generous per line, so it errs towards a little
   dead space rather than towards clipping; past six lines it stops growing and
   the block scrolls, which is what keeps a fifteen-thousand-character body from
   turning one row into a page. */
const MESSAGE_CHARS_PER_LINE = 110;
const MESSAGE_MAX_LINES = 6;

function messageHeight(body: string): number {
  const lines = Math.ceil(body.length / MESSAGE_CHARS_PER_LINE);
  return Math.min(Math.max(lines, 1), MESSAGE_MAX_LINES) * DETAIL_LINE_HEIGHT;
}
const OVERSCAN_ROWS = 20;

/** Typing a character must not send a request; the pause before the next one may. */
const SEARCH_DEBOUNCE_MS = 300;

/** Below this the list counts as "at the newest line" and the tail may keep running. */
const AT_TOP_PX = 8;

/* Column widths, declared once so the header and every row cannot drift apart. */
const TIME_COLUMN = "w-24 shrink-0 pr-3 pl-2";
/* `truncate` as well as a width: the Service column had it and this one did not,
   which is why an eleven-character "Information" ran into the service beside it
   rather than being cut. The label is five characters at most now, so the
   truncation should never fire — it is there so that it cannot be the next
   emitter's decision whether this column stays inside itself. */
const SEVERITY_COLUMN = "hidden w-16 shrink-0 truncate pr-3 sm:block";
const SERVICE_COLUMN = "hidden w-44 shrink-0 truncate pr-4 md:block";

/**
 * Every log line this project holds, searchable, with a live tail on the front of it.
 *
 * The route component owns the selections, the queries and the socket; everything
 * visible is in `LogsView`, which is a function of its props.
 */
export function LogsScreen() {
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const windowPhrase = useWindowPhrase();

  const navigate = useNavigate();
  // The route's own id, not its path: `/logs` renders inside the shell. Named
  // `routeSearch` because `search` below is the log search itself.
  const routeSearch = useSearch({ from: "/shell/logs" });

  // In the URL rather than in component state, so arriving from a service lands
  // already narrowed to it (IA1), and so a narrowed view can be handed to a
  // colleague as a link. The typed query stays local: it changes on every
  // keystroke, and a history entry per character is not a history.
  const serviceName = scopedService(routeSearch.service);
  const severityId = severityFilterById(String(routeSearch.severity ?? "")).id;

  const show = (next: { service?: string; severity?: string }) =>
    void navigate({
      to: "/logs",
      search: () => ({
        ...(next.service ? { service: next.service } : {}),
        ...(next.severity && next.severity !== "all" ? { severity: next.severity } : {}),
      }),
      replace: true,
    });

  const [typed, setTyped] = useState("");
  const [expanded, setExpanded] = useState<string | undefined>(undefined);

  const query = useDebounced(typed, SEARCH_DEBOUNCE_MS);
  const filters = useMemo<LogSearchFilters>(
    () => ({ query, serviceName, minSeverity: severityFilterById(severityId).minSeverity }),
    [query, serviceName, severityId],
  );

  const search = useLogSearch(projectId, timeWindow, filters);
  const facets = useLogFacets(projectId, timeWindow);
  const tail = useLogTail(projectId, filters, true);

  return (
    <LogsView
      windowLabel={windowPhrase}
      exportUrl={projectId ? logExportUrl(projectId, timeWindow, filters) : undefined}
      typed={typed}
      onTypedChange={setTyped}
      serviceName={serviceName}
      onServiceChange={(value) => show({ service: value, severity: severityId })}
      severityId={severityId}
      onSeverityChange={(value) => show({ service: serviceName, severity: value })}
      services={facets.data?.services ?? []}
      severities={facets.data?.severities ?? []}
      searched={search.data?.logs ?? []}
      isPending={search.isPending}
      isError={search.isError}
      error={search.error}
      onRetry={() => void search.refetch()}
      hasOlder={search.hasNextPage}
      isLoadingOlder={search.isFetchingNextPage}
      onLoadOlder={() => void search.fetchNextPage()}
      tail={tail}
      expanded={expanded}
      onToggleLine={(key) => setExpanded((open) => (open === key ? undefined : key))}
    />
  );
}

/** Holds the last value still, so a keystroke does not become a request. */
function useDebounced(value: string, delayMs: number): string {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return settled;
}

interface LogsViewProps {
  windowLabel: string;
  /** Absent until a project is chosen; there is nothing to export from before then. */
  exportUrl?: string;
  typed: string;
  onTypedChange: (value: string) => void;
  serviceName?: string;
  onServiceChange: (value: string | undefined) => void;
  severityId: string;
  onSeverityChange: (id: string) => void;
  services: readonly ServiceFacet[];
  severities: readonly SeverityFacet[];
  searched: readonly LogLine[];
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  hasOlder: boolean;
  isLoadingOlder: boolean;
  onLoadOlder: () => void;
  tail: LogTail;
  expanded?: string;
  onToggleLine: (key: string) => void;
}

/** Everything the screen shows, with no query client and no socket behind it. */
export function LogsView({
  windowLabel,
  exportUrl,
  typed,
  onTypedChange,
  serviceName,
  onServiceChange,
  severityId,
  onSeverityChange,
  services,
  severities,
  searched,
  isPending,
  isError,
  error,
  onRetry,
  hasOlder,
  isLoadingOlder,
  onLoadOlder,
  tail,
  expanded,
  onToggleLine,
}: LogsViewProps) {
  const rows = buildLogRows(tail.buffer, searched);

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => rowHeight(rows[index], expanded),
    overscan: OVERSCAN_ROWS,
    /* The open row is a different item, not the same item at a different height.
       Opening a line changes no row *count*, so a virtualizer keyed on the line alone
       keeps the cached height and every row below draws on top of the detail — seen in
       a browser, invisible to a test. Folding the state into the key is what makes the
       measurement stale in the one place it should be. */
    getItemKey: (index) => measurementKey(rows[index], expanded),
  });

  /* Reading an older line while new ones arrive on top would move it under the eye,
     so leaving the newest line pauses the tail rather than fighting the reader. */
  const { pause, resume, status } = tail;
  const onScroll = useCallback(() => {
    const scrolled = scrollRef.current?.scrollTop ?? 0;
    if (scrolled > AT_TOP_PX && status === "live") pause();
  }, [pause, status]);

  const followNewest = useCallback(() => {
    scrollRef.current?.scrollTo({ top: 0 });
    resume();
  }, [resume]);

  /* The tail is this screen's refresh, so the top bar's one control shows it —
     rather than an interval picker governing a facet count nobody is watching. */
  useDeclaredStream(status, pause, followNewest);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Logs</h1>
          <ExportControls exportUrl={exportUrl} buffer={tail.buffer} />
        </div>
        <p className="mt-1 text-ink-dim text-sm">
          {isPending || isError
            ? `Every log line this project holds, over ${windowLabel}.`
            : describeLogs(
                rows.length,
                windowLabel,
                narrate(tail.status),
                tail.buffer.droppedTotal,
              )}
        </p>
      </header>

      <FilterBar
        typed={typed}
        onTypedChange={onTypedChange}
        serviceName={serviceName}
        onServiceChange={onServiceChange}
        severityId={severityId}
        onSeverityChange={onSeverityChange}
        services={services}
        severities={severities}
      />

      {tail.status === "failed" && tail.failure ? (
        <p className="shrink-0 border-warning-solid border-l-2 px-4 py-2 text-ink-dim text-sm">
          {tail.failure} Stored lines below are unaffected.
        </p>
      ) : null}

      {isPending ? (
        <div className="px-4">
          <SkeletonRows rows={14} label="Loading logs" />
        </div>
      ) : isError ? (
        <div className="px-4">
          <InlineFailure title="Logs could not be loaded" error={error} onRetry={onRetry} />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          title="No log lines match"
          detail="Nothing in this window matches the search and filters above. Widen the time range, clear a filter, or check that your services are exporting logs — an uninstrumented logger writes nothing here."
        />
      ) : (
        <>
          <ColumnHeader />

          <div
            ref={scrollRef}
            onScroll={onScroll}
            className="min-h-0 flex-1 overflow-y-auto font-mono text-2xs"
          >
            <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
              {virtualizer.getVirtualItems().map((item) => (
                <VirtualRow
                  key={item.key}
                  row={rows[item.index]}
                  offsetTop={item.start}
                  height={item.size}
                  expanded={expanded === rows[item.index].key}
                  onToggle={onToggleLine}
                />
              ))}
            </div>

            {hasOlder ? (
              <div className="flex justify-center border-line-subtle border-t py-4">
                <Button
                  variant="secondary"
                  pending={isLoadingOlder}
                  pendingLabel="Loading…"
                  onClick={onLoadOlder}
                >
                  Load older lines
                </Button>
              </div>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}

/** A row's identity for measurement: what it is, and whether it is open. */
function measurementKey(row: LogRow, expanded: string | undefined): string {
  return row.key === expanded ? `${row.key}:open` : row.key;
}

export function rowHeight(row: LogRow, expanded: string | undefined): number {
  if (row.kind === "gap") return GAP_HEIGHT;
  if (row.key !== expanded) return LINE_HEIGHT;

  const detailLines =
    Object.keys(row.line.attributes).length +
    (row.line.trace_id ? 1 : 0) +
    (emitterSaidSomethingElse(row.line) ? 1 : 0);
  return (
    LINE_HEIGHT +
    DETAIL_PADDING +
    messageHeight(row.line.body) +
    (detailLines === 0 ? 1 : detailLines) * DETAIL_LINE_HEIGHT
  );
}

/**
 * Whether the tail is running, as a sentence with a switch in it — the same idiom as
 * the top bar's `PROJECT production ⌄` rather than a form control dropped on the page.
 */
/**
 * Two exports, because neither is the whole answer.
 *
 * **Records reach ClickHouse through the pipeline, so the stored search runs behind
 * the tail.** A file read from storage is complete for the window and missing the
 * newest lines; a file built from the buffer has the newest lines and reaches back
 * only as far as the buffer does. Offering one and calling it "export" would be a
 * silent choice about which of those a reader gets.
 *
 * So both are offered, each labelled with what it will actually contain — the
 * buffer one with its own line count, so nobody presses it expecting the window.
 */
function ExportControls({
  exportUrl,
  buffer,
}: {
  exportUrl: string | undefined;
  buffer: LiveBuffer;
}) {
  const held = bufferLineCount(buffer);

  return (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs">
      {exportUrl ? (
        <a href={exportUrl} className="text-ink-dim underline underline-offset-4 hover:text-ink">
          Download this window as CSV
        </a>
      ) : null}

      <button
        type="button"
        disabled={held === 0}
        onClick={() => downloadBuffer(buffer, "nexovue-logs-on-screen.csv")}
        className="text-ink-dim underline underline-offset-4 hover:text-ink disabled:cursor-not-allowed disabled:no-underline disabled:opacity-50"
      >
        Download the {formatCount(held)} {plural(held, "line", "lines")} on screen
      </button>
    </div>
  );
}

/** The socket has five states; the sentence under the heading has words for three. */
function narrate(status: TailStatus): TailNarration {
  if (status === "live") return "live";
  return status === "paused" ? "paused" : "off";
}

function FilterBar({
  typed,
  onTypedChange,
  serviceName,
  onServiceChange,
  severityId,
  onSeverityChange,
  services,
  severities,
}: {
  typed: string;
  onTypedChange: (value: string) => void;
  serviceName?: string;
  onServiceChange: (value: string | undefined) => void;
  severityId: string;
  onSeverityChange: (id: string) => void;
  services: readonly ServiceFacet[];
  severities: readonly SeverityFacet[];
}) {
  return (
    <div className="flex shrink-0 flex-wrap items-baseline gap-x-5 gap-y-2 px-4 pb-3">
      <input
        type="search"
        value={typed}
        onChange={(event) => onTypedChange(event.target.value)}
        placeholder="Search message text"
        aria-label="Search message text"
        className="min-w-0 flex-1 rounded-sm border border-line bg-surface px-2.5 py-1 font-mono text-ink text-xs outline-none placeholder:text-ink-dim hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      />

      {/* Native selects, deliberately: keyboard navigation, typeahead and the mobile
          picker all keep working, and there is nothing here a listbox would add. */}
      {/* The counts are the point. A facet is not a list of options — it is
          what the window actually holds, counted with no filters applied, so a
          reader picks the service with 4,000 records rather than discovering
          after two clicks that the one they picked has three. A service absent
          from the facets sent nothing here and is not offered at all. */}
      <InlineSelect
        label="Service"
        value={serviceName ?? ""}
        onChange={(value) => onServiceChange(value || undefined)}
        options={serviceOptions(services, serviceName)}
      />

      <InlineSelect
        label="Severity"
        value={severityId}
        onChange={onSeverityChange}
        options={SEVERITY_FILTERS.map((filter) => ({
          value: filter.id,
          label: countedSeverity(filter, severities),
        }))}
      />
    </div>
  );
}

/**
 * The services this window holds, with the one already chosen kept in the list.
 *
 * A native select shows its first option when none matches the value, so a
 * service that sent no logs this hour — arriving from its own screen, or from a
 * shared link — would leave the control reading "All" while the results were
 * narrowed to it. The control saying one thing and the list another is worse
 * than either answer alone, and it is how a reader concludes the product is
 * broken. The kept entry carries no count, because there is nothing to count.
 */
export function serviceOptions(
  facets: readonly ServiceFacet[],
  selected: string | undefined,
): { value: string; label: string }[] {
  const counted = facets.map((facet) => ({
    value: facet.service_name,
    label: `${facet.service_name} · ${formatCount(facet.records)}`,
  }));
  const known = selected === undefined || facets.some((f) => f.service_name === selected);
  return [
    { value: "", label: "All" },
    ...(known ? [] : [{ value: selected, label: selected }]),
    ...counted,
  ];
}

/**
 * A severity option with what the window holds behind it.
 *
 * A severity filter is a floor — "warning and above" — so its count is the sum
 * of every severity at or above it, not the count of that one name. Counting
 * only the exact level would say "warning 3" on a window holding three warnings
 * and four hundred errors, which is the opposite of what the filter returns.
 *
 * "All" carries no count: it is not a narrowing, and a total beside it competes
 * with the caption that already says how many records there are.
 */
export function countedSeverity(
  filter: (typeof SEVERITY_FILTERS)[number],
  severities: readonly SeverityFacet[],
): string {
  if (filter.minSeverity === undefined) return filter.label;
  const floor = filter.minSeverity;
  const records = severities
    .filter((facet) => facet.severity_number >= floor)
    .reduce((running, facet) => running + facet.records, 0);
  return records > 0 ? `${filter.label} · ${formatCount(records)}` : filter.label;
}

function InlineSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly { value: string; label: string }[];
}) {
  return (
    <label className="flex items-baseline gap-2">
      <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="max-w-44 cursor-pointer appearance-none truncate rounded-sm border-none bg-transparent py-0.5 pr-1 pl-1 font-medium text-ink text-sm outline-none hover:bg-hover focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function ColumnHeader() {
  return (
    <div className="flex shrink-0 items-center border-line border-b bg-surface px-4 py-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
      <span className={TIME_COLUMN}>Time</span>
      <span className={SEVERITY_COLUMN}>Level</span>
      <span className={SERVICE_COLUMN}>Service</span>
      <span className="min-w-0 flex-1">Message</span>
    </div>
  );
}

function VirtualRow({
  row,
  offsetTop,
  height,
  expanded,
  onToggle,
}: {
  row: LogRow;
  offsetTop: number;
  height: number;
  expanded: boolean;
  onToggle: (key: string) => void;
}) {
  return (
    <div
      className="absolute inset-x-0 top-0"
      style={{ height, transform: `translateY(${offsetTop}px)` }}
    >
      {row.kind === "gap" ? (
        <GapRow dropped={row.dropped} />
      ) : (
        <LogLineRow line={row.line} expanded={expanded} onToggle={() => onToggle(row.key)} />
      )}
    </div>
  );
}

/**
 * Where lines were lost.
 *
 * A tail that silently skips lines is worse than one that admits a hole: the person is
 * watching precisely because they need to trust that what is on screen is what
 * happened. Warning rather than critical — nothing is broken, but the record is
 * incomplete, and only the reader can decide whether that matters.
 */
function GapRow({ dropped }: { dropped: number }) {
  return (
    <div className="flex h-full items-center border-line-subtle border-b border-l-2 border-l-warning-solid bg-surface px-4">
      <span className="pl-2 text-warning-text">
        {formatCount(dropped)} {plural(dropped, "line")} dropped — the tail fell behind here
      </span>
    </div>
  );
}

function LogLineRow({
  line,
  expanded,
  onToggle,
}: {
  line: LogLine;
  expanded: boolean;
  onToggle: () => void;
}) {
  const tone = severityStyle(line.severity_number);

  return (
    <div className={`h-full border-line-subtle border-b border-l-2 ${tone.rule}`}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center px-4 text-left hover:bg-hover"
        style={{ height: LINE_HEIGHT }}
      >
        <span className={`${TIME_COLUMN} text-ink-dim`} title={formatTimestamp(line.observed_at)}>
          {formatClockTime(line.observed_at)}
        </span>
        <span
          className={`${SEVERITY_COLUMN} uppercase ${tone.label}`}
          title={emitterSaidSomethingElse(line) ? `Sent as “${line.severity}”` : undefined}
        >
          {severityLabel(line.severity_number)}
        </span>
        <span className={`${SERVICE_COLUMN} text-ink-dim`}>{line.service_name}</span>
        <span className={`min-w-0 flex-1 truncate ${tone.body}`} title={line.body}>
          {line.body}
        </span>
      </button>

      {expanded ? <LineDetail line={line} /> : null}
    </div>
  );
}

/**
 * What a line carries beyond its message: its attributes, and the trace it belongs to.
 *
 * The trace link is the whole reason logs and traces live in one product, so it is the
 * one thing here that reads as a link rather than as data.
 */
function LineDetail({ line }: { line: LogLine }) {
  const attributes = Object.entries(line.attributes);

  return (
    <dl className="space-y-0.5 bg-surface px-4 py-2.5 pl-26">
      {/* **The message, in full.** The row truncates it to one line, and this panel
          used to show the trace and the attributes and not the thing the line
          actually says — so the longest body in this project, 15,193 characters,
          could be read nowhere in the product. It wraps here, and scrolls past the
          height below rather than growing: the rows are virtualized, so a panel
          whose height depends on its content would be measured wrong for every row
          under it. */}
      <div className="flex gap-3" style={{ height: messageHeight(line.body) }}>
        <dt className="w-28 shrink-0 text-ink-dim uppercase tracking-caps">Message</dt>
        <dd className="min-w-0 flex-1 overflow-y-auto whitespace-pre-wrap break-words text-ink">
          {line.body}
        </dd>
      </div>

      {emitterSaidSomethingElse(line) ? (
        <div className="flex gap-3" style={{ height: DETAIL_LINE_HEIGHT }}>
          <dt className="w-28 shrink-0 text-ink-dim uppercase tracking-caps">Sent as</dt>
          <dd className="min-w-0 truncate text-ink">{line.severity}</dd>
        </div>
      ) : null}

      {line.trace_id ? (
        <div className="flex gap-3" style={{ height: DETAIL_LINE_HEIGHT }}>
          <dt className="w-28 shrink-0 text-ink-dim uppercase tracking-caps">Trace</dt>
          <dd className="min-w-0 truncate">
            <Link
              to="/traces/$traceId"
              params={{ traceId: line.trace_id }}
              className="text-brand-text underline-offset-2 hover:underline"
            >
              {line.trace_id}
            </Link>
          </dd>
        </div>
      ) : null}

      {attributes.length === 0 && !line.trace_id ? (
        <p className="text-ink-dim" style={{ height: DETAIL_LINE_HEIGHT }}>
          No attributes, and no trace context — this line was written outside any span.
        </p>
      ) : null}

      {attributes.map(([name, value]) => (
        <div key={name} className="flex gap-3" style={{ height: DETAIL_LINE_HEIGHT }}>
          <dt className="w-28 shrink-0 truncate text-ink-dim">{name}</dt>
          <dd className="min-w-0 truncate text-ink">{formatAttribute(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function formatAttribute(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}
