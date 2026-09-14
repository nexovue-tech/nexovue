import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "../router";
import { NAV_SECTIONS } from "./route-manifest";

/**
 * The defect this guards: the sidebar offered "Settings" at `/settings/project`, a
 * screen the single-operator decision (plan §9) means nobody will ever build. The
 * manifest promises that navigation and the route tree cannot disagree; nothing
 * enforced it, so a nav entry could outlive the route under it and land on the 404.
 */

/** The catch-all. Matching only this is exactly the "clicked it and got nothing" bug. */
const NOT_FOUND_ROUTE_PATH = "/$";

function servedPaths(): Set<string> {
  const router = createAppRouter(new QueryClient());
  return new Set(Object.keys(router.routesByPath).filter((path) => path !== NOT_FOUND_ROUTE_PATH));
}

describe("the sidebar", () => {
  it("offers only paths the router serves with a screen of their own", () => {
    const served = servedPaths();
    const offered = NAV_SECTIONS.flatMap((section) => section.items.map((item) => item.path));

    expect(offered.filter((path) => !served.has(path))).toEqual([]);
  });

  it("names every screen once, so no two entries lead to the same place", () => {
    const offered = NAV_SECTIONS.flatMap((section) => section.items.map((item) => item.path));

    expect(offered).toEqual([...new Set(offered)]);
  });

  it("offers every screen a person is meant to reach from inside the app", () => {
    // The other direction, and the one that was missing. The test above catches
    // a nav entry whose route was deleted; nothing caught a *route* the sidebar
    // never named — and Findings was exactly that for the whole of Phase 1. It
    // had a screen, a placeholder and a task id, and nothing in the running app
    // navigated to it.
    //
    // The exceptions are listed rather than pattern-matched: a screen reached
    // only by following something (a trace, a service) or only while signed out
    // has nothing to put in a sidebar, and each entry here is that claim made
    // explicitly.
    const reachedByFollowingSomething = new Set([
      "/services/$name",
      "/traces/$traceId",
      "/onboarding",
    ]);
    const signedOut = new Set([
      "/login",
      "/signup",
      "/forgot-password",
      "/reset-password",
      "/verify-email",
      // The setup documentation. Reachable from the sidebar, but as a plain
      // anchor rather than a nav item: it is not a screen of this console, takes
      // neither the project nor the window with it, and opens in its own tab.
      "/docs",
      "/docs/$",
    ]);

    const offered = new Set<string>(
      NAV_SECTIONS.flatMap((section) => section.items.map((item) => item.path)),
    );
    const unreachable = [...servedPaths()].filter(
      (path) =>
        !offered.has(path) && !reachedByFollowingSomething.has(path) && !signedOut.has(path),
    );

    expect(unreachable).toEqual([]);
  });
});
