// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import {
  alertReads,
  incident,
  json,
  mountAt,
  problem,
  readable,
  rule,
  stubApi,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * The defects these guard: an open incident told apart only by colour (principle 6),
 * a rule notifying nobody that looks healthy (FR25), and an empty or failed section
 * that reads as "all is well" (principle 3, AC25).
 */

afterEach(unmountAll);

describe("the Alerts screen", () => {
  it("puts what is open first, with its severity in words, its reason and who was told", async () => {
    stubApi(alertReads({ incidents: { open: [incident()], recent: [incident()] } }));
    const { container } = await mountAt("/alerts");

    const open = await waitFor(
      () => container.querySelector('section[aria-labelledby="open-incidents"] li'),
      "the open incident",
    );
    const text = readable(open);
    expect(text).toContain("critical");
    expect(text).toContain("Orders API");
    expect(text).toContain("is down");
    expect(text).toContain("Why: the check answered 503 where under 400 was expected");
    expect(text).toContain("Told: Organization owners");
  });

  it("says nothing is open rather than showing an empty list", async () => {
    stubApi(
      alertReads({
        incidents: { open: [], recent: [incident({ resolved_at: new Date().toISOString() })] },
      }),
    );
    const { container } = await mountAt("/alerts");

    await waitFor(() => readable(container).includes("Nothing is open."), "the nothing-open line");
    expect(readable(container)).toContain("The last incident resolved");
  });

  it("calls a rule with no channel what it is", async () => {
    stubApi(alertReads({ rules: [rule({ channels: [], notifying_nobody: true })] }));
    const { container } = await mountAt("/alerts");

    await waitFor(() => readable(container).includes("Not notifying anyone"), "the rule's state");
    expect(readable(container)).toContain("No channels");
  });

  it("offers the way to a first rule when there is none", async () => {
    stubApi(alertReads({ rules: [] }));
    const { container } = await mountAt("/alerts");

    await waitFor(() => readable(container).includes("No rules"), "the empty state");
    expect(container.querySelector('a[href="/alerts/rules/new"]')).not.toBeNull();
  });

  it("says a failed load failed, per section", async () => {
    stubApi({
      ...alertReads(),
      "GET /alerts/incidents": problem(503, "The database is unavailable."),
    });
    const { container } = await mountAt("/alerts");

    await waitFor(
      () => readable(container).includes("Open incidents could not be loaded"),
      "the failure",
    );
    // The rules still load: one failed read is not a failed screen.
    expect(readable(container)).toContain("Any check down");
  });
});

describe("when the service conditions are not being judged", () => {
  it("says so above the incidents, with the reason", async () => {
    stubApi({
      ...alertReads(),
      "GET /alerts/evaluation": json({
        sweeps: [
          {
            name: "thresholds",
            finished_at: new Date().toISOString(),
            skipped: null,
            behind: false,
          },
          {
            name: "silence",
            finished_at: new Date().toISOString(),
            skipped:
              "the usage meter is behind, so a silent service cannot be told from a late one",
            behind: true,
          },
          { name: "new_errors", finished_at: null, skipped: null, behind: true },
        ],
      }),
    });
    const { container } = await mountAt("/alerts");

    await waitFor(
      () => readable(container).includes("Silence rules are not being evaluated now"),
      "the banner",
    );
    expect(readable(container)).toContain("the usage meter is behind");
    expect(readable(container)).toContain(
      "New-error rules are not being evaluated now: they have not run",
    );
  });
});
