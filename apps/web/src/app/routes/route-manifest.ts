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
  | "/findings"
  | "/alerts"
  | "/services"
  | "/service-map"
  | "/traces"
  | "/profiles"
  | "/errors"
  | "/logs"
  | "/rum"
  | "/synthetics"
  | "/infrastructure"
  | "/metrics"
  | "/settings/general";

export interface NavSection {
  /** Small-caps rule in the sidebar. */
  label: string;
  items: Array<{ path: NavPath; title: string }>;
}

export const NAV_SECTIONS: NavSection[] = [
  {
    // Both first-class, per the founder's 2026-09-12 decision. Findings had no
    // entry at all until now: it has been a route nothing in the running app
    // navigated to, which is how a screen stays invisible while looking built.
    label: "Home",
    items: [
      { path: "/", title: "Overview" },
      { path: "/findings", title: "Findings" },
      // F21. Beside Findings: both answer "what is wrong", one found by us and one
      // declared by a rule the customer wrote.
      { path: "/alerts", title: "Alerts" },
    ],
  },
  {
    label: "Signals",
    items: [
      { path: "/services", title: "Services" },
      { path: "/service-map", title: "Service map" },
      { path: "/traces", title: "Traces" },
      { path: "/profiles", title: "Profiles" },
      { path: "/errors", title: "Errors" },
      { path: "/logs", title: "Logs" },
    ],
  },
  {
    label: "Surfaces",
    items: [
      { path: "/rum", title: "Real user monitoring" },
      { path: "/synthetics", title: "Synthetics" },
      { path: "/infrastructure", title: "Infrastructure" },
      { path: "/metrics", title: "Metrics" },
    ],
  },
  {
    // One entry, not six. The settings screens have their own rail, which is what
    // lets a reader see what else exists without the main sidebar listing account
    // preferences beside telemetry screens.
    label: "Account",
    items: [{ path: "/settings/general", title: "Settings" }],
  },
];
