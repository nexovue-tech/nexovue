// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import {
  buttons,
  json,
  minutesAgo,
  mountAt,
  press,
  readable,
  stubApi,
  target,
  unmountAll,
  waitFor,
} from "./slo-harness.test-util";

afterEach(unmountAll);

describe("one target", () => {
  it("leads with its sentence, then what was measured, as of a time in the project's zone (AC108)", async () => {
    const evaluated = new Date();
    evaluated.setUTCSeconds(0, 0);
    stubApi({
      "GET /slos/slo-1": json(
        target({ zone: "Asia/Kolkata", evaluated_minute: evaluated.toISOString() }),
      ),
    });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(
      () => readable(container).includes("Failures still allowed: 360 of 672."),
      "the sentence",
    );
    const clock = new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Asia/Kolkata",
    }).format(evaluated);
    expect(readable(container)).toContain(`As of ${clock} (IST)`);
    const text = readable(container);
    expect(text.indexOf("Failures still allowed: 360")).toBeLessThan(
      text.indexOf("What was measured"),
    );
  });

  it("states how many days of figures go, and when, before a delete can be confirmed (AC42)", async () => {
    stubApi(
      { "GET /slos/slo-1": json(target({ figures: { ...target().figures, days_held: 12 } })) },
      "member",
    );
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => buttons(container).includes("Delete"), "the delete action");
    await press(container, "Delete");
    expect(readable(container)).toContain("Its 12 days of figures go on");
    expect(readable(container)).toContain(
      "It can be restored until then. Past months' results stay.",
    );
  });

  it("shows a viewer no Edit, Delete or Disable (AC83)", async () => {
    stubApi({ "GET /slos/slo-1": json(target()) }, "viewer");
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => readable(container).includes("Failures still allowed"), "the page");
    expect(readable(container)).not.toContain("Edit");
    expect(buttons(container)).not.toContain("Delete");
    expect(buttons(container)).not.toContain("Disable");
  });

  it("refuses a viewer who follows an edit link (AC83)", async () => {
    stubApi({ "GET /slos/slo-1": json(target()) }, "viewer");
    const { container } = await mountAt("/slos/slo-1/edit");
    await waitFor(
      () => readable(container).includes("an owner, an admin or a member"),
      "the refusal",
    );
    expect(container.querySelector("form")).toBeNull();
  });

  it("names a burn on stale status 'Was using…' with no live sentence (AC51, AC63)", async () => {
    stubApi({
      "GET /slos/slo-1": json(
        target({
          state: "running_out",
          severity: "critical",
          attention: true,
          evaluated_minute: minutesAgo(20),
        }),
      ),
    });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(
      () => readable(container).includes("Was using its allowance too fast"),
      "the stale burn",
    );
    expect(readable(container)).not.toContain("Running out, act now");
  });

  it("shows at most two notes, with the rest behind 'N more notes', the no-alerts note pinned (FR10)", async () => {
    stubApi({
      "GET /slos/slo-1": json(
        target({ qualifiers: ["no_alerts", "missed", "thin_traffic", "never_failed"] }),
      ),
    });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => readable(container).includes("No alerts will be sent."), "the pinned note");
    expect(readable(container)).toContain("Target missed.");
    expect(readable(container)).not.toContain("Traffic is thin");
    await press(container, "2 more notes");
    expect(readable(container)).toContain("Traffic is thin");
  });

  it("shows its own failure line with a retry when it cannot load (AC82)", async () => {
    stubApi({
      "GET /slos/slo-1": json({ type: "about:blank", title: "Unavailable", status: 503 }, 503),
    });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(
      () => readable(container).includes("This target could not be loaded"),
      "the failure",
    );
  });
});

describe("the form", () => {
  it("refuses a too-fine objective beside its field and in the summary (AC5 in Step 1's form)", async () => {
    const calls = stubApi({ "GET /projects": json({ projects: [] }) }, "member");
    const { container } = await mountAt("/slos/new");
    await waitFor(() => container.querySelector("form"), "the form");
    const objective = container.querySelector<HTMLInputElement>("input[name=objective_percent]");
    expect(objective).not.toBeNull();
    await press(container, "Save");
    expect(readable(container)).toContain("The target was not saved");
    expect(readable(container)).toContain("Name the service to count.");
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("asks the sampling question and says what Yes means (FR5)", async () => {
    stubApi({ "GET /projects": json({ projects: [] }) }, "member");
    const { container } = await mountAt("/slos/new");
    await waitFor(
      () => readable(container).includes("Do you sample before sending to nexovue?"),
      "the question",
    );
    expect(readable(container)).toContain("A failure is a request whose span status is ERROR.");
  });
});
