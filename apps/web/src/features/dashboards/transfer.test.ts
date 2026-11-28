import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api-client";

import type { Dashboard } from "./api";
import {
  EXPORT_WARNING,
  exportedDocument,
  exportFileName,
  exportText,
  MAX_FILE_BYTES,
  readDashboardFile,
  refusalWords,
} from "./transfer";

/**
 * Export and import as data (F20, T9.3; FR42, AC40, AC41, AC119, AC139): a file holds the
 * document and nothing naming where it came from, reads back equal, says what it holds,
 * and a file past 64 KiB or that is not JSON is refused before anything is sent.
 */

const ORG = "0190e3f4-0000-7000-8000-00000000000a";
const DASHBOARD = "0190e3f4-0000-7000-8000-00000000000b";

const saved: Dashboard & { org_id: string } = {
  id: DASHBOARD,
  org_id: ORG,
  title: "Checkout on-call",
  description: "What the checkout team watches.",
  definition: {
    schema_version: 1,
    refresh_seconds: 60,
    default_range: null,
    service_picker: true,
    links: [{ label: "Runbook", url: "https://wiki.example/checkout" }],
    panels: [
      {
        id: "p_login",
        kind: "check_trend",
        title: null,
        width: 4,
        height: "medium",
        settings: { check: "Login", signal: "uptime" },
      },
      {
        id: "p_orders",
        kind: "metric_series",
        title: null,
        width: 4,
        height: "medium",
        settings: {
          metric: "orders_processed",
          shape: "sum",
          service: { picker: true },
          series: "18446744073709551615",
          aggregation: "sum",
        },
      },
    ],
  },
  version: 7,
  owner: "Asha",
  updated_by: "Ravi",
  last_viewed_at: "2026-11-12T09:00:00Z",
  created_at: "2026-11-01T09:00:00Z",
  updated_at: "2026-11-11T09:00:00Z",
};

const file = (body: string) => new Blob([body], { type: "application/json" });

describe("export (FR42, AC40)", () => {
  it("holds the document alone: no organization, owner, dashboard id or version", () => {
    const text = exportText(saved);

    expect(Object.keys(JSON.parse(text)).sort()).toEqual(["definition", "description", "title"]);
    for (const named of [ORG, DASHBOARD, "Asha", "Ravi", '"version"']) {
      expect(text).not.toContain(named);
    }
  });

  it("reads back equal to what was saved, a series by its hash and a check by its name", async () => {
    const reading = await readDashboardFile(file(exportText(saved)));

    expect(reading).toEqual({ kind: "read", document: exportedDocument(saved) });
    expect(exportedDocument(saved).definition).toBe(saved.definition);
  });

  it("names the file from the title, and nothing else", () => {
    expect(exportFileName("Checkout on-call")).toBe("Checkout-on-call.json");
    expect(exportFileName("../../etc/passwd")).toBe("..-..-etc-passwd.json");
    expect(exportFileName("‮")).toBe("-.json");
  });

  it("says the file holds links, notes, service names and host names (AC119, FR62)", () => {
    expect(EXPORT_WARNING).toBe("This file includes notes, links, service names and host names.");
  });
});

describe("reading a chosen file (AC139)", () => {
  it("refuses a 65 KiB file in words, before anything is sent", async () => {
    const body = `{"title":"${"x".repeat(MAX_FILE_BYTES)}"}`;

    expect(await readDashboardFile(file(body))).toEqual({
      kind: "refused",
      reasons: ["This file is 65 KiB, and a dashboard file is at most 64 KiB."],
    });
  });

  it("takes a file of exactly 64 KiB to the check", async () => {
    const padding = MAX_FILE_BYTES - '{"title":""}'.length;
    const reading = await readDashboardFile(file(`{"title":"${"x".repeat(padding)}"}`));

    expect(reading.kind).toBe("read");
  });

  it.each([
    ["not JSON", "<html>", "This file is not a dashboard file: it is not JSON."],
    ["a list", "[1, 2]", "This file is not a dashboard file."],
    ["a bare value", '"a dashboard"', "This file is not a dashboard file."],
    ["null", "null", "This file is not a dashboard file."],
  ])("refuses %s", async (_, body, reason) => {
    expect(await readDashboardFile(file(body))).toEqual({ kind: "refused", reasons: [reason] });
  });
});

const refused = (problem: Record<string, unknown>) =>
  new ApiError(
    "refused",
    422,
    { correlationId: "c-1" } as never,
    {
      type: "x",
      title: "Request validation failed",
      status: 422,
      ...problem,
    } as never,
  );

describe("what a refusal says (AC41, AC128)", () => {
  it("names each field in the screen's words, without pydantic's prefix", () => {
    const words = refusalWords(
      refused({
        errors: [
          { field: "body.title", reason: "String should have at least 1 character" },
          { field: "body.definition.links.2.url", reason: "Value error, must be an https address" },
          {
            field: "body.definition.schema_version",
            reason:
              "Value error, this dashboard is in format 2, made by a newer version of nexovue; this install reads format 1. Upgrade nexovue to open it",
          },
          { field: "body.definition.panels.0.kind", reason: "Input tag 'query' found" },
        ],
      }),
      "fallback",
    );

    expect(words).toEqual([
      "Title: String should have at least 1 character",
      "Link 3 address: must be an https address",
      "Format: this dashboard is in format 2, made by a newer version of nexovue; this install reads format 1. Upgrade nexovue to open it",
      "Panel 1: Input tag 'query' found",
    ]);
  });

  it("shows hidden characters in a reason as escapes", () => {
    const [words] = refusalWords(
      refused({ errors: [{ field: "body.title", reason: "holds ‮" }] }),
      "fallback",
    );

    expect(words).toBe("Title: holds [U+202E]");
  });

  it("says a refusal with no fields as its one sentence, and anything else as the fallback", () => {
    expect(
      refusalWords(refused({ detail: "This dashboard would need 42 reads." }), "fallback"),
    ).toEqual(["This dashboard would need 42 reads."]);
    expect(refusalWords(new Error("network"), "Could not check the file.")).toEqual([
      "Could not check the file.",
    ]);
  });
});
