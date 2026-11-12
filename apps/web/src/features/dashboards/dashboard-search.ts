import type { WindowChoice } from "@/shared/selection";
import { DASHBOARD_RANGES } from "@/shared/selection";

/**
 * What a dashboard's address carries (F20, FR28, FR53): the project, the window, the
 * picked service, and full screen. Read and checked here, once, so a hand-edited address
 * is read as what it can be and never as something the API would refuse.
 *
 * `project` is an identifier, `range` one of the dashboard's own ids, `from` and `to` a
 * valid ordered pair, and `service` a name the reader sees quoted, never trusted: a
 * name that is not in the project is said so (AC123), and the raw value is what is read.
 */

export interface DashboardSearch {
  project?: string;
  range?: string;
  from?: string;
  to?: string;
  service?: string;
  display?: 1;
}

const IDENTIFIER = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** The API's own bound on a name (`NAME_MAX_LENGTH`): a longer one cannot be a service. */
export const SERVICE_NAME_MAX_LENGTH = 255;
const MAX_INSTANT_LENGTH = 40;
/** A fixed window is no wider than the widest range a dashboard offers, 30 days, and a day over. */
const LONGEST_WINDOW_MS = 31 * 24 * 60 * 60_000;

function instant(value: unknown): string | undefined {
  if (typeof value !== "string" || value.length > MAX_INSTANT_LENGTH) return undefined;
  const at = Date.parse(value);
  return Number.isFinite(at) ? new Date(at).toISOString() : undefined;
}

export function parseDashboardSearch(search: Record<string, unknown>): DashboardSearch {
  const parsed: DashboardSearch = {};
  if (typeof search.project === "string" && IDENTIFIER.test(search.project)) {
    parsed.project = search.project.toLowerCase();
  }

  const from = instant(search.from);
  const to = instant(search.to);
  const span = from !== undefined && to !== undefined ? Date.parse(to) - Date.parse(from) : 0;
  if (from !== undefined && to !== undefined && span > 0 && span <= LONGEST_WINDOW_MS) {
    // Fixed times win over a range: a link that names both was made by hand.
    parsed.from = from;
    parsed.to = to;
  } else if (
    typeof search.range === "string" &&
    DASHBOARD_RANGES.some((range) => range.id === search.range)
  ) {
    parsed.range = search.range;
  }

  if (
    typeof search.service === "string" &&
    search.service.length > 0 &&
    search.service.length <= SERVICE_NAME_MAX_LENGTH
  ) {
    parsed.service = search.service;
  }
  // `display=1` parses as a number, and a typed address may carry it as text.
  if (search.display === 1 || search.display === "1" || search.display === true) parsed.display = 1;
  return parsed;
}

/** The window an address names, or the dashboard's own default, or none (FR52). */
export function addressWindow(
  search: DashboardSearch,
  defaultRange: string | null | undefined,
): WindowChoice | undefined {
  if (search.from !== undefined && search.to !== undefined) {
    return { kind: "absolute", from: search.from, to: search.to };
  }
  if (search.range !== undefined) return { kind: "relative", rangeId: search.range };
  if (defaultRange && DASHBOARD_RANGES.some((range) => range.id === defaultRange)) {
    return { kind: "relative", rangeId: defaultRange };
  }
  return undefined;
}

/** A window as address keys: a live range by its id, a fixed one by its two instants. */
export function windowSearch(choice: WindowChoice): Pick<DashboardSearch, "range" | "from" | "to"> {
  return choice.kind === "relative"
    ? { range: choice.rangeId }
    : { from: choice.from, to: choice.to };
}

/**
 * The address's project, against the projects the reader may open (design, section 7).
 *
 * **No read runs until the list has loaded**, and a project that is not in it is a
 * refusal, not a fallback: the console would otherwise swap in the first project, and
 * the reader would be looking at the wrong project's numbers under the sender's link.
 */
export type AddressProject =
  | { state: "none" }
  | { state: "loading" }
  | { state: "unchecked" }
  | { state: "foreign"; id: string }
  | { state: "ready"; id: string };

export function addressProject(
  search: DashboardSearch,
  projects: { id: string }[] | undefined,
  failed: boolean,
): AddressProject {
  if (search.project === undefined) return { state: "none" };
  if (projects === undefined) return failed ? { state: "unchecked" } : { state: "loading" };
  return projects.some((project) => project.id === search.project)
    ? { state: "ready", id: search.project }
    : { state: "foreign", id: search.project };
}

/** The address as the router writes it: only what is set. */
export function addressOf(parts: {
  project?: string;
  window?: WindowChoice;
  service?: string;
  display?: boolean;
}): DashboardSearch {
  return {
    ...(parts.project === undefined ? {} : { project: parts.project }),
    ...(parts.window === undefined ? {} : windowSearch(parts.window)),
    ...(parts.service === undefined ? {} : { service: parts.service }),
    ...(parts.display ? { display: 1 as const } : {}),
  };
}

const DISPLAY_PATH = /^\/dashboards\/([^/]+)\/?$/;

/**
 * Whether this address is a dashboard in full screen (FR55): the shell hides its sidebar
 * and top bar, and the page keeps its own project line, age, banners and counts.
 */
export function inDisplayMode(pathname: string, search: Record<string, unknown>): boolean {
  const match = DISPLAY_PATH.exec(pathname);
  return match !== null && match[1] !== "new" && parseDashboardSearch(search).display === 1;
}

/**
 * What the builder's address for a new dashboard carries (F20, FR39, FR41, FR42): the
 * starting point, the service a picker previews with, and the one-off id of a checked file
 * waiting in the query cache. The file itself never rides in an address.
 */
export interface NewDashboardSearch {
  template?: string;
  service?: string;
  file?: string;
}

/** A template's id, as `templates.py` spells them. */
const TEMPLATE_ID = /^[a-z0-9_]{1,64}$/;

export function parseNewDashboardSearch(search: Record<string, unknown>): NewDashboardSearch {
  const parsed: NewDashboardSearch = {};
  if (typeof search.template === "string" && TEMPLATE_ID.test(search.template)) {
    parsed.template = search.template;
  }
  if (
    typeof search.service === "string" &&
    search.service.length > 0 &&
    search.service.length <= SERVICE_NAME_MAX_LENGTH
  ) {
    parsed.service = search.service;
  }
  if (typeof search.file === "string" && IDENTIFIER.test(search.file)) {
    parsed.file = search.file;
  }
  return parsed;
}
