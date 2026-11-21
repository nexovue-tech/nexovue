import { describe, expect, it } from "vitest";

import { STEP_TARGETS } from "@/features/assistant/step-links";

import consoleRoutes from "./routes/console-routes.json";

/**
 * F4 T3.3: every screen a chat step may open is a console route, so no step offers a link
 * to nowhere. At the app layer, which may see both the feature and the route list.
 */
describe("where a chat step may lead", () => {
  it("names only screens the console routes", () => {
    for (const target of STEP_TARGETS) expect(consoleRoutes).toContain(target);
  });
});
