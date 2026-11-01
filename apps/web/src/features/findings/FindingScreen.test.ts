// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it } from "vitest";

import {
  buttons,
  finding,
  json,
  minutesAgo,
  mountFinding,
  PROJECT_ID,
  press,
  problem,
  readable,
  settle,
  stubApi,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * The finding's own page (FR31, design *The finding page*): every state it owns, and
 * what each role may do (AC38 to AC40).
 */

afterEach(unmountAll);

/** The sentence, and then the session: actions follow the role, read alongside. */
async function heading(container: HTMLElement) {
  const found = await waitFor(() => container.querySelector("h1"), "the finding's sentence");
  for (let round = 0; round < 5; round += 1) await settle();
  return found;
}

const ACTIONS = [
  "Acknowledge",
  "False alarm",
  "This is the new normal",
  "Snooze this service",
  "Mute this service",
];

describe("the finding page", () => {
  it("leads with the sentence, then each signal's values, comparison and readings (AC38)", async () => {
    stubApi(finding());
    const { container } = await mountFinding();

    expect((await heading(container)).textContent).toBe(
      "checkout is 3× slower than normal since 14:02 UTC",
    );
    const text = readable(container);
    expect(text).toContain("Slower — 412ms at worst, against 110ms normally. Now 330ms.");
    expect(text).toContain("Compared with Tuesdays around 15:00, last 3 weeks.");
    expect(text).toContain("Open since");
    expect(container.querySelector("figure figcaption")?.textContent).toBe("Slower");
    expect(container.querySelector('a[href="/services/checkout"]')?.textContent).toBe("checkout");
  });

  it("still reads once its readings expired, and says the chart is gone (AC40)", async () => {
    stubApi(
      finding({
        state: "closed",
        closed_reason: "recovered",
        closed_at: minutesAgo(5),
        series: [],
        new_normal: null,
      }),
    );
    const { container } = await mountFinding();

    await heading(container);
    const text = readable(container);
    expect(text).toContain("412ms at worst, against 110ms normally");
    expect(text).toContain("The chart is no longer available");
    expect(text).toContain("Recovered at");
    expect(container.querySelector("figure")).toBeNull();
  });

  it("shows a viewer no action at all (AC39)", async () => {
    stubApi(finding(), { role: "viewer" });
    const { container } = await mountFinding();

    await heading(container);
    expect(buttons(container).filter((label) => ACTIONS.includes(label))).toEqual([]);
  });

  it("offers a member acknowledge, false alarm and a snooze, but not the new normal or a mute (AC39, FR22)", async () => {
    stubApi(finding(), { role: "member" });
    const { container } = await mountFinding();

    await heading(container);
    expect(buttons(container).filter((label) => ACTIONS.includes(label))).toEqual([
      "Acknowledge",
      "False alarm",
      "Snooze this service",
    ]);
  });

  it("offers an admin all five", async () => {
    stubApi(finding(), { role: "admin" });
    const { container } = await mountFinding();

    await heading(container);
    expect(buttons(container).filter((label) => ACTIONS.includes(label))).toEqual(ACTIONS);
  });

  it("keeps only the false alarm on a finding that recovered, and nothing on one closed otherwise", async () => {
    stubApi(finding({ state: "closed", closed_reason: "recovered", closed_at: minutesAgo(5) }));
    const recovered = await mountFinding();
    await heading(recovered.container);
    expect(buttons(recovered.container).filter((label) => ACTIONS.includes(label))).toEqual([
      "False alarm",
    ]);
    unmountAll();

    stubApi(finding({ state: "closed", closed_reason: "adopted", closed_at: minutesAgo(5) }));
    const adopted = await mountFinding();
    await heading(adopted.container);
    expect(buttons(adopted.container).filter((label) => ACTIONS.includes(label))).toEqual([]);
    expect(readable(adopted.container)).toContain(
      "Accepted as the new normal after 7 days with no answer",
    );
  });

  it("names its new normal in real numbers before it can be confirmed", async () => {
    const api = stubApi(finding(), {
      role: "admin",
      extra: {
        "POST /findings/f-1/new-normal": json({
          finding: finding({ state: "closed", closed_reason: "new_normal", new_normal: null }),
          resolved_to: ["#ops"],
        }),
      },
    });
    const { container } = await mountFinding();
    await heading(container);

    await press(container, "This is the new normal");
    expect(readable(container)).toContain("checkout's normal becomes about 330ms, from 110ms.");
    expect(api.to("POST", "/findings/f-1/new-normal")).toEqual([]);

    await press(container, "Make this the new normal");
    await waitFor(
      () => readable(container).includes("Resolved message sent to #ops."),
      "the success line",
    );
    expect(readable(container)).toContain("Closed as the new normal.");
  });

  it("says when the new level is not known well enough yet, and offers no confirm", async () => {
    stubApi(finding({ new_normal: { available: false, readings: 9, needed: 15, signals: [] } }), {
      role: "admin",
    });
    const { container } = await mountFinding();
    await heading(container);

    await press(container, "This is the new normal");
    expect(readable(container)).toContain("known from 9 of the 15 readings it needs");
    expect(buttons(container)).not.toContain("Make this the new normal");
  });

  it("closes a false alarm with its reason and says who heard it close", async () => {
    const api = stubApi(finding(), {
      role: "member",
      extra: {
        "POST /findings/f-1/false-alarm": json({
          finding: finding({ state: "closed", closed_reason: "false_alarm", new_normal: null }),
          resolved_to: ["Organization owners"],
        }),
      },
    });
    const { container } = await mountFinding();
    await heading(container);

    await press(container, "False alarm");
    expect(readable(container)).toContain("only an admin's mark returns them");
    const reason = container.querySelector("textarea");
    if (!reason) throw new Error("no reason field");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setter?.call(reason, "our load test");
      reason.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await press(container, "Close as a false alarm");

    await waitFor(
      () => readable(container).includes("Resolved message sent to Organization owners."),
      "the success line",
    );
    expect(api.to("POST", "/findings/f-1/false-alarm")[0]?.body).toEqual({
      reason: "our load test",
    });
  });

  it("says a finding changed meanwhile, and reads it again (409)", async () => {
    let reads = 0;
    const api = stubApi(
      () => {
        reads += 1;
        const body =
          reads === 1
            ? finding()
            : finding({ state: "closed", closed_reason: "recovered", closed_at: minutesAgo(1) });
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      },
      {
        role: "member",
        extra: {
          "POST /findings/f-1/acknowledge": problem(
            409,
            "This finding has closed or was already acknowledged since you opened it.",
          ),
        },
      },
    );
    const { container } = await mountFinding();
    await heading(container);

    await press(container, "Acknowledge");

    await waitFor(
      () => readable(container).includes("It is shown as it is now."),
      "the changed-meanwhile line",
    );
    await waitFor(() => readable(container).includes("Recovered at"), "the finding read again");
    expect(api.to("GET", "/findings/f-1").length).toBe(2);
  });

  it("lists a project finding's services, and draws no chart of its own", async () => {
    stubApi(
      finding({
        scope: "project",
        service_name: null,
        severity: "critical",
        title:
          "Traffic stopped arriving from 2 of 3 services at 14:02 UTC. Check your collector, your network or nexovue's intake",
        services: ["cart", "checkout"],
        series: [],
        new_normal: null,
      }),
      { role: "admin" },
    );
    const { container } = await mountFinding();
    await heading(container);

    expect(readable(container)).toContain("Services affected");
    expect(container.querySelector('a[href="/services/cart"]')).not.toBeNull();
    expect(readable(container)).not.toContain("The chart is no longer available");
    expect(buttons(container).filter((label) => ACTIONS.includes(label))).toEqual([
      "Acknowledge",
      "False alarm",
    ]);
  });

  it("opens a project finding's services in the finding's own project", async () => {
    const other = "0d3c5a7e-1111-4c2f-9b5d-1f3f7d6a0999";
    stubApi(
      finding({
        project_id: other,
        scope: "project",
        service_name: null,
        services: ["cart"],
        series: [],
        new_normal: null,
      }),
      {
        extra: {
          [`GET /projects/${other}/detection`]: json({ sensitivity: "normal", timezone: "UTC" }),
        },
      },
    );
    const { container } = await mountFinding();
    await heading(container);

    const link = container.querySelector<HTMLAnchorElement>('a[href="/services/cart"]');
    if (!link) throw new Error("no link to cart");
    await act(async () => link.click());

    expect(JSON.parse(localStorage.getItem("nexovue.selection") ?? "{}").projectId).toBe(other);
  });

  it("says a muted finding sends nothing, and links its threshold incident", async () => {
    stubApi(
      finding({
        muted: true,
        incidents: [
          {
            id: "inc-1",
            rule_name: "Checkout p95",
            condition_kind: "service_latency_p95",
            opened_at: minutesAgo(20),
            resolved_at: null,
          },
        ],
      }),
    );
    const { container } = await mountFinding();
    await heading(container);

    expect(readable(container)).toContain("nothing was or will be sent about it");
    expect(container.querySelector('a[href="/alerts/incidents/inc-1"]')?.textContent).toBe(
      "Checkout p95",
    );
  });

  it("says it could not load rather than showing an empty finding", async () => {
    stubApi(problem(404, "No such finding."));
    const { container } = await mountFinding();

    await waitFor(
      () => readable(container).includes("The finding could not be loaded"),
      "the failure",
    );
    expect(readable(container)).toContain("No such finding.");
  });

  it("mutes the service for a chosen time, and says until when", async () => {
    const api = stubApi(finding(), {
      role: "admin",
      extra: {
        "PUT /services/checkout/mute": json({
          muted_until: minutesAgo(-1440),
          snoozed_until: null,
        }),
      },
    });
    const { container } = await mountFinding();
    await heading(container);

    await press(container, "Mute this service");
    await press(container, "Mute checkout");

    await waitFor(() => readable(container).includes("checkout is muted until"), "the mute");
    const [call] = api.to("PUT", "/services/checkout/mute");
    // A length, so the server's clock says when it ends: a day, the default.
    expect(call?.body).toEqual({ project_id: PROJECT_ID, minutes: 1440 });
  });

  it("lets a member snooze the service, for a length the server counts (FR22, FR31)", async () => {
    const api = stubApi(finding(), {
      role: "member",
      extra: {
        "POST /services/checkout/snooze": json({
          muted_until: null,
          snoozed_until: minutesAgo(-30),
        }),
      },
    });
    const { container } = await mountFinding();
    await heading(container);

    await press(container, "Snooze this service");
    expect(readable(container)).toContain("its minutes stay out of its normal");
    await press(container, "Snooze checkout");

    await waitFor(() => readable(container).includes("checkout is snoozed until"), "the snooze");
    expect(api.to("POST", "/services/checkout/snooze")[0]?.body).toEqual({
      project_id: PROJECT_ID,
      minutes: 30,
    });
  });

  it("shows the API's word when a member's snoozes have used the day's 4 hours", async () => {
    stubApi(finding(), {
      role: "member",
      extra: {
        "POST /services/checkout/snooze": problem(
          409,
          "Members may snooze a service for at most 4 hours in any 24, and this snooze would pass that.",
        ),
      },
    });
    const { container } = await mountFinding();
    await heading(container);

    await press(container, "Snooze this service");
    await press(container, "Snooze checkout");

    await waitFor(() => readable(container).includes("at most 4 hours in any 24"), "the refusal");
  });

  it("moves focus into a confirmation when it opens", async () => {
    stubApi(finding(), { role: "member" });
    const { container } = await mountFinding();
    await heading(container);

    await press(container, "False alarm");

    const panel = container.querySelector("fieldset");
    expect(panel).not.toBeNull();
    expect(panel?.contains(document.activeElement)).toBe(true);
  });

  it("keeps its announcement's live region in place before anything is done", async () => {
    stubApi(finding(), { role: "member" });
    const { container } = await mountFinding();
    await heading(container);

    const status = container.querySelector('section[aria-label="What you can do"] [role="status"]');
    expect(status).not.toBeNull();
    expect(readable(status as Element)).toBe("");
  });
});
