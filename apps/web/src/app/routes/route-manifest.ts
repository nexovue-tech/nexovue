/**
 * Every Phase 1 screen, in one table.
 *
 * The router and the sidebar are both built from this, so a route cannot exist without
 * navigation to it and navigation cannot point at a route that does not exist. The task
 * ids are the ones in docs/delivery/phase-1-plan.md; the placeholder shows them, which
 * is how walking the app tells you what is still missing.
 */

/** Only paths that exist in the tree. Keeps the sidebar from pointing at nothing. */
export type NavPath =
  | "/"
  | "/services"
  | "/service-map"
  | "/traces"
  | "/errors"
  | "/logs"
  | "/rum"
  | "/synthetics"
  | "/database"
  | "/infrastructure"
  | "/usage"
  | "/settings/project";

export interface ScreenDefinition {
  path: string;
  title: string;
  /** One line: the question the screen answers. */
  description: string;
  /** The plan task that delivers it. */
  taskId: string;
}

export interface NavSection {
  /** Small-caps rule in the sidebar. */
  label: string;
  items: Array<{ path: NavPath; title: string }>;
}

/**
 * `/` is the Phase 2 findings feed and stays a placeholder for the whole of Phase 1.
 * That is the standing rule in the plan: a dashboard must never become the front door.
 */
export const FINDINGS_FEED: ScreenDefinition = {
  path: "/",
  title: "Findings",
  description:
    "The feed of things that need you, written as sentences. Phase 2 builds it; until then this route stays deliberately empty so no dashboard drifts into being the front door.",
  taskId: "Phase 2 · F1",
};

export const SHELL_SCREENS: ScreenDefinition[] = [
  {
    path: "/services",
    title: "Services",
    description:
      "Every service you send traces from, ranked by what is worst right now: request rate, error rate and p95 trend.",
    taskId: "P1-40",
  },
  {
    path: "/services/$name",
    title: "Service overview",
    description:
      "One service in detail — its endpoints ranked by slowness and errors, and what changed recently.",
    taskId: "P1-40",
  },
  {
    path: "/service-map",
    title: "Service map",
    description:
      "Who calls whom, derived from spans you already send, with call rate, error rate and latency on every edge.",
    taskId: "P1-56",
  },
  {
    path: "/traces",
    title: "Traces",
    description: "Search and filter every assembled trace, then open one as a waterfall.",
    taskId: "P1-41",
  },
  {
    path: "/traces/$traceId",
    title: "Trace",
    description:
      "One request end to end: every span, where the time went, and the logs that belong to it.",
    taskId: "P1-17",
  },
  {
    path: "/errors",
    title: "Errors",
    description:
      "Exceptions grouped by where they come from, with counts, first and last seen, and stack traces.",
    taskId: "P1-42",
  },
  {
    path: "/logs",
    title: "Logs",
    description:
      "Full-text search across every log line, filtered by service and severity, with a live tail.",
    taskId: "P1-43",
  },
  {
    path: "/infrastructure",
    title: "Infrastructure",
    description: "Hosts and containers: CPU, memory, disk and network behind the services.",
    taskId: "P1-44",
  },
  {
    path: "/synthetics",
    title: "Synthetics",
    description:
      "The checks we run for you — uptime, certificates and browser flows — with failure detail and screenshots.",
    taskId: "P1-45",
  },
  {
    path: "/database",
    title: "Database",
    description: "Slow queries, mean time, call counts and connection health.",
    taskId: "P1-46",
  },
  {
    path: "/rum",
    title: "Real user monitoring",
    description:
      "What real browsers experienced: Core Web Vitals by page, geography and device, and the JavaScript errors behind them.",
    taskId: "P1-47",
  },
  {
    path: "/usage",
    title: "Usage",
    description: "What you have ingested this period and what it projects to cost.",
    taskId: "P1-48",
  },
  {
    path: "/settings/project",
    title: "Project",
    description: "Project name, environments and the defaults every screen inherits.",
    taskId: "P1-59",
  },
  {
    path: "/settings/api-keys",
    title: "API keys",
    description: "Create, rotate and revoke the keys your services send telemetry with.",
    taskId: "P1-13",
  },
  {
    path: "/settings/members",
    title: "Members",
    description: "Who can see this organisation's data, and what each of them may do.",
    taskId: "P1-59",
  },
  {
    path: "/settings/data",
    title: "Data handling",
    description: "Retention windows and the masking rules applied before anything is written.",
    taskId: "P1-24",
  },
];

/** Rendered outside the shell: there is no project, time range or navigation yet. */
export const BARE_SCREENS: ScreenDefinition[] = [
  {
    path: "/login",
    title: "Sign in",
    description: "Session sign-in against the control plane.",
    taskId: "P1-17",
  },
  {
    path: "/signup",
    title: "Create an account",
    description:
      "Company name, email, password — and behind it one transaction that creates the organization, the owner, a production project and the first API key. The screen itself never says either word.",
    taskId: "P1-49",
  },
  {
    path: "/onboarding",
    title: "Send your first span",
    description:
      "Two environment variables, then wait for the first span to arrive and say so out loud.",
    taskId: "P1-49",
  },
];

/** Sidebar grouping. Ordered by how often the screen is the answer, not alphabetically. */
export const NAV_SECTIONS: NavSection[] = [
  {
    label: "Signals",
    items: [
      { path: "/services", title: "Services" },
      { path: "/service-map", title: "Service map" },
      { path: "/traces", title: "Traces" },
      { path: "/errors", title: "Errors" },
      { path: "/logs", title: "Logs" },
    ],
  },
  {
    label: "Surfaces",
    items: [
      { path: "/rum", title: "Real user monitoring" },
      { path: "/synthetics", title: "Synthetics" },
      { path: "/database", title: "Database" },
      { path: "/infrastructure", title: "Infrastructure" },
    ],
  },
  {
    label: "Account",
    items: [
      { path: "/usage", title: "Usage" },
      { path: "/settings/project", title: "Settings" },
    ],
  },
];
