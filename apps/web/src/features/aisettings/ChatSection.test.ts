// @vitest-environment jsdom

import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { identityFor, type TestRole } from "@/shared/identity.test-util";
import { mount, PROJECT_ID, settle, unmountAll } from "@/shared/router-harness.test-util";

import { CHAT_MCP_LINE, KEYWORD_ONLY_WORDS, MEANING_SEARCH_ON, SELLING_LINE } from "./ai-words";
import { ChatSection } from "./ChatSection";
import type { ChatPreview, ChatSettings, EmbeddingState, ReadRow } from "./chat-api";

/**
 * F4 T2.12: the Chat section of `/settings/ai`. The MCP sentence (AC89); counts with no
 * question text, and a warning near chat's ceiling (AC37, AC101); the read record's columns
 * and nothing else (AC72); the preview's samples, masking and labelled sample, and an owner's
 * agreement carrying the fingerprint read (AC2); tested or not tested (AC96).
 */

const NOW = new Date("2026-11-12T09:31:00Z");
const API_PREFIX = "/api/v1";

const EMBEDDING: EmbeddingState = {
  model: null,
  source: null,
  own_base_url: null,
  own_host: null,
  own_wire_format: null,
  own_has_key: false,
  own_consent_fingerprint: null,
  own_consent_current: false,
  check: "not_tested",
  check_reason: null,
  checked_at: null,
  vector_available: true,
  keyword_only: "no_model",
  chunks_embedded: 0,
  chunks_total: 0,
};

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
  embedding: EMBEDDING,
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

function stub(role: TestRole, usedTokens = 0, settings: ChatSettings = SETTINGS): Call[] {
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
      if (path === "/assistant/settings" && method === "GET") return reply(settings);
      if (path === "/assistant/settings/embedding") return reply(settings);
      if (path === "/assistant/settings/embedding/consent") return reply(settings);
      if (path === "/assistant/settings/embedding/check") return reply(null);
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

  it.each([
    "no_extension",
    "no_model",
    "cannot_embed",
    "no_consent",
    "not_tested",
    "chat_not_ready",
  ] as const)("says search is keyword only, and why: %s", async (reason) => {
    stub("admin", 0, { ...SETTINGS, embedding: { ...EMBEDDING, keyword_only: reason } });
    const shown = await mount(section);
    expect(shown.textContent).toContain(KEYWORD_ONLY_WORDS[reason]);
    expect(shown.textContent).not.toContain(MEANING_SEARCH_ON);
  });

  it("says when search finds by meaning, with the model, its check and the progress", async () => {
    stub("admin", 0, {
      ...SETTINGS,
      embedding: {
        ...EMBEDDING,
        model: "nomic-embed-text",
        source: "chat",
        check: "passed",
        checked_at: "2026-11-12T09:00:00Z",
        keyword_only: null,
        chunks_embedded: 40,
        chunks_total: 52,
      },
    });
    const shown = await mount(section);
    expect(shown.textContent).toContain(MEANING_SEARCH_ON);
    expect(shown.textContent).toContain(
      "Embedding model nomic-embed-text, through the chat connection",
    );
    expect(shown.textContent).toContain("Its check passed on");
    expect(shown.textContent).toContain("40 of 52 runbook pieces and fact lines");
  });

  it("saves a model and a second connection, and an owner agrees to it as read", async () => {
    const calls = stub("owner", 0, {
      ...SETTINGS,
      embedding: {
        ...EMBEDDING,
        model: "model-e5",
        source: "own",
        own_base_url: "https://embed.example/v1",
        own_host: "https://embed.example",
        own_wire_format: "openai_chat",
        own_has_key: true,
        own_consent_fingerprint: "fp-own",
        keyword_only: "no_consent",
      },
    });
    const shown = await mount(section);
    const button = (words: string) =>
      [...shown.querySelectorAll("button")].find((each) => each.textContent?.startsWith(words));
    await act(async () => button("Save search settings")?.click());
    await act(async () => {});
    const saved = calls.find((call) => call.path === "/assistant/settings/embedding");
    // A saved key is kept by leaving it out; it is never sent back to the screen.
    expect(saved?.body).toEqual({
      model: "model-e5",
      own: { wire_format: "openai_chat", base_url: "https://embed.example/v1" },
    });
    await act(async () => button("Agree to send text there")?.click());
    await act(async () => {});
    const agreed = calls.find((call) => call.path === "/assistant/settings/embedding/consent");
    expect(agreed?.body).toEqual({ fingerprint: "fp-own" });
  });

  it("offers an admin who is not an owner no agreement to the second connection", async () => {
    stub("admin", 0, {
      ...SETTINGS,
      embedding: {
        ...EMBEDDING,
        model: "model-e5",
        source: "own",
        own_host: "https://embed.example",
        own_consent_fingerprint: "fp-own",
        keyword_only: "no_consent",
      },
    });
    const shown = await mount(section);
    expect(shown.textContent).toContain("only once an owner agrees");
    expect(shown.textContent).not.toContain("Agree to send text there");
  });

  it("starts a check, which runs as a job on the AI container", async () => {
    const calls = stub("admin", 0, {
      ...SETTINGS,
      embedding: { ...EMBEDDING, model: "model-e5", source: "chat", keyword_only: "not_tested" },
    });
    const shown = await mount(section);
    const check = [...shown.querySelectorAll("button")].find((each) =>
      each.textContent?.startsWith("Check this embedding model"),
    );
    await act(async () => check?.click());
    await act(async () => {});
    expect(
      calls.some(
        (call) => call.method === "POST" && call.path === "/assistant/settings/embedding/check",
      ),
    ).toBe(true);
  });
});
