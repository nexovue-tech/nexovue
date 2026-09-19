// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import {
  alertReads,
  button,
  channel,
  click,
  field,
  incident,
  json,
  mountAt,
  problem,
  readable,
  rule,
  stubApi,
  submit,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

afterEach(unmountAll);

const checkbox = (container: Element, label: string) =>
  waitFor(
    () =>
      [...container.querySelectorAll("label")]
        .find((candidate) => readable(candidate).startsWith(label))
        ?.querySelector<HTMLInputElement>('input[type="checkbox"]'),
    `the checkbox labelled ${label}`,
  );

describe("a new rule", () => {
  it("arrives from a check's page watching that check, and saves what the scheduler reads", async () => {
    const api = stubApi({
      ...alertReads({ channels: [channel()] }),
      "POST /alerts/rules": json(rule({ id: "rule-new", name: "Orders API is down" }), 201),
      "GET /alerts/rules/rule-new": json(rule({ id: "rule-new", name: "Orders API is down" })),
    });
    const { container, router } = await mountAt("/alerts/rules/new?check=chk-1");

    await waitFor(() => container.querySelector("form"), "the form");
    expect(field(container, "rule-name").value).toBe("Orders API is down");
    await click(await checkbox(container, "#incidents"));
    await submit(container.querySelector("form") as HTMLFormElement);

    const [sent] = await waitFor(() => api.to("POST", "/alerts/rules"), "the save");
    expect(sent.body).toMatchObject({
      name: "Orders API is down",
      condition: { kind: "check_state", check_ids: ["chk-1"], states: { down: "critical" } },
      channel_ids: ["ch-slack"],
      renotify_minutes: null,
    });
    await waitFor(
      () => router.state.location.pathname === "/alerts/rules/rule-new",
      "the saved rule's page",
    );
  });

  it("will not save a rule that tells nobody", async () => {
    const api = stubApi(alertReads({ channels: [channel()] }));
    const { container } = await mountAt("/alerts/rules/new");

    await waitFor(() => container.querySelector("form"), "the form");
    await submit(container.querySelector("form") as HTMLFormElement);

    expect(readable(container)).toContain("A rule with no channel tells nobody.");
    expect(api.to("POST", "/alerts/rules")).toEqual([]);
  });

  it("puts the API's refusal on the field it names", async () => {
    stubApi({
      ...alertReads({ channels: [channel()] }),
      "POST /alerts/rules": problem(422, "This rule cannot be saved.", {
        errors: [{ field: "condition.check_ids", reason: "no such check in this project" }],
      }),
    });
    const { container } = await mountAt("/alerts/rules/new?check=chk-1");

    await waitFor(() => container.querySelector("form"), "the form");
    await click(await checkbox(container, "#incidents"));
    await submit(container.querySelector("form") as HTMLFormElement);

    await waitFor(
      () => readable(container).includes("No such check in this project."),
      "the placed error",
    );
  });
});

describe("an existing rule", () => {
  it("can be muted for an hour, and says so", async () => {
    const muted = rule({ muted_until: new Date(Date.now() + 3_600_000).toISOString() });
    const api = stubApi({
      ...alertReads(),
      "PUT /alerts/rules/rule-default/mute": json(muted),
    });
    const { container } = await mountAt("/alerts/rules/rule-default");

    await waitFor(() => readable(container).includes("Mute for"), "the mute controls");
    await click(button(container, "1 hour"));

    const [sent] = await waitFor(
      () => api.to("PUT", "/alerts/rules/rule-default/mute"),
      "the mute",
    );
    const until = new Date((sent.body as { until: string }).until).getTime();
    expect(Math.round((until - Date.now()) / 60_000)).toBe(60);
  });
});

describe("a service rule", () => {
  it("arrives from a service's page as an error-rate rule on that service, with the last hour beside it", async () => {
    const api = stubApi({
      ...alertReads({ channels: [channel()] }),
      "POST /alerts/rules": json(rule({ id: "rule-svc" }), 201),
      "GET /alerts/rules/rule-svc": json(rule({ id: "rule-svc" })),
    });
    const { container } = await mountAt("/alerts/rules/new?service=checkout");

    await waitFor(() => readable(container).includes("Last hour:"), "the last hour's reading");
    expect(readable(container)).toContain("checkout — 1.2% errors over 3,400 requests");
    await click(await checkbox(container, "#incidents"));
    await submit(container.querySelector("form") as HTMLFormElement);

    const [sent] = await waitFor(() => api.to("POST", "/alerts/rules"), "the save");
    expect(sent.body).toMatchObject({
      name: "checkout error rate",
      condition: {
        kind: "service_error_rate",
        services: ["checkout"],
        threshold_percent: 5,
        window_minutes: 5,
        min_requests: 20,
        fire_after_minutes: 5,
        resolve_after_minutes: 5,
        severity: "critical",
      },
    });
  });
});

describe("a rule for a quiet service", () => {
  it("still shows the service it is for, ticked", async () => {
    stubApi({ ...alertReads({ channels: [channel()] }), "GET /services": json({ services: [] }) });
    const { container } = await mountAt("/alerts/rules/new?service=billing");

    const billing = await checkbox(container, "billing");
    expect(billing.checked).toBe(true);
  });
});

describe("deleting a rule", () => {
  it("takes its open incident off the Alerts screen at once, not after the next refresh", async () => {
    let open = [incident()];
    const api = stubApi({
      ...alertReads(),
      "GET /alerts/incidents": () =>
        new Response(JSON.stringify({ open, recent: [] }), {
          headers: { "content-type": "application/json" },
        }),
      "DELETE /alerts/rules/rule-default": () => {
        open = [];
        return new Response(null, { status: 204 });
      },
    });
    const { container, router } = await mountAt("/alerts");
    await waitFor(() => readable(container).includes("Orders API"), "the open incident");

    await router.navigate({ to: "/alerts/rules/$ruleId", params: { ruleId: "rule-default" } });
    await waitFor(() => readable(container).includes("Mute for"), "the rule's page");
    await click(button(container, "Delete"));
    await click(button(container, "Yes, delete Any check down"));

    await waitFor(() => api.to("DELETE", "/alerts/rules/rule-default").length > 0, "the delete");
    await waitFor(
      () => readable(container).includes("Nothing is open."),
      "the Alerts screen without the closed incident",
    );
  });
});

describe("a saved rule", () => {
  it("states its kind rather than offering a change the API refuses", async () => {
    stubApi(alertReads());
    const { container } = await mountAt("/alerts/rules/rule-default");
    await waitFor(() => readable(container).includes("create another rule"), "the fixed kind");
    const kinds = [...container.querySelectorAll("select")].filter((select) =>
      [...select.options].some((option) => option.value === "service_silent"),
    );
    expect(kinds).toEqual([]);
  });
});
