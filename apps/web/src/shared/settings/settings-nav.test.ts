import { describe, expect, it } from "vitest";

import { offeredSettings, SETTINGS_GROUPS } from "./settings-nav";

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

describe("runbooks in the rail (F4 T5.3)", () => {
  it("is offered only where AI may be, to everyone signed in, found by a runbook's words", () => {
    const items = SETTINGS_GROUPS.flatMap((group) => group.items);
    const selfHostedOnly = items.filter((item) => item.selfHostedOnly).map((item) => item.path);
    const entry = items.find((item) => item.path === "/settings/runbooks");

    expect(selfHostedOnly).toEqual(["/settings/runbooks"]);
    // Every signed-in person reads runbooks (D48); only writing is gated, on the screen.
    expect(entry?.requires).toBeUndefined();
    expect(entry?.keywords).toEqual(expect.arrayContaining(["runbook", "playbook"]));
  });
});

describe("what the rail offers on each kind of install", () => {
  const paths = (hosted: boolean) =>
    offeredSettings(SETTINGS_GROUPS, { permissions: [], hosted, needle: "" }).flatMap((group) =>
      group.items.map((item) => item.path),
    );

  it("offers runbooks only on a customer's own install, and billing only on the hosted one", () => {
    expect(paths(false)).toContain("/settings/runbooks");
    expect(paths(false)).not.toContain("/settings/billing");
    expect(paths(true)).not.toContain("/settings/runbooks");
    expect(paths(true)).toContain("/settings/billing");
  });
});
