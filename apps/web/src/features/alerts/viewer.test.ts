// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import { identityFor } from "@/shared/identity.test-util";

import {
  alertReads,
  channel,
  json,
  mountAt,
  rule,
  stubApi,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * F26, AC4: a viewer reads alerts and channels and is offered nothing the API would
 * refuse. Each "not shown" is paired with the owner seeing it, so a renamed label
 * cannot turn these into tests of nothing.
 */

afterEach(unmountAll);

const as = (role: "owner" | "viewer") => ({ "GET /auth/me": json(identityFor(role)) });

const buttons = (element: Element) =>
  [...element.querySelectorAll("button")].map((button) => button.textContent?.trim());
const links = (element: Element) =>
  [...element.querySelectorAll("a")].map((link) => link.textContent?.trim());

describe("alerts, by role", () => {
  it.each(["owner", "viewer"] as const)("the Alerts screen for an %s", async (role) => {
    stubApi({ ...alertReads({ rules: [rule({ name: "Any check down" })] }), ...as(role) });
    const { container } = await mountAt("/alerts");
    await waitFor(() => container.textContent?.includes("Any check down"), "the rules");

    const ruleLink = [...container.querySelectorAll("a")].some((link) =>
      link.textContent?.includes("Any check down"),
    );
    if (role === "owner") {
      expect(links(container)).toContain("New rule");
      expect(ruleLink).toBe(true);
    } else {
      expect(links(container)).not.toContain("New rule");
      expect(ruleLink).toBe(false);
    }
  });

  it.each(["owner", "viewer"] as const)("the channel list for an %s", async (role) => {
    stubApi({ ...alertReads(), ...as(role) });
    const { container } = await mountAt("/settings/channels");
    await waitFor(() => container.textContent?.includes("Organization owners"), "the channels");

    expect(buttons(container).includes("Connect a channel")).toBe(role === "owner");
  });

  it.each(["owner", "viewer"] as const)("a channel's page for an %s", async (role) => {
    const slack = channel({ id: "ch-slack", name: "#incidents" });
    stubApi({ ...alertReads({ channels: [slack] }), ...as(role) });
    const { container } = await mountAt("/settings/channels/ch-slack");
    await waitFor(() => container.textContent?.includes("#incidents"), "the channel");

    for (const control of ["Send test", "Remove this channel"]) {
      expect(buttons(container).includes(control)).toBe(role === "owner");
    }
  });

  it("refuses the rule editor to a viewer who opens it by its address", async () => {
    stubApi({ ...alertReads(), ...as("viewer") });
    const { container } = await mountAt("/alerts/rules/new");
    await waitFor(() => container.textContent?.includes("cannot open this"), "the refusal");

    expect(container.querySelector("form")).toBeNull();
  });
});
