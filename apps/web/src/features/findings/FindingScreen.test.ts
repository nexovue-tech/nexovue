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
  "False alarm",
  "This is the new normal",
  "Snooze this service",
  "Mute this service",
];

describe("the finding page", () => {
  it("offers the way back to the feed, which is the front door at / (FR1)", async () => {
    stubApi(finding());
    const { container } = await mountFinding();
    await heading(container);

    const back = [...container.querySelectorAll<HTMLAnchorElement>("a")].find(
      (link) => (link.textContent ?? "").trim() === "All findings",
    );
    expect(back?.getAttribute("href")).toBe("/");
  });

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
    // The band and the evidence were stored with the finding, so they outlive the chart.
    expect(text).toContain("Normal band 110ms to 165ms.");
    expect(text).toContain("POST /pay: p95 400ms, against 100ms the same time last week.");
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

  it("offers a member false alarm and a snooze, but not the new normal or a mute (AC39, FR22)", async () => {
    stubApi(finding(), { role: "member" });
    const { container } = await mountFinding();

    await heading(container);
    expect(buttons(container).filter((label) => ACTIONS.includes(label))).toEqual([
      "False alarm",
      "Snooze this service",
    ]);
    // D47: Take this replaced Acknowledge, in the case's own group.
    expect(buttons(container)).toContain("Take this");
    expect(buttons(container)).not.toContain("Acknowledge");
  });

  it("offers an admin all four", async () => {
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
    // The note box is a textarea too (F22); the reason field is the capped one.
    const reason = [...container.querySelectorAll("textarea")].find(
      (field) => field.maxLength === 1000,
    );
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
          "PATCH /findings/f-1/case": problem(409, "This case changed since you opened it."),
        },
      },
    );
    const { container } = await mountFinding();
    await heading(container);

    await press(container, "Take this");

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
    expect(buttons(container).filter((label) => ACTIONS.includes(label))).toEqual(["False alarm"]);
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

  it("still says the service is snoozed after a reload, and offers to end it", async () => {
    const api = stubApi(finding({ service_snoozed_until: minutesAgo(-30) }), {
      role: "member",
      extra: {
        "POST /services/checkout/snooze": json({ muted_until: null, snoozed_until: null }),
      },
    });
    const { container } = await mountFinding();
    await heading(container);

    expect(readable(container)).toContain("checkout is snoozed until");
    expect(readable(container)).not.toContain("Snooze this service");
    await press(container, "End snooze");

    await waitFor(() => api.to("POST", "/services/checkout/snooze").length === 1, "the end");
    expect(api.to("POST", "/services/checkout/snooze")[0]?.body).toEqual({
      project_id: PROJECT_ID,
      minutes: null,
    });
  });

  it("still says the service is muted after a reload, and offers to unmute", async () => {
    stubApi(finding({ service_muted_until: minutesAgo(-30) }));
    const { container } = await mountFinding();
    await heading(container);

    expect(readable(container)).toContain("checkout is muted until");
    expect(readable(container)).not.toContain("Mute this service");
    expect(readable(container)).toContain("Unmute");
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

describe("the case on the page (F22, T1.12)", () => {
  const investigating = () =>
    finding({
      case_state: "investigating",
      case_opened_at: minutesAgo(20),
      case_taken_at: minutesAgo(20),
      assignee_id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0001",
      assignee_name: "owner",
    });

  it("puts the work before the evidence, in one DOM order (D86)", async () => {
    stubApi(finding(), { role: "member" });
    const { container } = await mountFinding();
    await heading(container);

    const text = readable(container);
    expect(text.indexOf("The case")).toBeGreaterThan(-1);
    expect(text.indexOf("The case")).toBeLessThan(text.indexOf("The signal"));
    expect(text.indexOf("The signal")).toBeLessThan(text.indexOf("What moved"));
    expect(container.querySelector('a[href="#finding-evidence"]')).not.toBeNull();
  });

  it("takes a finding with one click and lands focus on the case line", async () => {
    const api = stubApi(finding(), {
      role: "member",
      extra: {
        "PATCH /findings/f-1/case": json({
          finding: investigating(),
          email_outcome: "none",
        }),
      },
    });
    const { container } = await mountFinding();
    await heading(container);

    await press(container, "Take this");

    expect(api.to("PATCH", "/findings/f-1/case")[0]?.body).toEqual({
      case_state: "investigating",
    });
    await waitFor(() => readable(container).includes("Yours."), "the done line");
    const line = container.querySelector('[data-testid="case-line"]');
    await waitFor(() => document.activeElement === line, "focus on the case line");
  });

  it("renders a note's hostile text as those exact characters, nothing clickable (AC18)", async () => {
    const hostile = "<script>alert(1)</script> [link](https://evil.example) https://evil.example";
    stubApi(investigating(), {
      role: "member",
      extra: {
        "GET /findings/f-1/timeline": json({
          events: [
            {
              id: "e1",
              kind: "note_added",
              at: minutesAgo(5),
              actor_kind: "person",
              actor_id: "someone-else",
              actor_name: "Asha",
              detail: { note_id: "n1" },
              note_body: hostile,
            },
          ],
          next_before: null,
        }),
      },
    });
    const { container } = await mountFinding();
    await heading(container);

    await waitFor(() => readable(container).includes("alert(1)"), "the note's text");
    expect(readable(container)).toContain("[link](https://evil.example)");
    expect(container.querySelector("script")).toBeNull();
    const links = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href") ?? "");
    expect(links.some((href) => href.includes("evil.example"))).toBe(false);
  });

  it("sends a note with the button or Ctrl+Enter, and a failed send keeps the draft (AC104)", async () => {
    let fail = true;
    const api = stubApi(finding(), {
      role: "member",
      extra: {
        "POST /findings/f-1/notes": (call) => {
          if (fail) {
            return new Response(JSON.stringify({ status: 400, detail: "refused" }), {
              status: 400,
              headers: { "content-type": "application/problem+json" },
            });
          }
          return new Response(
            JSON.stringify({
              note: {
                id: "n1",
                author_id: "me",
                author_name: "member",
                body: (call.body as { body: string }).body,
                created_at: minutesAgo(0),
              },
              finding: investigating(),
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        },
      },
    });
    const { container } = await mountFinding();
    await heading(container);

    const box = [...container.querySelectorAll("textarea")].find((f) => f.maxLength !== 1000);
    if (!box) throw new Error("no note box");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setter?.call(box, "tried restarting");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await press(container, "Add note");
    await waitFor(() => readable(container).includes("It is still here"), "the kept draft line");
    expect(box.value).toBe("tried restarting");

    fail = false;
    await act(async () => {
      box.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true }),
      );
    });
    await settle();
    await waitFor(() => box.value === "", "the sent draft clearing");
    expect(api.to("POST", "/findings/f-1/notes").length).toBe(2);
  });

  it("refuses a 4,001-character note inline, sending nothing (AC104)", async () => {
    const api = stubApi(finding(), { role: "member" });
    const { container } = await mountFinding();
    await heading(container);

    const box = [...container.querySelectorAll("textarea")].find((f) => f.maxLength !== 1000);
    if (!box) throw new Error("no note box");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setter?.call(box, "x".repeat(4001));
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await press(container, "Add note");

    expect(readable(container)).toContain("at most 4000 characters");
    expect(api.to("POST", "/findings/f-1/notes").length).toBe(0);
  });

  it("shows the empty-timeline sentence for a finding with no events (AC106)", async () => {
    stubApi(finding(), { role: "viewer" });
    const { container } = await mountFinding();
    await heading(container);

    await waitFor(
      () => readable(container).includes("No events for this finding."),
      "the empty-timeline sentence",
    );
  });

  it("reassigns through a panel with a Nobody option; choosing alone does not act (AC132)", async () => {
    const patches: unknown[] = [];
    const api = stubApi(investigating(), {
      role: "owner",
      extra: {
        "GET /members": json({
          members: [
            {
              id: "m1",
              email: "ravi@example.test",
              name: "Ravi",
              role: "member",
              status: "active",
            },
            { id: "v1", email: "v@example.test", name: "Viewer", role: "viewer", status: "active" },
          ],
        }),
        "PATCH /findings/f-1/case": (call) => {
          patches.push(call.body);
          return new Response(
            JSON.stringify({
              finding: finding({
                case_state: "investigating",
                case_opened_at: minutesAgo(20),
                assignee_id: "m1",
                assignee_name: "Ravi",
              }),
              email_outcome: "not_configured",
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        },
      },
    });
    const { container } = await mountFinding();
    await heading(container);

    await press(container, "Reassign");
    const select = await waitFor(() => container.querySelector("select"), "the picker");
    const options = [...select.querySelectorAll("option")].map((o) => readable(o));
    expect(options).toContain("Nobody");
    expect(options).toContain("Ravi");
    // AC62: the picker never offers a viewer.
    expect(options).not.toContain("Viewer");

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
      setter?.call(select, "m1");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await settle();
    expect(patches.length).toBe(0);

    await press(container, "Assign");
    // FR4: the call carries what the page last showed — the current assignee.
    expect(patches).toEqual([
      { assignee_id: "m1", expected_assignee_id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0001" },
    ]);
    await waitFor(
      () =>
        readable(container).includes(
          "Assigned to Ravi. Email is not set up here, so tell them yourself.",
        ),
      "the truthful email line",
    );
    expect(api.to("GET", "/members").length).toBeGreaterThan(0);
  });
});
