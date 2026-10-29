import { describe, expect, it } from "vitest";

import { SETTINGS_GROUPS } from "./settings-nav";

describe("the settings rail on a customer's own install", () => {
  it("drops billing and support access, which only the hosted service has", () => {
    const hostedOnly = SETTINGS_GROUPS.flatMap((group) => group.items)
      .filter((item) => item.hostedOnly)
      .map((item) => item.path);

    expect(hostedOnly.sort()).toEqual(["/settings/billing", "/settings/support"]);
  });
});
