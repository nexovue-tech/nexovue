/**
 * What the sidebar offers, and nothing else.
 *
 * **The routes themselves are declared in app/router.tsx.** That is why `/login`,
 * `/signup`, `/onboarding` and the three recovery screens are routes with no entry here
 * — a screen nobody navigates to from inside the app has nothing to put in a sidebar.
 * `route-manifest.test.ts` keeps the two from drifting in both directions: it fails if
 * the sidebar offers a path the router does not serve, and if the router serves a screen
 * the sidebar never names.
 *
 * This file also carried a table of every Phase 1 screen with its description and task
 * id, which fed the "coming soon" placeholder. Every one of those screens is now built,
 * so the table and the placeholder went with them — a manifest nothing reads is a
 * manifest that drifts.
 */

/** Only paths that exist in the tree. Keeps the sidebar from pointing at nothing. */
export type NavPath =
  | "/"
  | "/overview"
  | "/alerts"
  | "/slos"
  | "/dashboards"
  | "/services"
  | "/service-map"
  | "/traces"
  | "/profiles"
  | "/llm"
  | "/errors"
  | "/releases"
  | "/logs"
  | "/rum"
  | "/synthetics"
  | "/infrastructure"
  | "/metrics"
  | "/settings/general"
  | "/admin";

export interface NavSection {
  /** Small-caps rule in the sidebar. */
  label: string;
  items: Array<{ path: NavPath; title: string }>;
  /**
   * Shown only to nexovue's own operator. Absent means everyone signed in sees
   * it, which is every other section (`docs/changes/super-admin-in-the-console/`).
   */
  operatorOnly?: boolean;
}

/** Where the account row's Settings opens: the settings rail's first screen (sketch G). */
export const SETTINGS_DOOR = "/settings/general" as const;

export const NAV_SECTIONS: NavSection[] = [
  // Grouped by what the reader came to do (approved sketch G, 2026-11-28), replacing Home,
  // Signals, Surfaces and Account. No screen was removed; Settings moved to the account row.
  {
    // What needs me. Findings holds `/`, the front door (FD15, F20 D58); Alerts beside it,
    // both answering "what is wrong"; targets, "are we meeting them" (F19 D22).
    label: "Respond",
    items: [
      { path: "/", title: "Findings" },
      { path: "/alerts", title: "Alerts" },
      { path: "/slos", title: "Reliability targets" },
    ],
  },
  {
    // Why: the screens a person reads to find the cause.
    label: "Investigate",
    items: [
      { path: "/overview", title: "Overview" },
      { path: "/services", title: "Services" },
      { path: "/service-map", title: "Service map" },
      { path: "/traces", title: "Traces" },
      { path: "/logs", title: "Logs" },
      { path: "/errors", title: "Errors" },
      // F28: a release is context for what the other screens show.
      { path: "/releases", title: "Releases" },
      { path: "/profiles", title: "Profiles" },
      { path: "/llm", title: "LLM calls" },
    ],
  },
  {
    // What a team keeps an eye on: its own dashboards and the surfaces it watches.
    label: "Watch",
    items: [
      { path: "/dashboards", title: "Dashboards" },
      { path: "/synthetics", title: "Synthetics" },
      { path: "/rum", title: "Real user monitoring" },
      { path: "/infrastructure", title: "Infrastructure" },
      { path: "/metrics", title: "Metrics" },
    ],
  },
  {
    // nexovue's own, and nobody else's: the customers of this deployment rather
    // than the data of one. The API refuses it regardless of what is rendered.
    label: "nexovue",
    items: [{ path: "/admin", title: "Platform" }],
    operatorOnly: true,
  },
];
