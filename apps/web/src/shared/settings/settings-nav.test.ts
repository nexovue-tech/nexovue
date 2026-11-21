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

describe("tracker connections in the rail (F22)", () => {
  it("is an owner's and admin's entry, found by the words a person would type", () => {
    const entry = SETTINGS_GROUPS.flatMap((group) => group.items).find(
      (item) => item.path === "/settings/connections",
    );

    expect(entry?.requires).toBe("manage_connections");
    expect(entry?.keywords).toEqual(expect.arrayContaining(["github", "issues", "tracker"]));
    // Nothing public names Jira until Atlassian has answered (F22 T0.1).
    expect(entry?.keywords).not.toContain("jira");
  });
});
