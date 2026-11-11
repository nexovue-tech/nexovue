// @vitest-environment jsdom

import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { identityFor, type TestRole } from "@/shared/identity.test-util";
import { mount, PROJECT_ID, settle, unmountAll } from "@/shared/router-harness.test-util";

import { CHAT_MCP_LINE, SELLING_LINE } from "./ai-words";
import { ChatSection } from "./ChatSection";
import type { ChatPreview, ChatSettings, ReadRow } from "./chat-api";

/**
 * F4 T2.12: the Chat section of `/settings/ai`. The MCP sentence (AC89); counts with no
 * question text, and a warning near chat's ceiling (AC37, AC101); the read record's columns
 * and nothing else (AC72); the preview's samples, masking and labelled sample, and an owner's
 * agreement carrying the fingerprint read (AC2); tested or not tested (AC96).
 */

const NOW = new Date("2026-11-12T09:31:00Z");
const API_PREFIX = "/api/v1";

const SETTINGS: ChatSettings = {
  enabled: true,
  daily_question_limit: 50,
  data_level: "facts",
  mask_emails: true,
  consented_at: null,
  consented_by_name: null,
  consent_current: false,
  consent_fingerprint: null,
  disclosure_version: 1,
  monthly_counts: { answered: 12, rejected_way_out: 2, wrong_wrong_cause: 1 },
  people_this_month: [{ name: "Ada", questions: 9 }],
};

const PREVIEW: ChatPreview = {
  provider_label: "a model on your own server",
  address: "http://10.0.0.5:11434/v1",
  wire_format: "openai_chat",
  model: "gpt-oss:20b",
  project_id: PROJECT_ID,
  project_name: "production",
  kinds: [{ kind: "Reading logs: body", source: "your telemetry", level: "facts_and_text" }],
  samples: [
    {
      kind: "Reading logs: a log line",
      labelled_sample: true,
      sent: '<untrusted-ab label="step s1 result">',
    },
  ],
  masking_rules_off: ["cookies"],
  mask_emails: true,
  sent_for_embedding: 0,
  consent_fingerprint: "fp-read",
  disclosure_version: 1,
};

const READ: ReadRow = {
  at: "2026-11-12T09:00:00Z",
  person: "Ada",
  project: "production",
  tool: "search_logs",
  from_time: "2026-11-12T08:00:00Z",
  to_time: "2026-11-12T09:00:00Z",
  row_count: 14,
  provider_label: "a model on your own server",
};

interface Call {
  method: string;
  path: string;
  body: unknown;
}

function stub(role: TestRole, usedTokens = 0): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null });
      const reply = (body: unknown) =>
        new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
      if (path === "/auth/me") return reply(identityFor(role));
      if (path === "/projects") {
        return reply({
          projects: [{ id: PROJECT_ID, slug: "p", name: "production", first_reported_on: null }],
        });
      }
      if (path === "/assistant/settings" && method === "GET") return reply(SETTINGS);
      if (path === "/assistant/settings/consent")
        return reply({ ...SETTINGS, consent_current: true });
      if (path === "/assistant/reads") return reply([READ]);
      if (path === "/assistant/availability")
        return reply({ tested: false, waiting_for: ["Owen"] });
      if (path === "/ai/preview") return reply(PREVIEW);
      if (path === "/ai/usage") {
        return reply({
          month: "2026-11-01",
          resets_on: "2026-12-01",
          used_tokens: usedTokens,
          reserved_tokens: 0,
          monthly_token_limit: 1_000_000,
          input_tokens: 0,
          output_tokens: 0,
          estimated_cost: null,
          worst_case_cost: null,
        });
      }
      return new Response(JSON.stringify({ title: "no", status: 404 }), { status: 404 });
    }),
  );
  return calls;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  unmountAll();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const section = () => createElement(ChatSection, { now: NOW.getTime() });

describe("the Chat section", () => {
  it("says MCP is untouched, tested or not, and who it waits for", async () => {
    stub("admin");
    const shown = await mount(section);
    expect(shown.textContent).toContain(CHAT_MCP_LINE);
    expect(shown.textContent).toContain("not tested");
    expect(shown.textContent).toContain("Waiting for an owner to agree: Owen.");
  });

  it("counts by outcome, rejection and wrong mark, and by person, with no question text", async () => {
    stub("admin");
    const shown = await mount(section);
    expect(shown.textContent).toContain("Answered: 12");
    expect(shown.textContent).toContain("Rejected: way out: 2");
    expect(shown.textContent).toContain("Marked wrong: wrong cause: 1");
    expect(shown.textContent).toContain("Ada");
    expect(shown.textContent).not.toContain("What is failing");
  });

  it.each([
    [639_999, false],
    [640_000, true],
  ])("at %i tokens used of a million, warns: %s", async (used, warns) => {
    stub("admin", used);
    const shown = await mount(section);
    expect(shown.textContent?.includes("passed 80% of chat's share")).toBe(warns);
  });

  it("lists what was sent: person, time, project, tool, range, rows and provider", async () => {
    stub("admin");
    const shown = await mount(section);
    const tables = [...shown.querySelectorAll("table")];
    const record = tables.find((table) => table.textContent?.includes("Provider"));
    const row = record?.querySelector("tbody tr")?.textContent ?? "";
    for (const part of ["Ada", "production", "search_logs", "14", "a model on your own server"]) {
      expect(row).toContain(part);
    }
  });

  it("previews the samples and masking, and an owner agrees to the fingerprint read", async () => {
    const calls = stub("owner");
    const shown = await mount(section);
    const button = (words: string) =>
      [...shown.querySelectorAll("button")].find((each) => each.textContent?.startsWith(words));
    await act(async () => button("Show what would be sent")?.click());
    await new Promise((resolve) => setTimeout(resolve, 0));
    await act(async () => {});
    expect(shown.textContent).toContain("a built-in sample: this project has none yet");
    expect(shown.textContent).toContain(
      "These masking rules off".toLowerCase().replace("these", "these"),
    );
    expect(shown.textContent).toContain("cookies");
    expect(shown.textContent).toContain(SELLING_LINE);
    await act(async () => button("Agree to send this")?.click());
    await act(async () => {});
    const agreed = calls.find((call) => call.path === "/assistant/settings/consent");
    expect(agreed?.body).toEqual({
      fingerprint: "fp-read",
      disclosure_version: 1,
      data_level: "facts",
    });
  });

  it("offers no agreement to an admin who is not an owner", async () => {
    stub("admin");
    const shown = await mount(section);
    const show = [...shown.querySelectorAll("button")].find((each) =>
      each.textContent?.startsWith("Show what would be sent"),
    );
    await act(async () => show?.click());
    await settle();
    // The preview has loaded, so its absence is the role's doing, not the network's.
    expect(shown.textContent).toContain("a built-in sample");
    expect(shown.textContent).not.toContain("Agree to send this");
  });
});
