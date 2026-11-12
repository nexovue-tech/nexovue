import { formatCount } from "@/shared/format/number";
import { formatRelativeTime } from "@/shared/format/time";
import { visibleEscapes } from "@/shared/format/visible-escapes";

import type { PanelResult, Signal, StoredPanel } from "./api";

/**
 * What a panel is called and what it says when it has no line to draw (F20, design
 * *Words by kind*). Pure, so each sentence is tested without a canvas.
 *
 * Every telemetry value and stored text goes through `visibleEscapes` (FR61). Words that
 * name an action follow the reader's role: a viewer is never told to remove panels (FR65).
 */

export const TIME_KINDS: ReadonlySet<string> = new Set([
  "project_trend",
  "service_trend",
  "top_services_trend",
  "node_trend",
  "top_nodes_trend",
  "endpoint_trend",
  "check_trend",
  "statement_trend",
  "metric_series",
]);

/** One number, compared or not, and the open-findings count drawn from the context. */
export const NUMBER_KINDS: ReadonlySet<string> = new Set([
  "project_number",
  "service_number",
  "open_findings",
]);

/** Ranked tables: their height follows their rows, so none is saved `small` (FR5). */
export const TABLE_KINDS: ReadonlySet<string> = new Set([
  "services_table",
  "endpoints_table",
  "dependencies_table",
  "error_groups_table",
  "database_statements_table",
  "web_vitals_table",
]);

/** Every kind this console draws; a stored panel of any other reads `unknown_kind` (FR8). */
export const KNOWN_KINDS: ReadonlySet<string> = new Set([
  ...TIME_KINDS,
  ...NUMBER_KINDS,
  ...TABLE_KINDS,
  "note",
]);

/**
 * Kinds that draw every finding and release in the project, since they have no service:
 * they say so, so nobody reads a checkout finding on a disk chart as a claim about that
 * disk (FR18, AC132).
 */
export const PROJECT_MARKED_KINDS: ReadonlySet<string> = new Set([
  "node_trend",
  "top_nodes_trend",
  "check_trend",
  "statement_trend",
  "metric_series",
]);

/** A node as the infrastructure screen names it: its container, its host, or its reporter. */
export interface NodeRef {
  host: string;
  container: string;
  reporter: string;
  reporter_instance: string;
}

export function nodeOf(panel: StoredPanel): NodeRef | undefined {
  const node = panel.settings.node;
  if (typeof node !== "object" || node === null) return undefined;
  const { host, container, reporter, reporter_instance } = node as Record<string, unknown>;
  return typeof host === "string" &&
    typeof container === "string" &&
    typeof reporter === "string" &&
    typeof reporter_instance === "string"
    ? { host, container, reporter, reporter_instance }
    : undefined;
}

/** "checkout" for a container, "web-01" for a host, "reported by recommendation" otherwise. */
export function nodeName(node: NodeRef): string {
  if (node.container) return visibleEscapes(node.container);
  if (node.host) return visibleEscapes(node.host);
  return `reported by ${visibleEscapes(node.reporter)}`;
}

const SIGNAL_NAMES: Record<Signal, string> = {
  rate: "Rate",
  errors: "Errors",
  latency: "Latency",
};

export function signalOf(panel: StoredPanel): Signal | undefined {
  const signal = panel.settings.signal;
  return signal === "rate" || signal === "errors" || signal === "latency" ? signal : undefined;
}

/** The named service, or undefined for the picker or a panel with none. */
export function serviceOf(panel: StoredPanel): string | undefined {
  const service = panel.settings.service;
  return typeof service === "string" ? service : undefined;
}

/** The words for a number's measure, as Overview names its four totals. */
export const MEASURE_NAMES: Record<string, string> = {
  requests_per_minute: "Requests/min",
  error_rate: "Failure share",
  p50_ms: "p50 (est.)",
  p95_ms: "p95 (est.)",
  p99_ms: "p99 (est.)",
  service_count: "Services reporting",
};

const TABLE_NAMES: Record<string, (panel: StoredPanel) => string> = {
  services_table: (panel) =>
    panel.settings.order === "slowest" ? "Slowest services" : "Worst services",
  endpoints_table: (panel) =>
    panel.settings.order === "slowest" ? "Slowest endpoints" : "Worst endpoints",
  dependencies_table: (panel) =>
    panel.settings.failing_only === true ? "Failing dependencies" : "Busiest dependencies",
  error_groups_table: () => "Most frequent errors",
  database_statements_table: () => "Slowest statements",
  web_vitals_table: (panel) =>
    typeof panel.settings.device_class === "string"
      ? `Core Web Vitals · ${visibleEscapes(panel.settings.device_class)}`
      : "Core Web Vitals",
};

/** Kinds whose service is a setting, the picker, or (where optional) none. */
const SERVICE_KINDS: ReadonlySet<string> = new Set([
  "service_trend",
  "endpoint_trend",
  "service_number",
  "open_findings",
  "endpoints_table",
  "error_groups_table",
  "metric_series",
]);

/** The service a panel shows, as its heading names it: escaped, or "Picked service". */
function headingService(panel: StoredPanel): string | undefined {
  const service = panel.settings.service;
  if (typeof service === "string") return visibleEscapes(service);
  if (typeof service === "object" && service !== null) return "Picked service";
  return undefined;
}

function stringSetting(panel: StoredPanel, name: string): string {
  const value = panel.settings[name];
  return typeof value === "string" ? value : "";
}

/** A generated title always says what is shown, with the service first (FR35). */
export function panelTitle(panel: StoredPanel): { service?: string; text: string } {
  const signal = signalOf(panel);
  const what = panel.title ? visibleEscapes(panel.title) : signal ? SIGNAL_NAMES[signal] : "Note";
  if (panel.kind === "service_trend") {
    const service = serviceOf(panel);
    return {
      service: service === undefined ? "Picked service" : visibleEscapes(service),
      text: what,
    };
  }
  const own = panel.title ? visibleEscapes(panel.title) : undefined;
  const service = SERVICE_KINDS.has(panel.kind) ? headingService(panel) : undefined;
  const withService = (text: string) => ({
    ...(service === undefined ? {} : { service }),
    text: own ?? text,
  });
  switch (panel.kind) {
    case "endpoint_trend":
      return withService(`${visibleEscapes(stringSetting(panel, "endpoint"))} · ${what}`);
    case "top_nodes_trend": {
      const count = Number(panel.settings.count) || 0;
      return {
        text:
          own ??
          `Top ${count} ${count === 1 ? "machine" : "machines"} · ${visibleEscapes(stringSetting(panel, "metric"))}`,
      };
    }
    case "check_trend": {
      const shown = panel.settings.signal === "duration" ? "Duration" : "Uptime";
      return { text: own ?? `${visibleEscapes(stringSetting(panel, "check"))} · ${shown}` };
    }
    case "statement_trend": {
      const label = stringSetting(panel, "label");
      return {
        text:
          own ??
          (label
            ? visibleEscapes(label)
            : `Statement ${visibleEscapes(stringSetting(panel, "query_hash"))}`),
      };
    }
    case "project_number":
    case "service_number":
      return withService(MEASURE_NAMES[stringSetting(panel, "measure")] ?? "Number");
    case "open_findings":
      return withService("Open findings");
    case "metric_series":
      return withService(metricTitle(panel));
  }
  const table = TABLE_NAMES[panel.kind];
  if (table) return withService(table(panel));
  if (panel.kind === "top_services_trend") {
    const count = Number(panel.settings.count) || 0;
    const order = panel.settings.order === "slowest" ? "Slowest" : "Worst";
    return {
      text: panel.title
        ? visibleEscapes(panel.title)
        : `${order} ${count} ${count === 1 ? "service" : "services"} · ${what}`,
    };
  }
  if (panel.kind === "node_trend") {
    const node = nodeOf(panel);
    const metric = typeof panel.settings.metric === "string" ? panel.settings.metric : "";
    return {
      text: panel.title
        ? visibleEscapes(panel.title)
        : `${node ? nodeName(node) : "A machine"} · ${visibleEscapes(metric)}`,
    };
  }
  if (!KNOWN_KINDS.has(panel.kind)) {
    return { text: panel.title ? visibleEscapes(panel.title) : "Panel" };
  }
  return { text: what };
}

const AGGREGATION_NAMES: Record<string, string> = {
  mean: "mean",
  min: "lowest",
  max: "highest",
  sum: "total",
};

/** "orders_processed", or "orders_processed · all series, total" when every series is one line. */
function metricTitle(panel: StoredPanel): string {
  const metric = visibleEscapes(stringSetting(panel, "metric"));
  if (typeof panel.settings.series === "string") return metric;
  const how = AGGREGATION_NAMES[stringSetting(panel, "aggregation")] ?? "mean";
  return `${metric} · all series, ${how}`;
}

/**
 * A metric series reading two units in the window draws no line, and says so (AC51).
 * Undefined when it has one unit or none.
 */
export function mixedUnitsWords(
  panel: StoredPanel,
  units: readonly string[] | null | undefined,
): string | undefined {
  if (panel.kind !== "metric_series" || !units || units.length < 2) return undefined;
  const named = units.map((unit) => visibleEscapes(unit === "" ? "no unit" : unit)).join(", ");
  return `${visibleEscapes(stringSetting(panel, "metric"))} arrives in ${units.length} units in this window (${named}), so its series are not added together.`;
}

/** "Combines 3 series", under a line that adds several together (FR15, AC52). */
export function combinedWords(
  panel: StoredPanel,
  combined: number | null | undefined,
): string | undefined {
  if (
    panel.kind !== "metric_series" ||
    combined === null ||
    combined === undefined ||
    combined < 2
  ) {
    return undefined;
  }
  return `Combines ${formatCount(combined)} series.`;
}

/** An edit the reader can make, or who to ask for it (FR65). */
function askToEdit(context: WordsContext, edit: string): string {
  if (context.canEdit) return `${edit.charAt(0).toUpperCase()}${edit.slice(1)}.`;
  return `Ask ${context.owner ? visibleEscapes(context.owner) : "the person who made it"}, or someone who can edit it, to ${edit}.`;
}

export interface WordsContext {
  canEdit: boolean;
  owner: string | null;
  projectName: string;
  /** Panels past the scan cap, for the editor's "Remove N panels". */
  overLimit: number;
  now?: number;
  /** Whether the reader may rename a check: "Rename one" is said only to them (FR65). */
  canConfigureChecks?: boolean;
  /** The read's window start, to tell a quiet service from one that sent nothing (AC99). */
  windowStart?: string;
}

/** "Last seen 3 hours ago.", or nothing when there is no record of when. */
function lastSeen(at: string | null, now: number | undefined): string {
  return at === null ? "" : ` Last seen ${formatRelativeTime(at, now)}.`;
}

/** What an `empty` panel says, by kind (design, *Words by kind*). */
function emptyWords(
  result: Extract<PanelResult, { state: "empty" }>,
  panel: StoredPanel,
  context: WordsContext,
): string {
  switch (panel.kind) {
    case "top_services_trend":
    case "services_table":
    case "endpoints_table":
      return "No services reported in this window.";
    case "node_trend": {
      const node = nodeOf(panel);
      return `${node ? nodeName(node) : "This machine"} has not reported in this window.`;
    }
    case "top_nodes_trend":
      return "No machine has reported this metric in this window.";
    case "check_trend":
      return "This check has not run in this window.";
    case "statement_trend":
    case "database_statements_table":
      return "No statement reported in this window.";
    case "metric_series":
      return "This metric has not reported in this window.";
    case "web_vitals_table":
      return "No browser readings in this window.";
    case "dependencies_table":
      return panel.settings.failing_only === true
        ? "No dependency failed in this window."
        : "No dependencies seen in this window.";
    case "error_groups_table": {
      const service = serviceOf(panel);
      // Good news only when the service was seen in the window (AC99).
      const seenInWindow =
        result.last_seen_at !== null &&
        context.windowStart !== undefined &&
        Date.parse(result.last_seen_at) >= Date.parse(context.windowStart);
      if (service === undefined || result.last_seen_at === null || seenInWindow) {
        return "No errors in this window.";
      }
      return `${visibleEscapes(service)} sent nothing in this window.${lastSeen(result.last_seen_at, context.now)}`;
    }
    case "service_trend":
    case "endpoint_trend":
    case "service_number":
      return `No requests in this window.${lastSeen(result.last_seen_at, context.now)}`;
    default:
      return "Nothing reported in this window.";
  }
}

/** The sentence a panel shows in place of a line. Undefined when it draws one. */
export function stateWords(
  result: PanelResult,
  panel: StoredPanel,
  context: WordsContext,
): string | undefined {
  switch (result.state) {
    case "ok":
      return undefined;
    case "empty":
      return emptyWords(result, panel, context);
    case "not_in_project":
      if (panel.kind === "check_trend") {
        return `No check named ${visibleEscapes(stringSetting(panel, "check"))} in ${visibleEscapes(context.projectName)}.`;
      }
      return `No record of ${serviceOf(panel) ? visibleEscapes(serviceOf(panel) ?? "") : "this service"} in ${visibleEscapes(context.projectName)}.`;
    case "ambiguous": {
      const named = `Two checks in ${visibleEscapes(context.projectName)} are named ${visibleEscapes(stringSetting(panel, "check"))}`;
      return context.canConfigureChecks
        ? `${named}. Rename one.`
        : `${named}, so this panel cannot tell which to read.`;
    }
    case "series_changed":
      return `${visibleEscapes(stringSetting(panel, "metric"))} still reports, under different attributes. ${askToEdit(context, "choose again")}`;
    case "too_many_series":
      return `This metric has more than ${formatCount(result.limit)} series, too many to combine. ${askToEdit(context, "choose one series")}`;
    case "window_too_wide":
      return `This ${TABLE_KINDS.has(panel.kind) ? "table" : "chart"} can show at most ${result.limit_days} days.`;
    case "paused_by_operator":
      return "Dashboards paused by your administrator.";
    case "needs_service":
      return "Choose a service to fill this dashboard.";
    case "over_limit":
      return context.canEdit
        ? `This dashboard asks for more reads than one page may make. Remove ${context.overLimit} ${context.overLimit === 1 ? "panel" : "panels"}.`
        : `This dashboard asks for more reads than one page may make. Ask ${context.owner ? visibleEscapes(context.owner) : "the person who made it"}, or someone who can edit it, to remove panels.`;
    case "refused":
      return "Too many reads just now.";
    case "timed_out":
      return "The database took too long to answer.";
    case "failed":
      return "Could not read this.";
    case "unknown_kind":
      return "This panel type is no longer available.";
  }
}

/** Panels that hold no reading of this answer: the header's "could not be read" count (FR64). */
export function unread(result: PanelResult): boolean {
  return (
    result.state === "failed" ||
    result.state === "timed_out" ||
    result.state === "refused" ||
    result.state === "over_limit" ||
    result.state === "ambiguous" ||
    result.state === "series_changed" ||
    result.state === "too_many_series" ||
    result.state === "unknown_kind"
  );
}

/** A read that failed for now and may succeed on the next, so an earlier line may stay. */
export function transient(result: PanelResult): boolean {
  return result.state === "failed" || result.state === "timed_out" || result.state === "refused";
}
