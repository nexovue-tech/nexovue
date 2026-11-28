import { describe, expect, it } from "vitest";

import type { DashboardContext, PanelResult, StoredPanel, TableRow } from "./api";
import { comparisonOf, formatMeasure, NOT_ENOUGH_HISTORY } from "./number-reading";
import { openFindingsWords } from "./OpenFindingsPanel";
import { panelTitle, stateWords } from "./panel-words";
import { tableColumns } from "./TablePanel";

/**
 * Step 5's numbers, tables, open findings and words (F20, T5.6a), as data: a number in its
 * own unit and its comparison only over a covered window (AC73); a table's every
 * telemetry value escaped and every latency an estimate (AC35's render half, AC113); open
 * findings a dash and detection's reason while it is not judging, never 0 (AC97); each
 * kind's empty words (AC99) and the words that follow the reader's role (AC105). Times are
 * fixed: `now` is passed, never read.
 */

const NOW = Date.parse("2026-11-09T12:00:00.000Z");
const WINDOW_START = "2026-11-09T11:00:00.000Z";

const panel = (kind: string, settings: Record<string, unknown> = {}): StoredPanel => ({
  id: `p_${kind}`,
  kind,
  title: null,
  width: 4,
  height: "medium",
  settings,
});

const words = (result: PanelResult, shown: StoredPanel, changes = {}) =>
  stateWords(result, shown, {
    canEdit: true,
    owner: "Asha",
    projectName: "staging",
    overLimit: 0,
    now: NOW,
    windowStart: WINDOW_START,
    ...changes,
  });

const empty = (lastSeen: string | null = null): PanelResult => ({
  state: "empty",
  kind: "x",
  read_at: "2026-11-09T12:00:00.000Z",
  last_seen_at: lastSeen,
});

describe("a number panel", () => {
  it("is written in its measure's own unit", () => {
    expect(formatMeasure("p95_ms", 88)).toBe("88ms");
    expect(formatMeasure("error_rate", 0.002)).toBe("0.2%");
    expect(formatMeasure("service_count", 0)).toBe("0");
  });

  it("compares only when asked, and only over a covered window (AC73)", () => {
    expect(comparisonOf({ measure: "p95_ms", value: 90 }, false).kind).toBe("none");
    expect(
      comparisonOf({ measure: "p95_ms", value: 90, previous: null, previous_coverage: 0.5 }, true)
        .kind,
    ).toBe("not_enough_history");
    const compared = comparisonOf(
      { measure: "p95_ms", value: 150, previous: 100, previous_coverage: 1 },
      true,
    );
    expect(compared.kind).toBe("compared");
    expect(compared.delta?.display).toBe("+50%");
    expect(compared.worse).toBe("up");
    expect(NOT_ENOUGH_HISTORY).toBe("Not enough history to compare");
  });
});

describe("a table panel", () => {
  const hostile = "<img src=x onerror=alert(1)>‮";

  it.each([
    ["services_table", { service_name: hostile, requests_per_minute: 1, error_rate: 0, p95_ms: 9 }],
    ["endpoints_table", { service_name: "s", endpoint: hostile, p95_ms: 9 }],
    ["dependencies_table", { caller: hostile, callee: "b", calls_per_minute: 1 }],
    ["error_groups_table", { exception_type: hostile, message_template: "m", occurrences: 3 }],
    ["database_statements_table", { query_hash: hostile, server: { address: "db", port: 5432 } }],
    ["web_vitals_table", { name: hostile, p75: 1200, readings: 9 }],
  ] as [string, TableRow][])(
    "shows a %s's hostile value as literal text with its escapes (AC35, FR61)",
    (kind, row) => {
      const cells = tableColumns(kind, "UTC").map((column) => String(column.cell(row)));
      expect(cells.join(" ")).toContain("<img src=x onerror=alert(1)>[U+202E]");
    },
  );

  it("marks every latency an estimate (AC113)", () => {
    for (const kind of ["services_table", "endpoints_table", "dependencies_table"]) {
      expect(tableColumns(kind, "UTC").map((column) => column.header)).toContain("p95 (est.)");
    }
  });
});

describe("open findings", () => {
  const context = (detection: Partial<DashboardContext["detection"]>): DashboardContext => ({
    project: { id: "p", name: "staging", timezone: "UTC", retention: null },
    window_start: WINDOW_START,
    window_end: "2026-11-09T12:00:00.000Z",
    detection: {
      state: "judging",
      services_total: 2,
      services_learning: 0,
      services_judged: 2,
      services_delayed: 0,
      services_muted: 0,
      earliest_judging_date: null,
      not_running_since: null,
      delayed_since: null,
      open_findings: 0,
      services_too_many: 0,
      judged_service_limit: 50,
      ...detection,
    },
    ever_reported: true,
    last_seen_at: null,
    findings: [],
    findings_truncated: false,
    open_findings: { total: 0, by_service: { checkout: 2 } },
    markers: [],
    markers_truncated: false,
    markers_cap: 500,
    coverage: {},
    incidents: [],
    incidents_truncated: false,
  });

  it("counts a real 0 while detection judges every service", () => {
    expect(openFindingsWords(context({}), undefined)).toEqual({ value: "0" });
    expect(openFindingsWords(context({}), "checkout")).toEqual({ value: "2" });
  });

  it.each([
    [{ state: "not_running" as const }, "Detection is not running"],
    [
      { state: "learning" as const, earliest_judging_date: "2026-11-14" },
      "Learning your services' normal. Judging starts on 14 Nov",
    ],
    [{ state: "data_delayed" as const }, "nexovue is behind on reading your data"],
  ])(
    "shows a dash and detection's reason while it is not judging, never 0 (AC97)",
    (detection, reason) => {
      const said = openFindingsWords(context(detection), undefined);
      expect(said.value).toBe("—");
      expect(said.reason).toContain(reason);
    },
  );
});

describe("Step 5's words", () => {
  it("says each kind's empty words (design, Words by kind)", () => {
    expect(words(empty(), panel("check_trend", { check: "Login" }))).toBe(
      "This check has not run in this window.",
    );
    expect(words(empty(), panel("top_nodes_trend"))).toBe(
      "No machine has reported this metric in this window.",
    );
    expect(words(empty(), panel("statement_trend"))).toBe("No statement reported in this window.");
    expect(words(empty(), panel("web_vitals_table"))).toBe("No browser readings in this window.");
    expect(words(empty(), panel("services_table"))).toBe("No services reported in this window.");
    expect(words(empty(), panel("project_number"))).toBe("Nothing reported in this window.");
  });

  it("tells a failing-only dependencies table with nothing from a quiet one (AC99)", () => {
    expect(words(empty(), panel("dependencies_table", { failing_only: true }))).toBe(
      "No dependency failed in this window.",
    );
    expect(words(empty(), panel("dependencies_table", { failing_only: false }))).toBe(
      "No dependencies seen in this window.",
    );
  });

  it("says no errors only for a service seen in the window (AC99)", () => {
    const errors = panel("error_groups_table", { service: "checkout" });
    expect(words(empty("2026-11-09T11:30:00.000Z"), errors)).toBe("No errors in this window.");
    expect(words(empty("2026-11-09T09:00:00.000Z"), errors)).toBe(
      "checkout sent nothing in this window. Last seen 3 hr. ago.",
    );
    expect(words(empty(), panel("error_groups_table", { service: null }))).toBe(
      "No errors in this window.",
    );
  });

  it("tells an editor who may rename checks to, and anyone else why it cannot read (AC105)", () => {
    const twin = panel("check_trend", { check: "Login" });
    const ambiguous: PanelResult = { state: "ambiguous", kind: "check_trend" };

    expect(words(ambiguous, twin, { canConfigureChecks: true })).toBe(
      "Two checks in staging are named Login. Rename one.",
    );
    expect(words(ambiguous, twin, { canConfigureChecks: false })).toBe(
      "Two checks in staging are named Login, so this panel cannot tell which to read.",
    );
  });

  it("names a missing check, a too-wide window and the operator's pause", () => {
    const login = panel("check_trend", { check: "Login" });
    expect(words({ state: "not_in_project", kind: "check_trend" }, login)).toBe(
      "No check named Login in staging.",
    );
    expect(words({ state: "window_too_wide", kind: "check_trend", limit_days: 30 }, login)).toBe(
      "This chart can show at most 30 days.",
    );
    expect(
      words({ state: "window_too_wide", kind: "x", limit_days: 7 }, panel("error_groups_table")),
    ).toBe("This table can show at most 7 days.");
    expect(words({ state: "paused_by_operator", kind: "x" }, login)).toBe(
      "Dashboards paused by your administrator.",
    );
  });

  it("titles each kind for what it shows, its service first (FR35)", () => {
    expect(
      panelTitle(
        panel("endpoint_trend", {
          service: "checkout",
          endpoint: "GET /orders",
          signal: "latency",
        }),
      ),
    ).toEqual({ service: "checkout", text: "GET /orders · Latency" });
    expect(
      panelTitle(panel("service_number", { service: { picker: true }, measure: "p95_ms" })),
    ).toEqual({
      service: "Picked service",
      text: "p95 (est.)",
    });
    expect(panelTitle(panel("check_trend", { check: "Login", signal: "duration" })).text).toBe(
      "Login · Duration",
    );
    expect(panelTitle(panel("statement_trend", { query_hash: "-61", label: "" })).text).toBe(
      "Statement -61",
    );
    expect(panelTitle(panel("open_findings", { service: null }))).toEqual({
      text: "Open findings",
    });
    expect(panelTitle(panel("dependencies_table", { failing_only: true })).text).toBe(
      "Failing dependencies",
    );
  });
});
