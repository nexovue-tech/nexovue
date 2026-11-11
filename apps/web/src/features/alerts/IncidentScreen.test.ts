// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import { CASE_IS_NOT_AN_ALERT } from "@/shared/case-words";

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

  it("shows a burn's current figures, as of when, with its target (F19 AC104)", async () => {
    const burning = incident({
      rule_name: "Reliability targets",
      condition_kind: "slo_burn",
      subject_key: "slo:t-1",
      subject_label: "checkout latency under 487.2 ms, 99.9% over 28 days",
      detail: {
        slo_id: "t-1",
        kind: "service_latency",
        service: "checkout",
        objective_percent: "99.9",
        window_days: 28,
        holding: true,
        rate: 4.6,
        hours_left: 60,
        allowed: 672,
        left: 312,
        as_of: minutesAgo(1),
      },
    });
    stubApi({ ...alertReads(), "GET /alerts/incidents/inc-1": json(burning) });
    const { container } = await mountAt("/alerts/incidents/inc-1");

    expect((await heading(container)).textContent).toBe(
      "checkout latency under 487.2 ms, 99.9% over 28 days is using its allowed slow requests 4 times faster than allowed",
    );
    const text = container.textContent ?? "";
    expect(text).toContain("4 times faster than allowed.");
    expect(text).toContain("At this rate, what is left lasts about 2 days.");
    expect(text).toContain("Slow requests still allowed: 312 of 672.");
    expect(text).toContain("As of");
    expect(text).not.toContain("failures");
    expect(container.querySelector('a[href="/slos/t-1"]')?.textContent).toBe("Open the target");
  });

  it("states a burn's finding, last deploy and links to act from, in the project's zone (AC85, AC108)", async () => {
    const opened = new Date(Date.now() - 26 * 3_600_000);
    opened.setUTCSeconds(0, 0);
    const deployed = new Date(opened.getTime() - 11 * 60_000).toISOString();
    const burning = incident({
      rule_name: "Reliability targets",
      condition_kind: "slo_burn",
      subject_key: "slo:t-1",
      subject_label: "checkout availability, 99.9% over 28 days",
      opened_at: opened.toISOString(),
      zone: "Asia/Kolkata",
      detail: { slo_id: "t-1", kind: "service_availability", service: "checkout", rate: 14 },
      burn: {
        findings: [{ id: "f-1", title: "checkout is failing more than normal" }],
        last_deploy_at: deployed,
        deploys_connected: true,
        target_deleted: false,
      },
    });
    stubApi({ ...alertReads(), "GET /alerts/incidents/inc-1": json(burning) });
    const { container } = await mountAt("/alerts/incidents/inc-1");
    await heading(container);
    const text = container.textContent ?? "";

    const clock = new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Asia/Kolkata",
    });
    expect(text).toContain(`${clock.format(new Date(deployed))} Kolkata time, 11 minutes before`);
    // Not today in the project's zone, so the start carries its date (AC108).
    expect(text).toMatch(
      new RegExp(`Started: \\d{1,2} [A-Z][a-z]{2}, ${clock.format(opened)} Kolkata time`),
    );
    expect(container.querySelector('a[href="/findings/f-1"]')?.textContent).toBe(
      "checkout is failing more than normal",
    );
    expect(container.querySelector('a[href="/services/checkout"]')).not.toBeNull();
    expect(container.querySelector('a[href^="/errors"]')?.textContent).toBe(
      "Errors in checkout since this started",
    );
    expect(container.querySelector('a[href="/slos/t-1"]')).not.toBeNull();
    expect(container.querySelector(".max-w-3xl")).toBeNull();
  });

  it("tells no deploy recorded from deploys not connected, and a deleted target (AC85)", async () => {
    const base = {
      rule_name: "Reliability targets",
      condition_kind: "slo_burn" as const,
      subject_key: "slo:t-1",
      subject_label: "checkout availability, 99.9% over 28 days",
      detail: { slo_id: "t-1", kind: "service_availability", service: "checkout" },
    };
    stubApi({
      ...alertReads(),
      "GET /alerts/incidents/inc-1": json(
        incident({
          ...base,
          burn: {
            findings: [],
            last_deploy_at: null,
            deploys_connected: false,
            target_deleted: true,
          },
        }),
      ),
    });
    const unconnected = await mountAt("/alerts/incidents/inc-1");
    await heading(unconnected.container);
    expect(unconnected.container.textContent).toContain("Deploys are not connected.");
    expect(unconnected.container.textContent).toContain(
      "This target has since been deleted. What it recorded is above.",
    );
    expect(unconnected.container.querySelector('a[href="/slos/t-1"]')).toBeNull();
    unmountAll();

    stubApi({
      ...alertReads(),
      "GET /alerts/incidents/inc-1": json(
        incident({
          ...base,
          burn: {
            findings: [],
            last_deploy_at: null,
            deploys_connected: true,
            target_deleted: false,
          },
        }),
      ),
    });
    const none = await mountAt("/alerts/incidents/inc-1");
    await heading(none.container);
    expect(none.container.textContent).toContain("No deploy recorded.");
  });

  it("links a check target's burn to its check (AC85)", async () => {
    stubApi({
      ...alertReads(),
      "GET /alerts/incidents/inc-1": json(
        incident({
          rule_name: "Reliability targets",
          condition_kind: "slo_burn",
          subject_key: "slo:t-2",
          subject_label: "Payment check availability, 99% over 7 days",
          detail: { slo_id: "t-2", kind: "check_availability", check_id: "chk-9" },
          burn: {
            findings: [],
            last_deploy_at: null,
            deploys_connected: false,
            target_deleted: false,
          },
        }),
      ),
    });
    const { container } = await mountAt("/alerts/incidents/inc-1");
    await heading(container);
    expect(container.querySelector('a[href="/synthetics/chk-9"]')?.textContent).toBe(
      "Open the check and its runs",
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

describe("Look next on an incident with no finding (F3 FR53, AC101)", () => {
  const lookNext = (container: HTMLElement) => [
    ...container.querySelectorAll<HTMLAnchorElement>('[aria-labelledby="incident-look-next"] a'),
  ];

  it("opens its service's data for its window", async () => {
    const slow = incident({
      condition_kind: "service_latency_p95",
      subject_key: "service:checkout",
      subject_label: "checkout",
      opened_at: "2026-11-09T14:02:00.000Z",
      resolved_at: "2026-11-09T14:30:00.000Z",
      detail: { service: "checkout", reading: "p95 latency", value: 720, threshold: 500 },
    });
    stubApi({ ...alertReads(), "GET /alerts/incidents/inc-1": json(slow) });
    const { container } = await mountAt("/alerts/incidents/inc-1");
    await heading(container);

    const links = await waitFor(
      () => lookNext(container).length > 0 && lookNext(container),
      "Look next",
    );
    expect(links.map((link) => link.textContent)).toEqual([
      "Traces",
      "Logs",
      "Errors",
      "Deploys",
      "Infrastructure (whole project)",
    ]);
    const traces = new URL(links[0].getAttribute("href") ?? "", "http://x");
    expect(traces.searchParams.get("service")).toBe("checkout");
    expect(traces.searchParams.get("from")).toBe("2026-11-09T13:47:00.000Z");
    expect(traces.searchParams.get("to")).toBe("2026-11-09T14:35:00.000Z");
    expect(traces.searchParams.get("finding")).toBeNull();
  });

  it("opens the project's data for a check's incident, which has no service", async () => {
    stubApi({ ...alertReads(), "GET /alerts/incidents/inc-1": json(incident()) });
    const { container } = await mountAt("/alerts/incidents/inc-1");
    await heading(container);

    const [traces] = await waitFor(
      () => lookNext(container).length > 0 && lookNext(container),
      "Look next",
    );
    expect(
      new URL(traces.getAttribute("href") ?? "", "http://x").searchParams.get("service"),
    ).toBeNull();
  });
});

describe("a case is not an alert (F22 FR53, AC121)", () => {
  it("says a linked alert is a rule's own record and cannot become a case", async () => {
    stubApi({ ...alertReads(), "GET /alerts/incidents/inc-1": json(incident()) });
    const { container } = await mountAt("/alerts/incidents/inc-1");
    await heading(container);

    expect(readable(container)).toContain(CASE_IS_NOT_AN_ALERT);
  });
});
