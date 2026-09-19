// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import { identityFor } from "@/shared/identity.test-util";

import {
  checkDefinition,
  checkRun,
  json,
  mountAt,
  resultSummary,
  stubApi,
  syntheticsReads,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * F26, AC4: a viewer reads every synthetics screen and is shown no control the API
 * would refuse — no New check, no Import, no Pause, no Settings tab, no Delete, no way
 * to issue a heartbeat URL, and no "Alert me about this".
 */

afterEach(unmountAll);

const asViewer = { "GET /auth/me": json(identityFor("viewer")) };

/** The check-page controls, by their real labels — proved real by the owner case below. */
const CONTROLS = ["Pause", "Issue a new URL", "Delete…"];

const buttons = (element: Element) =>
  [...element.querySelectorAll("button")].map((button) => button.textContent?.trim());
const links = (element: Element) =>
  [...element.querySelectorAll("a")].map((link) => link.textContent?.trim());

describe("synthetics, as a viewer", () => {
  it("lists checks with no way to make one", async () => {
    const check = checkDefinition({ name: "Checkout API" });
    stubApi({
      ...syntheticsReads({ checks: [check], results: [resultSummary({ check_id: check.id })] }),
      ...asViewer,
    });
    const { container } = await mountAt("/synthetics");
    await waitFor(() => container.textContent?.includes("Checkout API"), "the list");

    expect(links(container)).not.toContain("New check");
    expect(links(container)).not.toContain("Import");
  });

  it("shows a check's overview with nothing to change", async () => {
    const check = checkDefinition({ name: "Nightly job", type: "heartbeat" });
    stubApi({
      ...syntheticsReads({
        checks: [check],
        results: [resultSummary({ check_id: check.id })],
        runs: { [check.id]: [checkRun()] },
      }),
      ...asViewer,
    });
    const { container } = await mountAt(`/synthetics/${check.id}`);
    await waitFor(() => container.querySelector("h1"), "the check");

    const offered = buttons(container);
    for (const control of CONTROLS) {
      expect(offered).not.toContain(control);
    }
    expect(container.textContent).not.toContain("Settings");
    expect(links(container)).not.toContain("Alert me about this");
  });

  it("is shown those same controls when an owner (so the labels above are real)", async () => {
    const check = checkDefinition({ name: "Nightly job", type: "heartbeat" });
    stubApi(
      syntheticsReads({
        checks: [check],
        results: [resultSummary({ check_id: check.id })],
        runs: { [check.id]: [checkRun()] },
      }),
    );
    const { container } = await mountAt(`/synthetics/${check.id}`);
    await waitFor(() => container.querySelector("h1"), "the check");

    for (const control of CONTROLS) expect(buttons(container)).toContain(control);
  });

  it("refuses the new-check screen opened by its address", async () => {
    stubApi({ ...syntheticsReads(), ...asViewer });
    const { container } = await mountAt("/synthetics/new");
    await waitFor(() => container.textContent?.includes("cannot open this"), "the refusal");

    expect(container.querySelector("form")).toBeNull();
  });
});
