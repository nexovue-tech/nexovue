// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import {
  alertReads,
  incident,
  json,
  minutesAgo,
  mountAt,
  problem,
  readable,
  stubApi,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * The page every alert message links to. The defect it closes: the link went to the
 * check, so a message about a check deleted since led to "No such check".
 */

afterEach(unmountAll);

const heading = (container: HTMLElement) =>
  waitFor(() => container.querySelector("h1"), "the incident's heading");

describe("the incident page", () => {
  it("says what broke, how long it has been open, why, and who was told", async () => {
    stubApi({ ...alertReads(), "GET /alerts/incidents/inc-1": json(incident()) });
    const { container } = await mountAt("/alerts/incidents/inc-1");

    expect((await heading(container)).textContent).toBe("Orders API is down");
    const text = readable(container);
    expect(text).toContain("Open for 7 min.");
    expect(text).toContain("Why: the check answered 503 where under 400 was expected");
    expect(text).toContain("Opened → Organization owners: delivered");
    expect(
      container.querySelector('a[href="/synthetics/chk-1"]')?.textContent,
      "the evidence is one link away while the check exists",
    ).toBe("Open the check and its runs");
  });

  it("still reads once its check is deleted, and says so instead of linking to nothing", async () => {
    const over = incident({
      resolved_at: minutesAgo(5),
      check_deleted: true,
      notifications: [
        ...incident().notifications,
        {
          id: "n-resolved",
          channel_id: "ch-owners",
          channel_name: "Organization owners",
          channel_kind: "owners",
          kind: "resolved",
          state: "delivered",
          reason: null,
          delivered_at: minutesAgo(5),
        },
      ],
    });
    stubApi({ ...alertReads(), "GET /alerts/incidents/inc-1": json(over) });
    const { container } = await mountAt("/alerts/incidents/inc-1");

    await heading(container);
    const text = readable(container);
    expect(text).toContain("Resolved after 2 min.");
    expect(text).toContain("The check this was about has since been deleted");
    expect(text).toContain("Resolved → Organization owners: delivered");
    expect(container.querySelector('a[href="/synthetics/chk-1"]')).toBeNull();
  });

  it("links a service's incident to the service", async () => {
    const slow = incident({
      condition_kind: "service_latency_p95",
      subject_key: "service:checkout",
      subject_label: "checkout",
      severity: "warning",
      detail: {
        service: "checkout",
        reading: "p95 latency",
        value: 720,
        threshold: 500,
        unit: "ms",
      },
    });
    stubApi({ ...alertReads(), "GET /alerts/incidents/inc-1": json(slow) });
    const { container } = await mountAt("/alerts/incidents/inc-1");

    expect((await heading(container)).textContent).toBe("checkout p95 latency 720 ms, over 500 ms");
    expect(container.querySelector('a[href="/services/checkout"]')?.textContent).toBe(
      "Open checkout",
    );
  });

  it("says it could not load rather than showing an empty incident", async () => {
    stubApi({ ...alertReads(), "GET /alerts/incidents/inc-1": problem(404, "No such incident.") });
    const { container } = await mountAt("/alerts/incidents/inc-1");

    await waitFor(
      () => readable(container).includes("The incident could not be loaded"),
      "the failure",
    );
  });

  it("does not link the Alerts screen's row to a check that is gone", async () => {
    stubApi(
      alertReads({
        incidents: {
          open: [],
          recent: [incident({ resolved_at: minutesAgo(5), check_deleted: true })],
        },
      }),
    );
    const { container } = await mountAt("/alerts");

    await waitFor(
      () => container.querySelector('a[href="/alerts/incidents/inc-1"]'),
      "the row's link to its incident",
    );
    expect(container.querySelector('a[href="/synthetics/chk-1"]')).toBeNull();
  });

  it("is reached from each row of the Alerts screen", async () => {
    stubApi(alertReads({ incidents: { open: [incident()], recent: [] } }));
    const { container } = await mountAt("/alerts");

    const details = await waitFor(
      () => container.querySelector('a[href="/alerts/incidents/inc-1"]'),
      "the row's link to its incident",
    );
    expect(details.textContent).toBe("Details");
  });
});
