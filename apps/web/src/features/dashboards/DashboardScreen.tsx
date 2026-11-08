import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import { ApiError, isNotFound, isRateLimited } from "@/shared/api-client";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { CHART_HEIGHT } from "@/shared/components/TrendChart";
import { detectionHeadlineFor } from "@/shared/detection/headline";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import { formatZonedTime, zoneLabel } from "@/shared/format/zoned";
import { useCan } from "@/shared/identity";
import { useSelection } from "@/shared/selection";

import type { Dashboard, DashboardContext, PanelReadResponse, StoredPanel } from "./api";
import { refreshIntervalOf, useDashboard, useDashboardContext, useDashboardPanels } from "./api";
import type { KeptReadings } from "./kept-readings";
import { allFailing, keptLine, nextExpiry, nextKept } from "./kept-readings";
import type { GridSize } from "./layout-rows";
import { layoutRows } from "./layout-rows";
import { NotePanel } from "./NotePanel";
import { CorrelationId, PanelFrame, PanelWords, StaleStrip } from "./PanelFrame";
import { panelTitle, signalOf, stateWords, TIME_KINDS, transient, unread } from "./panel-words";
import { TimePanel } from "./TimePanel";

/**
 * A saved dashboard, read in one charged batch (F20, Step 2).
 *
 * Two reads a refresh: the panels, and the panel context, which is Postgres alone, so the
 * header, the notes and detection's line draw before the scans finish and through any
 * ClickHouse outage (D24). Each panel owns its state; what affects the whole page is said
 * once, above the grid (FR48). A panel that could not be read never draws as a flat line
 * (BR5): it keeps an earlier line for one interval under a filled strip, then words alone.
 */

const GRID_SIZES: readonly GridSize[] = ["lg", "xl"];
// Literal classes, so Tailwind generates each one.
const SPAN_CLASS: Record<GridSize, Record<number, string>> = {
  base: { 12: "col-span-12" },
  lg: {
    1: "lg:col-span-1",
    2: "lg:col-span-2",
    3: "lg:col-span-3",
    4: "lg:col-span-4",
    5: "lg:col-span-5",
    6: "lg:col-span-6",
    7: "lg:col-span-7",
    8: "lg:col-span-8",
    9: "lg:col-span-9",
    10: "lg:col-span-10",
    11: "lg:col-span-11",
    12: "lg:col-span-12",
  },
  xl: {
    1: "xl:col-span-1",
    2: "xl:col-span-2",
    3: "xl:col-span-3",
    4: "xl:col-span-4",
    5: "xl:col-span-5",
    6: "xl:col-span-6",
    7: "xl:col-span-7",
    8: "xl:col-span-8",
    9: "xl:col-span-9",
    10: "xl:col-span-10",
    11: "xl:col-span-11",
    12: "xl:col-span-12",
  },
};

export function DashboardScreen({ dashboardId }: { dashboardId: string }) {
  const { projectId, window: choice } = useSelection();
  const dashboard = useDashboard(dashboardId);
  const context = useDashboardContext(dashboard.data, projectId, choice);
  const panels = useDashboardPanels(dashboard.data, projectId, choice);
  const canEdit = useCan("edit_dashboards");
  const [kept, setKept] = useState<KeptReadings>({});
  const [lastGoodAt, setLastGoodAt] = useState<string | undefined>();
  // The moment the page last judged its lines: each read, and each line's expiry.
  const [now, setNow] = useState(() => Date.now());
  const intervalMs = dashboard.data ? refreshIntervalOf(dashboard.data.definition, choice) : false;

  // Each answer, or each failed batch, moves the kept lines on once (FR31). Keyed by when
  // it arrived, not by its value: an equal answer is the same object, and must still count.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the update times are the trigger
  useEffect(() => {
    const at = Date.now();
    setNow(at);
    if (panels.isSuccess) {
      setKept((previous) => nextKept(previous, panels.data, at));
      const newest = newestGoodRead(panels.data);
      if (newest) setLastGoodAt(newest);
    } else if (panels.isError) {
      setKept((previous) => allFailing(previous, at));
    }
  }, [panels.dataUpdatedAt, panels.errorUpdatedAt]);

  // An old line goes when its time is up, refresh or none (FR31).
  useEffect(() => {
    const expiry = nextExpiry(kept, intervalMs, now);
    if (expiry === undefined) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.max(expiry - Date.now(), 0));
    return () => clearTimeout(timer);
  }, [kept, intervalMs, now]);

  if (dashboard.isPending) return <SkeletonRows rows={6} label="Loading the dashboard" />;
  if (dashboard.isError) {
    return (
      <PageState
        words={
          isNotFound(dashboard.error)
            ? "This dashboard does not exist or is no longer available."
            : "Could not read this dashboard."
        }
      />
    );
  }
  if (context.isError && isNotFound(context.error)) {
    return <PageState words="This link is for a project you cannot open." />;
  }

  return (
    <DashboardView
      dashboard={dashboard.data}
      context={context.data}
      answer={panels.data}
      batchError={panels.isError ? panels.error : undefined}
      pending={panels.isPending}
      kept={kept}
      now={now}
      lastGoodAt={lastGoodAt}
      intervalMs={intervalMs}
      canEdit={canEdit}
      onReadAgain={() => void panels.refetch()}
    />
  );
}

function newestGoodRead(answer: PanelReadResponse): string | undefined {
  const reads = Object.values(answer.panels).flatMap((result) =>
    result.state === "ok" ? [result.read_at] : [],
  );
  return reads.sort().at(-1);
}

function PageState({ words }: { words: string }) {
  return (
    <div className="flex flex-col gap-2 p-6">
      <p className="text-ink">{words}</p>
      <Link to="/dashboards" className="text-brand text-sm underline">
        All dashboards
      </Link>
    </div>
  );
}

interface ViewProps {
  dashboard: Dashboard;
  context: DashboardContext | undefined;
  answer: PanelReadResponse | undefined;
  batchError: unknown;
  pending: boolean;
  kept: KeptReadings;
  now: number;
  lastGoodAt: string | undefined;
  intervalMs: number | false;
  canEdit: boolean;
  onReadAgain: () => void;
}

function DashboardView(props: ViewProps) {
  const { dashboard, context, answer, canEdit } = props;
  const zone = context?.project.timezone ?? "UTC";
  const scanPanels = dashboard.definition.panels.filter((panel) => panel.kind !== "note");
  const unreadIds = answer
    ? scanPanels.filter((panel) => {
        const result = answer.panels[panel.id];
        return result !== undefined && unread(result);
      })
    : [];
  const spans = useMemo(() => spansOf(dashboard.definition.panels), [dashboard.definition.panels]);
  const detection = context
    ? detectionHeadlineFor(context.detection, { zone: context.project.timezone })
    : undefined;

  return (
    <div className="flex flex-col gap-3 p-4">
      <Header dashboard={dashboard} context={context} />
      <PageLine {...props} zone={zone} scanCount={scanPanels.length} />
      {detection ? (
        <p className={detection.wrong ? "text-critical-text text-sm" : "text-ink-dim text-sm"}>
          {detection.title}
        </p>
      ) : null}
      {unreadIds.length > 0 ? (
        <button
          type="button"
          className="self-start text-sm text-warning-text underline"
          onClick={() => document.getElementById(`panel-${unreadIds[0].id}`)?.focus()}
        >
          {unreadIds.length} of {dashboard.definition.panels.length} panels could not be read
        </button>
      ) : null}
      <div className="grid grid-cols-12 gap-3">
        {dashboard.definition.panels.map((panel) => (
          <PanelCell
            key={panel.id}
            panel={panel}
            span={spans[panel.id] ?? ""}
            {...props}
            zone={zone}
            overLimit={
              answer
                ? Object.values(answer.panels).filter((result) => result.state === "over_limit")
                    .length
                : 0
            }
            canEdit={canEdit}
          />
        ))}
      </div>
    </div>
  );
}

function spansOf(panels: readonly StoredPanel[]): Record<string, string> {
  const shaped = panels.map((panel) => ({
    id: panel.id,
    width: panel.width,
    shape: TIME_KINDS.has(panel.kind) ? ("time" as const) : ("text" as const),
  }));
  const classes: Record<string, string[]> = {};
  for (const size of GRID_SIZES) {
    for (const row of layoutRows(shaped, size)) {
      for (const cell of row) {
        classes[cell.id] = [...(classes[cell.id] ?? []), SPAN_CLASS[size][cell.span]];
      }
    }
  }
  return Object.fromEntries(Object.entries(classes).map(([id, names]) => [id, names.join(" ")]));
}

function Header({
  dashboard,
  context,
}: {
  dashboard: Dashboard;
  context: DashboardContext | undefined;
}) {
  return (
    <header className="flex flex-col gap-1">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="font-semibold text-ink text-lg">{visibleEscapes(dashboard.title)}</h1>
        {context ? (
          // The project comes from the data, never from a title (BR13).
          <p className="text-ink-dim text-sm">
            {visibleEscapes(context.project.name)} · times in{" "}
            {zoneLabel(context.project.timezone, new Date())}
          </p>
        ) : null}
      </div>
      {dashboard.description ? (
        <p className="whitespace-pre-wrap text-ink-dim text-sm">
          {visibleEscapes(dashboard.description, { keepLineBreaks: true })}
        </p>
      ) : null}
      <p className="text-ink-dim text-xs">
        Owner: {dashboard.owner ? visibleEscapes(dashboard.owner) : "Former member"}
      </p>
      {dashboard.definition.links.length > 0 ? (
        <ul className="flex flex-wrap gap-3 text-sm">
          {dashboard.definition.links.map((link) => (
            <li key={link.url}>
              {/* The host beside the label: a label alone says nothing about where it goes. */}
              <a
                href={link.url}
                rel="noopener noreferrer"
                target="_blank"
                className="text-brand underline"
              >
                {visibleEscapes(link.label)}
              </a>{" "}
              <span className="text-ink-dim text-xs">{hostOf(link.url)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </header>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
}

/**
 * What affects the whole page, said once: a whole-request refusal in FR45's words, the
 * browser giving up at 25 s, or telemetry that could not be read at all (AC61, AC145).
 */
function PageLine(props: ViewProps & { zone: string; scanCount: number }) {
  const { answer, batchError, lastGoodAt, zone, scanCount, onReadAgain } = props;
  const since = lastGoodAt ? ` since ${formatZonedTime(lastGoodAt, { zone })}` : "";
  if (batchError instanceof ApiError) {
    if (isRateLimited(batchError)) {
      return (
        <Line words="Too many reads just now." correlationId={batchError.context.correlationId} />
      );
    }
    if (batchError.context.aborted) {
      return (
        <Line
          words="The page did not answer within 25 seconds."
          correlationId={batchError.context.correlationId}
          onReadAgain={onReadAgain}
        />
      );
    }
    return (
      <Line
        words={`Could not read telemetry${since}. Notes and findings below are current.`}
        correlationId={batchError.context.correlationId}
        onReadAgain={onReadAgain}
      />
    );
  }
  const results = answer
    ? Object.values(answer.panels).filter((result) => result.state !== "unknown_kind")
    : [];
  if (answer && scanCount > 0 && results.length > 0 && results.every(transient)) {
    return (
      <Line
        words={`Could not read telemetry${since}. Notes and findings below are current.`}
        correlationId={answer.correlation_id}
        onReadAgain={onReadAgain}
      />
    );
  }
  return null;
}

function Line({
  words,
  correlationId,
  onReadAgain,
}: {
  words: string;
  correlationId?: string | null;
  onReadAgain?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-sm border border-warning-border bg-warning-subtle px-3 py-2 text-sm text-warning-text"
    >
      <p>{words}</p>
      {onReadAgain ? (
        <button type="button" onClick={onReadAgain} className="underline">
          Read again
        </button>
      ) : null}
      {correlationId ? <CorrelationId value={correlationId} /> : null}
    </div>
  );
}

function PanelCell(
  props: ViewProps & { panel: StoredPanel; span: string; zone: string; overLimit: number },
) {
  const { panel, span, answer, pending, kept, now, intervalMs, context, zone, canEdit, dashboard } =
    props;
  const title = panelTitle(panel);
  const frame = (body: ReactNode) => (
    <PanelFrame panelId={panel.id} service={title.service} title={title.text} span={span}>
      {body}
    </PanelFrame>
  );

  if (panel.kind === "note") {
    const text = typeof panel.settings.text === "string" ? panel.settings.text : "";
    return frame(<NotePanel text={text} />);
  }
  const result = answer?.panels[panel.id];
  if (result === undefined) {
    // Loading, or the whole batch failed: an earlier line may stay, words otherwise.
    const old = keptLine(kept[panel.id], intervalMs, now);
    if (old && props.batchError) return frame(<KeptChart {...props} reading={old} />);
    if (pending) {
      return frame(
        <div
          aria-hidden
          className="animate-pulse rounded-xs bg-raised"
          style={{ height: CHART_HEIGHT }}
        />,
      );
    }
    return frame(<PanelWords words="Could not read this." />);
  }
  if (result.state === "ok") {
    const signal = signalOf(panel);
    return frame(
      signal ? (
        <TimePanel
          reading={result}
          signal={signal}
          zone={zone}
          description={`${title.service ? `${title.service} · ` : ""}${title.text} across the window.`}
        />
      ) : (
        <PanelWords words="This panel type is no longer available." />
      ),
    );
  }
  const words =
    stateWords(result, panel, {
      canEdit,
      owner: dashboard.owner,
      projectName: context?.project.name ?? "this project",
      overLimit: props.overLimit,
    }) ?? "";
  const old = transient(result) ? keptLine(kept[panel.id], intervalMs, now) : undefined;
  if (old) return frame(<KeptChart {...props} reading={old} />);
  return frame(
    <PanelWords
      words={words}
      correlationId={transient(result) ? answer?.correlation_id : undefined}
      onReadAgain={transient(result) ? props.onReadAgain : undefined}
    />,
  );
}

function KeptChart(
  props: ViewProps & {
    panel: StoredPanel;
    zone: string;
    reading: NonNullable<ReturnType<typeof keptLine>>;
  },
) {
  const { panel, zone, reading } = props;
  const signal = signalOf(panel);
  return (
    <>
      <StaleStrip since={formatZonedTime(reading.read_at, { zone })} />
      {signal ? (
        <TimePanel
          reading={reading}
          signal={signal}
          zone={zone}
          description="An earlier reading."
        />
      ) : null}
    </>
  );
}
