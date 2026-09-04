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
});
