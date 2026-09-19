// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import { formatRelativeTime } from "@/shared/format/time";
import { setSelectedRangeId } from "@/shared/selection";

import type { CheckDefinition } from "./api";
import { whySilent } from "./CheckDetailScreen";
import {
  buttonLabelled,
  checkDefinition,
  checkRun,
  click,
  failedEvidence,
  findButton,
  type Handlers,
  held,
  inputLabelled,
  json,
  minutesAgo,
  mountAt,
  noContent,
  problem,
  readable,
  resultSummary,
  stubApi,
  submitForm,
  syntheticsReads,
  typeInto,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * The defects these guard: a failure the page does not open on (US18.15), a delete that
 * can be confirmed before its cost is known (FR37, AC27), a silent check that does not
 * say why (EC11), and a heartbeat URL replaced by one careless click (FR16).
 */

afterEach(unmountAll);

const DOWN = checkDefinition({
  displayed_state: "down",
  confirmed_state: "down",
  state_since: minutesAgo(25),
  interval_seconds: 300,
  locations: ["mumbai"],
});

async function mountCheck(check: CheckDefinition, extra: Handlers = {}, search = "") {
  const api = stubApi({
    ...syntheticsReads({
      checks: [check],
      results: [resultSummary({ check_id: check.id })],
      runs: {
        [check.id]: [
          checkRun({ run_id: "run-up", timestamp: minutesAgo(1) }),
          checkRun({
            run_id: "run-down",
            timestamp: minutesAgo(3),
            up: false,
            outcome: "down",
            attempt: 2,
          }),
          checkRun({ run_id: "run-older", timestamp: minutesAgo(8) }),
        ],
      },
      evidence: [failedEvidence({ check_id: check.id })],
    }),
    ...extra,
  });
  const screen = await mountAt(`/synthetics/${check.id}${search}`);
  await waitFor(() => screen.container.querySelector("h1"), "the check to load");
  return { ...screen, api };
}

/** Mounted with nothing measured in the window, which is when EC11 speaks. */
async function mountSilent(check: CheckDefinition, extra: Handlers = {}) {
  const api = stubApi({ ...syntheticsReads({ checks: [check] }), ...extra });
  const screen = await mountAt(`/synthetics/${check.id}`);
  await waitFor(
    () => screen.container.querySelector('section[aria-label="Readings"] h2'),
    "the readings' empty state",
  );
  return { ...screen, api };
}

describe("a check that does not exist", () => {
  it("says so inside the page, with the way back", async () => {
    stubApi({
      "GET /checks/chk-gone": problem(404, "No check with this id in this organization."),
    });
    const { container } = await mountAt("/synthetics/chk-gone");
    await waitFor(() => readable(container).includes("No such check"), "the not-found state");

    expect(container.querySelector('a[href="/synthetics"]')?.textContent).toBe(
      "Back to every check",
    );
  });
});

describe("the header", () => {
  it("states the check's state, since when, and how it runs", async () => {
    const { container } = await mountCheck(DOWN);

    expect(container.querySelector("h1")?.textContent).toBe("Orders API");
    expect(readable(container.querySelector("header p:last-of-type") as Element)).toBe(
      `down since ${formatRelativeTime(DOWN.state_since ?? "")} · runs every 5 minutes from mumbai`,
    );
  });

  it("pauses by sending only enabled: false, and then offers to resume", async () => {
    const paused = { ...DOWN, enabled: false, displayed_state: "paused" as const };
    const { container, api } = await mountCheck(DOWN, { "PATCH /checks/chk-api": json(paused) });

    await click(buttonLabelled(container, "Pause"));
    await waitFor(() => findButton(container, "Resume"), "the Resume button");

    expect(api.to("PATCH", "/checks/chk-api").map((call) => call.body)).toEqual([
      { enabled: false },
    ]);
    expect(readable(container.querySelector("header") as Element)).toContain("paused");
    expect(container.querySelector('header [role="status"]')?.textContent).toBe("Paused.");
  });
});

describe("runs and evidence", () => {
  it("opens on the failed run, not the newest, and shows what it saw", async () => {
    const { container, api } = await mountCheck(DOWN);
    const runs = await waitFor(
      () => container.querySelector('section[aria-label="Runs"] ol'),
      "the runs",
    );
    await waitFor(() => readable(container).includes("The run failed"), "the evidence");

    const selected = runs.querySelector('[aria-current="true"]');
    expect(readable(selected as Element)).toContain("failed");
    expect(
      api.calls.filter((call) => call.path.startsWith("/synthetics/checks/chk-api/runs/")),
    ).toHaveLength(1);
    expect(api.to("GET", "/synthetics/checks/chk-api/runs/run-down")).toHaveLength(1);
    expect(readable(container)).toContain(
      "The run failed from mumbai on attempt 2: 1 of 2 assertions failed",
    );
    expect(readable(container)).toContain("$.order.id equals 42 — got 41");
  });

  it("opens the run named in the address instead", async () => {
    const { container, api } = await mountCheck(
      DOWN,
      {
        "GET /synthetics/checks/chk-api/runs/run-older": json({
          evidence: failedEvidence({ run_id: "run-older", outcome: "up", error: null }),
        }),
      },
      "?run=run-older",
    );
    await waitFor(() => readable(container).includes("The run passed"), "the evidence");

    expect(api.to("GET", "/synthetics/checks/chk-api/runs/run-down")).toEqual([]);
  });
});

describe("deleting", () => {
  it("states the real number of runs and files before Delete can be pressed", async () => {
    const runCount = held(json({ check_id: "chk-api", runs: 1440 }));
    const fileCount = held(json({ check_id: "chk-api", files: 2 }));
    const { container, router, api } = await mountCheck(DOWN, {
      "GET /synthetics/checks/chk-api/run-count": runCount.handler,
      "GET /checks/chk-api/deletion-impact": fileCount.handler,
      "DELETE /checks/chk-api": noContent,
    });

    await click(buttonLabelled(container, "Delete…"));

    expect(readable(container)).toContain("Counting what would be lost…");
    expect(buttonLabelled(container, "Delete check").disabled).toBe(true);

    runCount.release();
    fileCount.release();
    await waitFor(() => readable(container).includes("1440 recorded runs"), "the counts");

    expect(readable(container)).toContain(
      "Deleting “Orders API” removes its definition, 1440 recorded runs of history and 2 stored files. It cannot be undone.",
    );
    expect(readable(container)).toContain("To stop it without losing anything, pause it instead.");
    expect(api.to("GET", "/synthetics/checks/chk-api/run-count")[0]?.search.get("from")).toBe(
      DOWN.created_at,
    );
    expect(api.to("DELETE", "/checks/chk-api")).toEqual([]);

    await click(buttonLabelled(container, "Delete check"));
    await waitFor(() => router.state.location.pathname === "/synthetics", "the list");

    expect(api.to("DELETE", "/checks/chk-api")).toHaveLength(1);
  });
});

describe("a check with no readings (EC11)", () => {
  it.each([
    [
      "paused",
      checkDefinition({ enabled: false, displayed_state: "paused" }),
      "Paused",
      "This check is paused, so nothing is being run.",
    ],
    [
      "in maintenance",
      checkDefinition({ in_maintenance: true, displayed_state: "maintenance" }),
      "In maintenance",
      "A maintenance window is open.",
    ],
    [
      "not run yet",
      checkDefinition({
        displayed_state: null,
        last_run_at: null,
        created_at: minutesAgo(2),
        interval_seconds: 300,
      }),
      "Not run yet",
      "The first run is due within 5 minutes of saving.",
    ],
    [
      "overdue",
      checkDefinition({ last_run_at: minutesAgo(30), interval_seconds: 60 }),
      "Overdue",
      "The probe workers may be stopped",
    ],
  ])("says it is %s", async (_, check, title, detail) => {
    const { container } = await mountSilent(check);
    const readings = container.querySelector('section[aria-label="Readings"]') as Element;

    expect(readings.querySelector("h2")?.textContent).toBe(title);
    expect(readable(readings)).toContain(detail);
  });

  it("says a check that ran before the window is outside it, and widens the window", async () => {
    setSelectedRangeId("1h");
    try {
      const lastRun = minutesAgo(120);
      const { container } = await mountSilent(
        checkDefinition({ last_run_at: lastRun, interval_seconds: 3600 }),
      );
      const readings = container.querySelector('section[aria-label="Readings"]') as Element;

      expect(readings.querySelector("h2")?.textContent).toBe("No runs in the last hour");
      expect(readable(readings)).toContain(
        `It last ran ${formatRelativeTime(lastRun)}, outside this window.`,
      );
      expect(readable(readings)).not.toContain("Not run yet");

      await click(buttonLabelled(container, "Show the last 6 hours"));
      await waitFor(
        () => readings.querySelector("h2")?.textContent === "No runs in the last 6 hours",
        "the wider window",
      );
    } finally {
      setSelectedRangeId("1h");
    }
  });
});

describe("whySilent", () => {
  const NOW = Date.parse("2026-09-17T12:00:00.000Z");
  const at = (minutesBefore: number) => new Date(NOW - minutesBefore * 60_000).toISOString();
  const LAST_HOUR = { from: at(60), to: at(0), phrase: "the last hour" };
  const silent = (overrides: Partial<CheckDefinition>) =>
    whySilent(
      {
        enabled: true,
        in_maintenance: false,
        last_run_at: null,
        interval_seconds: 60,
        created_at: at(1),
        type: "api",
        ...overrides,
      },
      LAST_HOUR,
      NOW,
    );

  it("puts paused before every other reason", () => {
    expect(silent({ enabled: false, in_maintenance: true, created_at: at(600) }).title).toBe(
      "Paused",
    );
  });

  it("puts maintenance before overdue", () => {
    expect(silent({ in_maintenance: true, created_at: at(600) }).title).toBe("In maintenance");
  });

  it("is not run yet within three intervals of saving, and overdue after", () => {
    expect(silent({ created_at: at(3) }).title).toBe("Not run yet");
    expect(silent({ created_at: at(4) }).title).toBe("Not run yet — and overdue");
  });

  it("is overdue when the last run is more than three intervals old", () => {
    expect(silent({ last_run_at: at(2), created_at: at(600) }).title).not.toBe("Overdue");
    expect(silent({ last_run_at: at(4), created_at: at(600) }).title).toBe("Overdue");
  });

  it("never calls a check that has run 'not run yet'", () => {
    const landing = silent({ last_run_at: at(2), created_at: at(600) });
    expect(landing.title).toBe("No runs in the last hour");
    expect(landing.detail).toContain("has not arrived yet");
    expect(landing.widenTo).toBeUndefined();

    const earlier = silent({ last_run_at: at(300), created_at: at(600), interval_seconds: 7200 });
    expect(earlier.title).toBe("No runs in the last hour");
    expect(earlier.detail).toBe(
      `It last ran ${formatRelativeTime(at(300), NOW)}, outside this window.`,
    );
    expect(earlier.widenTo?.id).toBe("6h");
  });

  it("never says a heartbeat ran, was probed, or is due for a run", () => {
    // A heartbeat is judged on silence. Every sentence this can produce for one has
    // to avoid the probe's vocabulary, not just the branch someone remembered.
    const beats = [
      { last_run_at: null, created_at: at(1) },
      { last_run_at: null, created_at: at(600) },
      { last_run_at: at(2), created_at: at(600) },
      { last_run_at: at(300), created_at: at(600), interval_seconds: 7200 },
      { enabled: false, last_run_at: at(2), created_at: at(600) },
      { in_maintenance: true, last_run_at: at(2), created_at: at(600) },
    ] as const;

    // The claims a heartbeat's screen must never make. "not probed" is fine and
    // deliberate; "it last ran" and "the probe workers" are not.
    const wrong = [/it last ran/i, /probe workers/i, /first run is due/i, /no runs in/i];

    for (const beat of beats) {
      const silence = silent({ type: "heartbeat", ...beat });
      const words = `${silence.title} ${silence.detail}`;
      for (const claim of wrong) expect(words).not.toMatch(claim);
    }
  });

  it("never says a heartbeat ran, was probed, or is due for a run", () => {
    // A heartbeat is judged on silence. Every sentence this can produce for one has
    // to avoid the probe's vocabulary, not just the branch someone remembered.
    const beats = [
      { last_run_at: null, created_at: at(1) },
      { last_run_at: null, created_at: at(600) },
      { last_run_at: at(2), created_at: at(600) },
      { last_run_at: at(300), created_at: at(600), interval_seconds: 7200 },
      { enabled: false, last_run_at: at(2), created_at: at(600) },
      { in_maintenance: true, last_run_at: at(2), created_at: at(600) },
    ] as const;

    // The claims a heartbeat's screen must never make. "not probed" is fine and
    // deliberate; "it last ran" and "the probe workers" are not.
    const wrong = [/it last ran/i, /probe workers/i, /first run is due/i, /no runs in/i];

    for (const beat of beats) {
      const silence = silent({ type: "heartbeat", ...beat });
      const words = `${silence.title} ${silence.detail}`;
      for (const claim of wrong) expect(words).not.toMatch(claim);
    }
  });

  it.each([
    [60, "within one minute of saving"],
    [300, "within 5 minutes of saving"],
    [3600, "within one hour of saving"],
    [21_600, "within 6 hours of saving"],
    [90, "within 90 seconds of saving"],
  ])("names a %i-second interval as a span of time", (interval_seconds, phrase) => {
    expect(silent({ interval_seconds }).detail).toContain(phrase);
  });
});

describe("a heartbeat's URL", () => {
  const HEARTBEAT = checkDefinition({
    id: "chk-beat",
    name: "Nightly export",
    type: "heartbeat",
    config: { type: "heartbeat", grace_seconds: 300 },
    interval_seconds: 86_400,
    last_ping_at: minutesAgo(120),
  });

  it("asks before issuing a new one, and shows the new one once", async () => {
    const { container, api } = await mountSilent(HEARTBEAT, {
      "POST /checks/chk-beat/heartbeat-token": json(
        { token: "hb_new", ping_path: "/api/v1/heartbeat/hb_new" },
        201,
      ),
    });

    await click(buttonLabelled(container, "Issue a new URL"));

    expect(readable(container)).toContain(
      "Issue a new URL? The one your job calls now stops working immediately.",
    );
    expect(api.to("POST", "/checks/chk-beat/heartbeat-token")).toEqual([]);

    await click(buttonLabelled(container, "Keep the current one"));
    expect(findButton(container, "Issue new URL")).toBeUndefined();

    await click(buttonLabelled(container, "Issue a new URL"));
    await click(buttonLabelled(container, "Issue new URL"));
    const section = await waitFor(
      () => container.querySelector('section[aria-label="Heartbeat URL"] code'),
      "the new URL",
    );

    expect(api.to("POST", "/checks/chk-beat/heartbeat-token")).toHaveLength(1);
    expect(section.textContent).toBe(`${window.location.origin}/api/v1/heartbeat/hb_new`);
    expect(readable(container)).toContain("Copy it now — it is not shown again.");
    expect(buttonLabelled(container, "Copy heartbeat URL")).toBeDefined();
  });

  it("says when it was last called", async () => {
    const { container } = await mountSilent(HEARTBEAT);

    expect(readable(container.querySelector("header") as Element)).toContain(
      `expected every 24 hours · last called ${formatRelativeTime(HEARTBEAT.last_ping_at ?? "")}`,
    );
  });
});

describe("editing while the check changes", () => {
  const V3 = "https://api.example.com/v3/orders";
  const changedTo = (url: string) =>
    json({
      ...DOWN,
      enabled: false,
      displayed_state: "paused",
      updated_at: minutesAgo(0),
      config: {
        type: "api",
        request: {
          ...(DOWN.config.type === "api" ? DOWN.config.request : {}),
          url,
        },
      },
    });

  it("keeps an unsaved edit through a pause, which changes nothing the form holds", async () => {
    const { container } = await mountCheck(
      DOWN,
      { "PATCH /checks/chk-api": changedTo("https://api.example.com/orders") },
      "?tab=edit",
    );
    await typeInto(inputLabelled(container, "URL"), "https://api.example.com/v2/orders");

    await click(buttonLabelled(container, "Pause"));
    await waitFor(() => findButton(container, "Resume"), "the Resume button");

    expect(inputLabelled(container, "URL").value).toBe("https://api.example.com/v2/orders");
    expect(readable(container)).not.toContain("changed elsewhere");
  });

  it("takes a change made elsewhere into a form nobody has edited", async () => {
    const { container } = await mountCheck(
      DOWN,
      { "PATCH /checks/chk-api": changedTo(V3) },
      "?tab=edit",
    );

    await click(buttonLabelled(container, "Pause"));
    await waitFor(() => inputLabelled(container, "URL").value === V3, "the stored URL");

    expect(readable(container)).not.toContain("changed elsewhere");
    expect(readable(container)).toContain("No unsaved changes.");
  });

  it("keeps an edited draft over a change made elsewhere, and says so", async () => {
    const { container } = await mountCheck(
      DOWN,
      { "PATCH /checks/chk-api": changedTo(V3) },
      "?tab=edit",
    );
    await typeInto(inputLabelled(container, "URL"), "https://api.example.com/v2/orders");

    await click(buttonLabelled(container, "Pause"));
    await waitFor(() => findButton(container, "Resume"), "the Resume button");

    expect(inputLabelled(container, "URL").value).toBe("https://api.example.com/v2/orders");
    expect(readable(container)).toContain(
      "This check was changed elsewhere while you were editing.",
    );
  });
});

describe("editing", () => {
  it("saves the whole definition and says when it takes effect", async () => {
    const moved = { ...DOWN, updated_at: minutesAgo(0) };
    const { container, api } = await mountCheck(
      DOWN,
      { "PATCH /checks/chk-api": json(moved) },
      "?tab=edit",
    );
    const url = inputLabelled(container, "URL");
    expect(url.value).toBe("https://api.example.com/orders");

    await typeInto(url, "https://api.example.com/v2/orders");
    await submitForm(container);
    await waitFor(() => container.querySelector('[role="status"]'), "the saved notice");

    expect(api.to("PATCH", "/checks/chk-api")[0]?.body).toMatchObject({
      name: "Orders API",
      config: { type: "api", request: { url: "https://api.example.com/v2/orders" } },
      interval_seconds: 300,
      locations: ["mumbai"],
    });
    expect(readable(container)).toContain("Saved. The next run uses this");
  });
});

describe("the alerts strip (F21, FR24)", () => {
  it("shows the open incident, the rule that watches the check, and the way to add one", async () => {
    stubApi({
      ...syntheticsReads({ checks: [checkDefinition()] }),
      "GET /alerts/coverage/checks/chk-api": json({
        rules: [
          {
            id: "rule-default",
            name: "Any check down",
            muted_until: null,
            enabled: true,
            notifying_nobody: false,
            channels: [{ id: "ch-owners", name: "Organization owners" }],
          },
        ],
        open_incidents: [
          {
            id: "inc-1",
            severity: "critical",
            opened_at: minutesAgo(4),
            rule_name: "Any check down",
          },
        ],
      }),
    });
    const { container } = await mountAt("/synthetics/chk-api");

    const strip = await waitFor(
      () => container.querySelector('section[aria-label="Alerts"]'),
      "the alerts strip",
    );
    expect(readable(strip)).toContain("Open incident — critical");
    expect(readable(strip)).toContain("Watched by Any check down");
    expect(strip.querySelector('a[href="/alerts/rules/new?check=chk-api"]')?.textContent).toBe(
      "Alert me about this",
    );
  });

  it("marks a muted rule, which watches but tells nobody until the mute ends", async () => {
    stubApi({
      ...syntheticsReads({ checks: [checkDefinition()] }),
      "GET /alerts/coverage/checks/chk-api": json({
        rules: [
          {
            id: "rule-default",
            name: "Any check down",
            muted_until: new Date(Date.now() + 3_600_000).toISOString(),
            enabled: true,
            notifying_nobody: false,
            channels: [{ id: "ch-owners", name: "Organization owners" }],
          },
        ],
        open_incidents: [],
      }),
    });
    const { container } = await mountAt("/synthetics/chk-api");

    const strip = await waitFor(
      () => container.querySelector('section[aria-label="Alerts"]'),
      "the alerts strip",
    );
    expect(readable(strip)).toContain("Watched by Any check down (muted)");
  });

  it("says plainly when nothing would tell anyone", async () => {
    stubApi(syntheticsReads({ checks: [checkDefinition()] }));
    const { container } = await mountAt("/synthetics/chk-api");

    const strip = await waitFor(
      () => container.querySelector('section[aria-label="Alerts"]'),
      "the alerts strip",
    );
    expect(readable(strip)).toContain("No rule tells anyone when this breaks.");
  });
});
