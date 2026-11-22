import { Link, useRouter } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  isDashboardNotFound,
  isNotFound,
  isProjectNotFound,
  isRateLimited,
} from "@/shared/api-client";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { CHART_HEIGHT } from "@/shared/components/TrendChart";
import { detectionHeadlineFor } from "@/shared/detection/headline";
import { roundedOut } from "@/shared/drag-window";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import { formatZonedTime, zoneLabel } from "@/shared/format/zoned";
import { useCan } from "@/shared/identity";
import { useProjects } from "@/shared/projects";
import type { FixedIntervalRefresh } from "@/shared/refresh";
import { useDeclaredRefresh, useRefreshPreference } from "@/shared/refresh";
import {
  readRememberedSelection,
  resolveChoice,
  TIME_RANGES,
  windowCacheKey,
} from "@/shared/selection";
import { setupDocUrl } from "@/shared/setup-docs";
import type { Dashboard, DashboardContext, PanelReadResponse, StoredPanel } from "./api";
import {
  effectiveIntervalMs,
  newRefusalStreak,
  useDashboard,
  useDashboardContext,
  useDashboardPanels,
  WHOLE_REQUEST_RETRIES,
} from "./api";
import { usePanelCatchUp } from "./catch-up";
import {
  describeTimes,
  exactTimesFirst,
  exactTimesLink,
  type LinkTo,
  liveLink,
  liveWords,
} from "./copy-link";
import { ExportFile } from "./FileTransfer";
import { gridSpans } from "./grid-spans";
import type { KeepFor, KeptReadings } from "./kept-readings";
import { allFailing, keptLine, nextExpiry, nextKept } from "./kept-readings";
import { NotePanel } from "./NotePanel";
import { OpenFindingsPanel } from "./OpenFindingsPanel";
import { PageLine, StickyLine } from "./PageLine";
import { PanelFrame, PanelWords, StaleStrip } from "./PanelFrame";
import { PanelReading } from "./PanelReading";
import type { LineAction, PageLineInput, PageLines } from "./page-lines";
import {
  pageLines,
  RECENT_END_MS,
  STILL_READING_AFTER_MS,
  staleAfterMs,
  usesPicker,
  widerRange,
} from "./page-lines";
import { marksFor } from "./panel-marks";
import {
  KNOWN_KINDS,
  panelTitle,
  serviceOf,
  stateWords,
  TIME_KINDS,
  transient,
  unread,
} from "./panel-words";
import { ServicePicker } from "./ServicePicker";
import { chartHeight, syncKeyOf } from "./time-lines";
import { useDashboardAddress } from "./use-dashboard-address";
import { useVisiblePanels } from "./visible-panels";
import { WindowStrip } from "./WindowStrip";

/**
 * A saved dashboard, read in one charged batch (F20, Step 2), at an address that decides
 * what it shows (Step 6).
 *
 * Two reads a refresh: the panels, and the panel context, which is Postgres alone, so the
 * header, the notes and detection's line draw before the scans finish and through any
 * ClickHouse outage (D24). Each panel owns its state; what affects the whole page is said
 * once, above the grid (FR48, `page-lines.ts`). A panel that could not be read never draws
 * as a flat line (BR5): it keeps an earlier line for one interval under a filled strip,
 * then words alone.
 */

/** A drag's window, and the panels read in it so far: those on screen, then as they scroll in. */
interface Dragged {
  windowKey: string;
  read: ReadonlySet<string>;
}

/** Where the page's own clock next needs to be read: a line expiring, the banner, "Still reading". */
function nextWake(parts: {
  kept: KeptReadings;
  intervalMs: number | false;
  keepFor: KeepFor;
  now: number;
  lastGoodAt: string | undefined;
  refreshing: boolean;
  readingSince: number | undefined;
}): number | undefined {
  const wakes: number[] = [];
  const expiry = nextExpiry(parts.kept, parts.keepFor, parts.now);
  if (expiry !== undefined) wakes.push(expiry);
  if (parts.refreshing && parts.intervalMs !== false && parts.lastGoodAt !== undefined) {
    wakes.push(Date.parse(parts.lastGoodAt) + staleAfterMs(parts.intervalMs) + 1);
  }
  if (parts.readingSince !== undefined) wakes.push(parts.readingSince + STILL_READING_AFTER_MS);
  const later = wakes.filter((at) => at > parts.now);
  return later.length > 0 ? Math.min(...later) : undefined;
}

export function DashboardScreen({ dashboardId }: { dashboardId: string }) {
  const dashboard = useDashboard(dashboardId);
  const definition = dashboard.data?.definition;
  const [projectZone, setProjectZone] = useState<string | undefined>();
  const address = useDashboardAddress({
    defaultRange: definition?.default_range,
    zone: projectZone,
  });
  const { projectId, choice } = address;
  const preference = useRefreshPreference();
  // This tab's own interval (FR71): never saved, so a reload starts from the dashboard's (AC118).
  const [tabInterval, setTabInterval] = useState<number | undefined>();
  const fixedWindow = choice.kind === "absolute";
  const savedSetting: number | false =
    definition?.refresh_seconds == null ? false : definition.refresh_seconds * MS_PER_SECOND;
  // What the page refreshes at, and what it would be unpaused: the control shows the second.
  const chosenIntervalMs = effectiveIntervalMs({
    saved: savedSetting,
    tab: tabInterval,
    fixedWindow,
    paused: false,
  });
  const intervalMs = preference.paused ? false : chosenIntervalMs;
  const refreshing = intervalMs !== false;
  const keepFor: KeepFor = fixedWindow ? "closed" : chosenIntervalMs;

  const onScreen = useVisiblePanels();
  const [drag, setDrag] = useState<Dragged | undefined>();
  // A drag's narrowing lasts while its window does: any other window reads every panel.
  const dragged = drag?.windowKey === windowCacheKey(choice) ? drag : undefined;
  const panelIds = useMemo(() => (dragged ? [...dragged.read].sort() : undefined), [dragged]);
  const streak = useRef(newRefusalStreak());
  // The picker moves between two lines once a service is chosen; the focus moves with it.
  const pickerHadFocus = useRef(false);
  // A service the person picked here is not "from the link".
  const serviceChosenHere = useRef(false);
  // A panel is drawn once it has scrolled into view, and stays drawn: thirty canvases is a
  // lot for a phone (FR50). Without an observer every panel counts as seen.
  const seenPanels = useRef(new Set<string>());
  const mayDraw = (panelId: string) => {
    if (onScreen.isVisible(panelId)) seenPanels.current.add(panelId);
    return seenPanels.current.has(panelId);
  };
  const context = useDashboardContext(dashboard.data, projectId, choice, {
    intervalMs,
    enabled: address.ready,
  });
  const panels = useDashboardPanels(dashboard.data, projectId, choice, panelIds, {
    intervalMs,
    enabled: address.ready,
    service: address.service,
    paused: preference.paused,
    streak,
  });
  const scope = useMemo(
    () =>
      address.ready && dashboard.data && projectId
        ? { dashboardId: dashboard.data.id, projectId, choice, service: address.service }
        : undefined,
    [address.ready, dashboard.data, projectId, choice, address.service],
  );
  const catchUp = usePanelCatchUp({ base: panels.data, scope, paused: preference.paused });
  const answer = catchUp.answer;

  useEffect(() => setProjectZone(context.data?.project.timezone), [context.data]);

  // A panel scrolling in after a drag joins the read; those already read cost nothing,
  // since the server keeps their answers (BR6).
  // biome-ignore lint/correctness/useExhaustiveDependencies: `version` is when the panels on screen changed
  useEffect(() => {
    const now = onScreen.visibleNow();
    if (!dragged || !now) return;
    const joining = [...now].filter((panelId) => !dragged.read.has(panelId));
    if (joining.length > 0) setDrag({ ...dragged, read: new Set([...dragged.read, ...joining]) });
  }, [onScreen.version, dragged]);

  /** One drag, one window: rounded out, in the address with one history entry, never remembered. */
  const onDrag = useCallback(
    (fromSeconds: number, toSeconds: number) => {
      const window = roundedOut(fromSeconds, toSeconds);
      if (window === undefined) return;
      const visible = onScreen.visibleNow();
      setDrag(
        visible
          ? { windowKey: windowCacheKey({ kind: "absolute", ...window }), read: visible }
          : undefined,
      );
      address.zoom(window);
    },
    [onScreen, address.zoom],
  );
  const canEdit = useCan("edit_dashboards");
  const canConfigureChecks = useCan("operate_checks");
  const canSetUp = useCan("manage_credentials");
  const [kept, setKept] = useState<KeptReadings>({});
  const [lastGoodAt, setLastGoodAt] = useState<string | undefined>();
  // The moment the page last judged its lines: each read, and each line's expiry.
  const [now, setNow] = useState(() => Date.now());
  const lastAttemptAt = Math.max(panels.dataUpdatedAt, panels.errorUpdatedAt);

  // Back from a hidden tab, the banner waits for the first read to answer (design, state table).
  const [returnedAt, setReturnedAt] = useState<number | undefined>();
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") setReturnedAt(Date.now());
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);
  const awaitingRead = returnedAt !== undefined && lastAttemptAt < returnedAt;

  // A first read that is slow says so (FR59).
  const pendingNow = panels.isPending && address.ready && dashboard.data !== undefined;
  const [readingSince, setReadingSince] = useState<number | undefined>();
  useEffect(() => setReadingSince(pendingNow ? Date.now() : undefined), [pendingNow]);

  // A change of service, project, window or dashboard never carries an old reading across (FR31).
  const readKey = `${dashboardId}|${projectId}|${windowCacheKey(choice)}|${address.service}`;
  // biome-ignore lint/correctness/useExhaustiveDependencies: the read's identity is the trigger
  useEffect(() => {
    setKept({});
    setLastGoodAt(undefined);
  }, [readKey]);

  // Each answer, or each failed batch, moves the kept lines on once (FR31). Keyed by when
  // it arrived, not by its value: an equal answer is the same object, and must still count.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the update times are the trigger
  useEffect(() => {
    const at = Date.now();
    setNow(at);
    if (answer && panels.isSuccess) {
      setKept((previous) => nextKept(previous, answer, at));
      const newest = newestGoodRead(answer);
      if (newest) setLastGoodAt(newest);
    } else if (panels.isError) {
      setKept((previous) => allFailing(previous, at));
    }
  }, [panels.dataUpdatedAt, panels.errorUpdatedAt, catchUp.version]);

  // An old line goes when its time is up, and the banner and "Still reading" appear when
  // theirs is, refresh or none (FR31, FR59).
  useEffect(() => {
    const wake = nextWake({
      kept,
      intervalMs: chosenIntervalMs,
      keepFor,
      now,
      lastGoodAt,
      refreshing,
      readingSince,
    });
    if (wake === undefined) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.max(wake - Date.now(), 0));
    return () => clearTimeout(timer);
  }, [kept, chosenIntervalMs, keepFor, now, lastGoodAt, refreshing, readingSince]);

  const readNow = useCallback(() => {
    void panels.refetch();
    void context.refetch();
  }, [panels.refetch, context.refetch]);
  const chooseInterval = useCallback(
    (ms: number | undefined) => setTabInterval(ms === savedSetting ? undefined : ms),
    [savedSetting],
  );
  const zone = context.data?.project.timezone ?? "UTC";
  const { replaceWindow } = address;
  const declaration = useMemo<FixedIntervalRefresh>(
    () => ({
      kind: "fixed-interval",
      fixedWindow,
      savedIntervalMs: savedSetting,
      intervalMs: chosenIntervalMs,
      tabIntervalMs: tabInterval,
      chooseInterval,
      lastGoodAt,
      zone,
      reading: panels.isFetching,
      readNow,
      chooseRange: (rangeId: string) => replaceWindow({ kind: "relative", rangeId }),
    }),
    [
      fixedWindow,
      savedSetting,
      chosenIntervalMs,
      tabInterval,
      chooseInterval,
      lastGoodAt,
      zone,
      panels.isFetching,
      readNow,
      replaceWindow,
    ],
  );
  useDeclaredRefresh(declaration);

  const projects = useProjects();
  const nameOf = (id: string | undefined) =>
    projects.data?.find((project) => project.id === id)?.name;

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
  if (
    address.project.state === "foreign" ||
    (context.isError && isProjectNotFound(context.error))
  ) {
    return (
      <PageState
        words="This link is for a project you cannot open."
        action={{ label: "View against mine", onClick: address.backToOwnProject }}
      />
    );
  }
  if (address.project.state === "unchecked") {
    return <PageState words="Could not check which project this link is for." />;
  }
  if (context.isError && isDashboardNotFound(context.error)) {
    // This tab had loaded it, so it was deleted rather than never there.
    return <PageState words="This dashboard was deleted." />;
  }
  if (context.isError && isNotFound(context.error)) {
    return <PageState words="This link is for a project you cannot open." />;
  }

  const scanPanels = dashboard.data.definition.panels.filter(
    (panel) => panel.kind !== "note" && panel.kind !== "open_findings",
  );
  // The link's service is unknown when the project has no record of it: every panel that reads
  // the picked service says so (AC123).
  const pickerResults = answer
    ? dashboard.data.definition.panels.filter(usesPicker).map((panel) => answer.panels[panel.id])
    : [];
  const unknownService =
    address.service !== undefined &&
    pickerResults.length > 0 &&
    pickerResults.every((result) => result?.state === "not_in_project")
      ? address.service
      : undefined;
  const pickerUnset =
    answer !== undefined &&
    Object.values(answer.panels).some((result) => result.state === "needs_service");
  const detection = context.data
    ? detectionHeadlineFor(context.data.detection, { zone: context.data.project.timezone })
    : undefined;
  const failure = failureOf(panels.error, {
    retryScheduled:
      isRateLimited(panels.error) &&
      !preference.paused &&
      streak.current.refusals <= WHOLE_REQUEST_RETRIES,
  });
  const lines = pageLines({
    now,
    zone,
    canSetUp,
    refreshing,
    intervalMs: chosenIntervalMs,
    readingSince,
    failure,
    answer,
    scanCount: scanPanels.length,
    lastGoodAt,
    awaitingRead,
    context: context.data,
    detectionTitle: detection?.title,
    projectName: context.data?.project.name ?? "this project",
    pickerUnset,
    unknownService,
    fromLink: address.fromLink ? { own: nameOf(address.ownProjectId) } : undefined,
    fixedTimes: fixedWindow ? describeTimes(resolveChoice(choice), zone, now) : undefined,
    endsRecently: choice.kind === "absolute" && now - Date.parse(choice.to) < RECENT_END_MS,
    zoomed:
      address.zoomedFrom && address.zoomedFrom.kind === "absolute"
        ? { before: describeTimes(address.zoomedFrom, zone, now) }
        : address.zoomedFrom
          ? { before: `the ${liveWords(address.zoomedFrom.rangeId)}` }
          : undefined,
    wider: choice.kind === "relative" ? widerRange(choice.rangeId) : undefined,
  } satisfies PageLineInput);

  const unreadIds = answer
    ? scanPanels.filter((panel) => {
        const result = answer.panels[panel.id];
        return result !== undefined && unread(result);
      })
    : [];
  const onAction = (action: LineAction) => {
    switch (action) {
      case "read-again":
        void panels.refetch();
        break;
      case "back-to-live":
        address.replaceWindow(undefined);
        break;
      case "back-to-project":
        address.backToOwnProject();
        break;
      case "back-from-zoom":
        address.backFromZoom();
        break;
      case "show-wider": {
        const wider = choice.kind === "relative" ? widerRange(choice.rangeId) : undefined;
        if (wider) address.replaceWindow({ kind: "relative", rangeId: wider.id });
        break;
      }
    }
  };

  return (
    <DashboardView
      dashboard={dashboard.data}
      context={context.data}
      answer={answer}
      batchError={panels.isError ? panels.error : undefined}
      pending={panels.isPending}
      kept={kept}
      now={now}
      lastGoodAt={lastGoodAt}
      intervalMs={keepFor}
      canEdit={canEdit}
      canConfigureChecks={canConfigureChecks}
      canSetUp={canSetUp}
      lines={lines}
      onAction={onAction}
      onReadAgain={() => void panels.refetch()}
      onReadPanel={(panelId) => catchUp.readAgain([panelId])}
      readingAgainIn={catchUp.readingAgainIn}
      onDrag={onDrag}
      onWiden={(limitDays) => {
        const range = widestRangeWithin(limitDays);
        if (range) address.replaceWindow({ kind: "relative", rangeId: range.id });
      }}
      watch={onScreen.watch}
      mayDraw={mayDraw}
      panelIds={panelIds}
      unreadCount={unreadIds.length}
      onFocusFirstUnread={() => document.getElementById(`panel-${unreadIds[0]?.id}`)?.focus()}
      failing={panels.isError || unreadIds.length > 0}
      address={address}
      projectName={context.data?.project.name}
      picker={
        dashboard.data.definition.service_picker ? (
          <ServicePicker
            projectId={projectId}
            choice={choice}
            value={address.service}
            fromLink={address.service !== undefined && !serviceChosenHere.current}
            onPick={(service) => {
              serviceChosenHere.current = true;
              address.setService(service);
            }}
            focusKeeper={pickerHadFocus}
          />
        ) : undefined
      }
    />
  );
}

const MS_PER_SECOND = 1000;

/** "Show the last 7 days": the widest live range a raw panel may read (FR17). */
export function widestRangeWithin(limitDays: number): (typeof TIME_RANGES)[number] | undefined {
  const minutes = limitDays * MINUTES_PER_DAY;
  return [...TIME_RANGES].reverse().find((range) => range.minutes <= minutes);
}

const MINUTES_PER_DAY = 1440;
/** The setup page that turns on host metrics: a collector's hostmetrics receiver. */
const COLLECTOR_DOC = "collector";

/** How a failed batch reads to the page: refused, given up on at 25 s, or failed. */
function failureOf(error: unknown, options: { retryScheduled: boolean }): PageLineInput["failure"] {
  if (error === null || error === undefined) return undefined;
  if (!(error instanceof ApiError)) return { kind: "failed" };
  const correlationId = error.context.correlationId;
  if (isRateLimited(error)) {
    return { kind: "refused", correlationId, retryScheduled: options.retryScheduled };
  }
  if (error.context.aborted) return { kind: "timed-out", correlationId };
  return { kind: "failed", correlationId };
}

function newestGoodRead(answer: PanelReadResponse): string | undefined {
  const reads = Object.values(answer.panels).flatMap((result) =>
    result.state === "ok" ? [result.read_at] : [],
  );
  return reads.sort().at(-1);
}

function PageState({
  words,
  action,
}: {
  words: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="flex flex-col gap-2 p-6">
      <p className="text-ink">{words}</p>
      {action ? (
        <button
          type="button"
          onClick={action.onClick}
          className="inline-flex min-h-11 items-center self-start text-brand text-sm underline md:min-h-0"
        >
          {action.label}
        </button>
      ) : null}
      <Link
        to="/dashboards"
        className="inline-flex min-h-11 items-center text-brand text-sm underline md:min-h-0"
      >
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
  /** How long an earlier line is kept: the refresh interval, or "closed" on a fixed window. */
  intervalMs: KeepFor;
  canEdit: boolean;
  canConfigureChecks: boolean;
  canSetUp: boolean;
  lines: PageLines;
  onAction: (action: LineAction) => void;
  /** The whole page, again. */
  onReadAgain: () => void;
  /** One panel, again, without a try spent (FR45). */
  onReadPanel: (panelId: string) => void;
  /** Seconds until a refused panel reads again by itself, while one is scheduled (FR45). */
  readingAgainIn: number | undefined;
  onDrag: (fromSeconds: number, toSeconds: number) => void;
  /** A raw panel's "Show the last 7 days" (FR17). */
  onWiden: (limitDays: number) => void;
  watch: (element: HTMLElement | null) => void;
  /** Whether a panel has been on screen, so its canvas may be drawn (FR50). */
  mayDraw: (panelId: string) => boolean;
  /** After a drag, the panels read so far; undefined when every panel is read. */
  panelIds: readonly string[] | undefined;
  unreadCount: number;
  onFocusFirstUnread: () => void;
  /** Reads have failed since the last good one (FR31). */
  failing: boolean;
  address: ReturnType<typeof useDashboardAddress>;
  projectName: string | undefined;
  /** The service picker, when the dashboard has one (FR48). */
  picker: ReactNode;
}

function DashboardView(props: ViewProps) {
  const { dashboard, context, answer, canEdit, address } = props;
  const zone = context?.project.timezone ?? "UTC";
  // Notes and open findings are drawn from the definition and the context, never read.
  const scanPanels = dashboard.definition.panels.filter(
    (panel) => panel.kind !== "note" && panel.kind !== "open_findings",
  );
  const spans = useMemo(
    () => gridSpans(dashboard.definition.panels),
    [dashboard.definition.panels],
  );
  const detection = context
    ? detectionHeadlineFor(context.detection, { zone: context.project.timezone })
    : undefined;
  // Which link was copied, said in the page's one live region and shown beside the button (AC137).
  const [copied, setCopied] = useState("");

  return (
    <div className="flex flex-col gap-3 p-4">
      <Header
        dashboard={dashboard}
        context={context}
        canEdit={canEdit}
        address={address}
        answer={answer}
        zone={zone}
        copied={copied}
        onCopied={setCopied}
      />
      <PageLine
        lines={props.lines}
        onAction={props.onAction}
        canSetUp={props.canSetUp}
        picker={props.picker}
      />
      {context ? (
        <WindowStrip
          context={context}
          now={props.now}
          // An empty strip never reads as calm while detection is not judging (FR63).
          empty={detection ? detection.title : "no findings, alert incidents or releases."}
        />
      ) : null}
      <StickyLine
        lastGoodAt={props.lastGoodAt}
        zone={zone}
        failing={props.failing}
        unreadCount={props.unreadCount}
        totalCount={dashboard.definition.panels.length}
        onFocusFirst={props.onFocusFirstUnread}
        // One picker on screen: in the line that asks for it while no service is chosen, then
        // here, where it stays in reach as a phone scrolls (FR50).
        picker={props.lines.state?.kind === "picker" ? undefined : props.picker}
      />
      {/* The page's one live region, said on change only, never per refresh (AC143). */}
      <p data-live-region role="status" aria-live="polite" className="sr-only">
        {answer ? loadedWords(answer, scanPanels.length) : ""}
        {copied ? ` ${copied}` : ""}
      </p>
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

/** "Dashboard loaded, 28 of 30 panels read": a read is any panel with an answer of its own. */
function loadedWords(answer: PanelReadResponse, scanCount: number): string {
  const read = Object.values(answer.panels).filter((result) => !unread(result)).length;
  return `Dashboard loaded, ${read} of ${scanCount} panels read`;
}

function Header({
  dashboard,
  context,
  canEdit,
  address,
  answer,
  zone,
  copied,
  onCopied,
}: {
  dashboard: Dashboard;
  context: DashboardContext | undefined;
  canEdit: boolean;
  address: ReturnType<typeof useDashboardAddress>;
  answer: PanelReadResponse | undefined;
  zone: string;
  copied: string;
  onCopied: (said: string) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
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
        // Runbook links stay outside the phone's menu: someone paged at 3 am needs them first (FR50).
        <ul className="flex flex-wrap gap-3 text-sm">
          {dashboard.definition.links.map((link) => (
            <li key={link.url}>
              {/* The host beside the label: a label alone says nothing about where it goes. */}
              <a
                href={link.url}
                rel="noopener noreferrer"
                target="_blank"
                className="inline-flex min-h-11 items-center text-brand underline md:min-h-0"
              >
                {visibleEscapes(link.label)}
              </a>{" "}
              <span className="text-ink-dim text-xs">{hostOf(link.url)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {address.display ? null : (
        <div className="flex flex-col gap-1 md:flex-row md:items-center md:gap-3">
          {/* One menu below `md` holds every header action (FR50). */}
          <button
            type="button"
            aria-expanded={menuOpen}
            aria-label="Dashboard actions"
            onClick={() => setMenuOpen((open) => !open)}
            className="inline-flex min-h-11 min-w-11 items-center self-start text-ink text-lg md:hidden"
          >
            ⋯
          </button>
          <div
            className={`${menuOpen ? "flex" : "hidden"} flex-col items-start gap-1 md:flex md:flex-row md:items-center md:gap-3`}
          >
            <CopyLink
              address={address}
              answer={answer}
              zone={zone}
              definition={dashboard.definition}
              said={copied}
              onSaid={onCopied}
            />
            {/* Anyone who can read it may keep a copy; export writes nothing (FR42, FR27). */}
            <ExportFile dashboard={dashboard} />
            <button
              type="button"
              onClick={() => address.setDisplay(true)}
              className="inline-flex min-h-11 items-center text-brand text-sm underline md:min-h-0"
            >
              Full screen
            </button>
            {/* A viewer is offered no edit that would fail (AC7, AC105). */}
            {canEdit ? (
              <Link
                to="/dashboards/$dashboardId/edit"
                params={{ dashboardId: dashboard.id }}
                className="inline-flex min-h-11 items-center text-brand text-sm underline md:min-h-0"
              >
                Edit
              </Link>
            ) : null}
          </div>
        </div>
      )}
    </header>
  );
}

/**
 * "Copy link", with both choices, exact times first after a drag or on a fixed window, and
 * a sentence saying which was copied (FR29, AC137).
 */
function CopyLink({
  address,
  answer,
  zone,
  definition,
  said,
  onSaid,
}: {
  address: ReturnType<typeof useDashboardAddress>;
  answer: PanelReadResponse | undefined;
  zone: string;
  definition: Dashboard["definition"];
  said: string;
  onSaid: (said: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { choice } = address;
  // The server's own window, as it rounded it, so a link opens the minutes this page shows.
  const times =
    choice.kind === "absolute"
      ? { from: choice.from, to: choice.to }
      : answer
        ? { from: answer.window_start, to: answer.window_end }
        : resolveChoice(choice);
  const liveRange =
    choice.kind === "relative"
      ? choice.rangeId
      : (definition.default_range ?? readRememberedSelection().rangeId);
  const base: LinkTo = {
    origin: window.location.origin,
    pathname: window.location.pathname,
    stringify: router.options.stringifySearch,
    projectId: address.projectId,
    service: address.service,
  };
  const exact = {
    key: "exact",
    label: `Link to these exact times (${describeTimes(times, zone)})`,
    href: () => exactTimesLink(base, times),
    said: "Copied the link to these exact times.",
  };
  const live = {
    key: "live",
    label: `Link that stays live (${liveWords(liveRange)})`,
    href: () => liveLink(base, liveRange),
    said: "Copied the link that stays live.",
  };
  const choices = exactTimesFirst(choice) ? [exact, live] : [live, exact];

  const copy = async (option: (typeof choices)[number]) => {
    try {
      await navigator.clipboard.writeText(option.href());
      onSaid(option.said);
    } catch {
      onSaid("Could not copy the link. Copy the address from the browser instead.");
    }
    setOpen(false);
  };

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((before) => !before)}
        className="inline-flex min-h-11 items-center text-brand text-sm underline md:min-h-0"
      >
        Copy link
      </button>
      {open ? (
        <ul className="flex flex-col items-start gap-1 rounded-sm border border-line bg-raised p-2 text-sm">
          {choices.map((option) => (
            <li key={option.key}>
              <button
                type="button"
                onClick={() => void copy(option)}
                className="inline-flex min-h-11 items-center text-left text-ink underline md:min-h-0"
              >
                {option.label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {/* Not a live region of its own: the page has one, and it says this too. */}
      <p className="text-ink-dim text-xs">{said}</p>
    </div>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
}

function PanelCell(
  props: ViewProps & { panel: StoredPanel; span: string; zone: string; overLimit: number },
) {
  const { panel, span, answer, pending, kept, now, intervalMs, context, zone, canEdit, dashboard } =
    props;
  const readThisPanel = () => props.onReadPanel(panel.id);
  const title = panelTitle(panel);
  // "View as table" opens at the full row's width (FR49).
  const [asTable, setAsTable] = useState(false);
  const frame = (body: ReactNode, toggle?: ReactNode) => (
    <PanelFrame
      panelId={panel.id}
      service={title.service}
      title={title.text}
      span={asTable ? "" : span}
      watch={props.watch}
      holdHeight={answer?.panels[panel.id] === undefined}
    >
      {body}
      {toggle}
    </PanelFrame>
  );

  if (panel.kind === "note") {
    const text = typeof panel.settings.text === "string" ? panel.settings.text : "";
    return frame(<NotePanel text={text} />);
  }
  if (panel.kind === "open_findings") {
    return frame(<OpenFindingsPanel context={context} service={serviceOf(panel)} />);
  }
  const result = answer?.panels[panel.id];
  const awaitingScroll = props.panelIds !== undefined && !props.panelIds.includes(panel.id);
  if (result === undefined && awaitingScroll) {
    // After a drag, read when it scrolls into view (FR53).
    return frame(
      <div
        aria-hidden
        className="rounded-xs bg-raised"
        style={{ height: chartHeight(panel.height) }}
      />,
    );
  }
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
    if (!KNOWN_KINDS.has(panel.kind)) {
      return frame(<PanelWords words="This panel type is no longer available." />);
    }
    const chart = TIME_KINDS.has(panel.kind);
    if (chart && !props.mayDraw(panel.id)) {
      return frame(
        <div
          aria-hidden
          className="rounded-xs bg-raised"
          style={{ height: chartHeight(panel.height) }}
        />,
      );
    }
    const marking = marksFor(panel, context, result);
    return frame(
      <>
        <PanelReading
          panel={panel}
          reading={result}
          zone={zone}
          now={now}
          syncKey={syncKeyOf(dashboard.id)}
          onDrag={props.onDrag}
          marks={marking.marks}
          asTable={asTable}
          description={`${title.service ? `${title.service} · ` : ""}${title.text} across the window.`}
        />
        <MarkingNote note={marking.note} coverage={marking.coverage} />
      </>,
      chart ? (
        <TableToggle asTable={asTable} onToggle={() => setAsTable((before) => !before)} />
      ) : undefined,
    );
  }
  const words =
    stateWords(result, panel, {
      canEdit,
      owner: dashboard.owner,
      projectName: context?.project.name ?? "this project",
      overLimit: props.overLimit,
      canConfigureChecks: props.canConfigureChecks,
      windowStart: answer?.window_start,
    }) ?? "";
  if (result.state === "window_too_wide") {
    const range = widestRangeWithin(result.limit_days);
    return frame(
      <div className="flex flex-col items-start gap-2 text-ink-dim text-sm">
        <p>{words}</p>
        {range ? (
          <button
            type="button"
            onClick={() => props.onWiden(result.limit_days)}
            className="inline-flex min-h-11 items-center text-brand underline md:min-h-0"
          >
            Show the last {range.label}
          </button>
        ) : null}
      </div>,
    );
  }
  if (result.state === "empty" && panel.kind === "top_nodes_trend" && canEdit) {
    // No machine sends host metrics until a collector does: where to start (AC129).
    return frame(
      <div className="flex flex-col items-start gap-2 text-ink-dim text-sm">
        <p>{words}</p>
        <a
          href={setupDocUrl(COLLECTOR_DOC)}
          className="inline-flex min-h-11 items-center text-brand underline md:min-h-0"
        >
          Read the collector setup
        </a>
      </div>,
    );
  }
  const old = transient(result) ? keptLine(kept[panel.id], intervalMs, now) : undefined;
  if (old) return frame(<KeptChart {...props} reading={old} />);
  // "Reading again in 20 s" only while one is scheduled; after the last try, "Read again" (FR45).
  const scheduled = result.state === "refused" && props.readingAgainIn !== undefined;
  return frame(
    <PanelWords
      words={words}
      countdown={scheduled ? `Reading again in ${props.readingAgainIn} s` : undefined}
      correlationId={transient(result) && !scheduled ? answer?.correlation_id : undefined}
      onReadAgain={transient(result) && !scheduled ? readThisPanel : undefined}
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
  return (
    <>
      <StaleStrip
        since={formatZonedTime(reading.read_at, { zone })}
        closed={props.intervalMs === "closed"}
      />
      {KNOWN_KINDS.has(panel.kind) ? (
        <PanelReading
          panel={panel}
          reading={reading}
          zone={zone}
          description="An earlier reading."
        />
      ) : null}
    </>
  );
}

/** What a chart's marks are, said under it: the whole project's, or no deploy information. */
function MarkingNote({ note, coverage }: { note?: string; coverage?: string }) {
  if (!note && !coverage) return null;
  return (
    <p className="mt-1 text-ink-dim text-xs">
      {note}
      {note && coverage ? " " : null}
      {coverage ? (
        <>
          {coverage}{" "}
          <a href={setupDocUrl("deploys")} className="text-brand underline">
            How releases are found
          </a>
        </>
      ) : null}
    </p>
  );
}

/** Every chart offers its numbers as a table (FR34); a 44 px target below `md` (FR50). */
function TableToggle({ asTable, onToggle }: { asTable: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={asTable}
      className="mt-1 inline-flex min-h-11 items-center text-brand text-xs underline md:min-h-0"
    >
      View as table
    </button>
  );
}
