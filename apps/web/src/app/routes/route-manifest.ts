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
  | "/services"
  | "/service-map"
  | "/traces"
  | "/errors"
  | "/logs"
  | "/rum"
  | "/synthetics"
  | "/infrastructure"
  | "/metrics"
  | "/usage"
  | "/settings/api-keys"
  | "/settings/projects"
  | "/settings/billing"
  | "/settings/profile";

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
    ],
  },
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
      { path: "/infrastructure", title: "Infrastructure" },
      { path: "/metrics", title: "Metrics" },
    ],
  },
  {
    label: "Account",
    items: [
      { path: "/usage", title: "Usage" },
      { path: "/settings/api-keys", title: "API keys" },
      { path: "/settings/projects", title: "Projects" },
      { path: "/settings/billing", title: "Billing" },
      { path: "/settings/profile", title: "Profile & security" },
    ],
  },
];
