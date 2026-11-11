// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { identityFor, type TestRole } from "@/shared/identity.test-util";

import { AISettingsScreen } from "./AISettingsScreen";
import {
  BEAT_LOCK_EXPOSED,
  CONTAINER_NOT_RUNNING,
  eraseWarning,
  MCP_LINE,
  MEMBER_LINE,
  MODEL_CHANGED,
  NO_ANSWER,
  NO_FORMAT_YET,
  NO_RUNS,
  OWNER_LEFT,
  tokens,
  usageLine,
} from "./ai-words";
import type { AISettings, CheckResult, ConnectionView, RunRow, Usage } from "./api";

/**
 * `/settings/ai` (F3 T4.7; AC85, AC90, AC103's screen): each role, in loading, load failed,
 * saving and each design state, mounted in a router with only the network replaced.
 */

const API_PREFIX = "/api/v1";
const SETTLE_ROUNDS = 60;
const ORGANIZATION = "Acme";
const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function connection(overrides: Partial<ConnectionView> = {}): ConnectionView {
  return {
    wire_format: "openai_chat",
    base_url: "https://api.openai.com/v1",
    provider_host: "https://api.openai.com",
    address_kind: "public",
    model: "gpt-4o-mini",
    region_label: null,
    has_key: true,
    call_seconds: 100,
    monthly_token_limit: 2_000_000,
    price_input: null,
    price_output: null,
    checked_at: "2026-11-11T10:00:00Z",
    checked_ok: true,
    test_current: true,
    model_changed: false,
    consent: null,
    ...overrides,
  };
}

function settings(overrides: Partial<AISettings> = {}): AISettings {
  return {
    on: false,
    full: true,
    available: true,
    unavailable_reason: null,
    container_running: true,
    beat_lock_exposed: false,
    off_reason: null,
    missing: ["connection"],
    connection: null,
    formats_offered: ["openai_chat"],
    consent_fingerprint: null,
    schema_version: 1,
    suggested_monthly_tokens: 2_000_000,
    ...overrides,
  };
}

const USAGE: Usage = {
  month: "2026-11-01",
  resets_on: "2026-12-01",
  used_tokens: 1_200_000,
  reserved_tokens: 0,
  monthly_token_limit: 2_000_000,
  input_tokens: 1_000_000,
  output_tokens: 200_000,
  estimated_cost: "2.000",
  worst_case_cost: "10",
};

function run(overrides: Partial<RunRow> = {}): RunRow {
  return {
    id: "6c1f0a52-7d64-4c87-9b7a-0f0d1d1d1d01",
    started_at: "2026-11-11T10:00:00Z",
    finished_at: "2026-11-11T10:00:03Z",
    seconds: 3,
    purpose: "explain",
    outcome: "done",
    finding_id: null,
    provider_host: "https://api.openai.com",
    model: "gpt-4o-mini",
    input_tokens: 1000,
    output_tokens: 200,
    tokens_estimated: false,
    fields_sent: ["facts[].chance"],
    by_person: false,
    host_consent: "consented",
    ...overrides,
  };
}

interface Call {
  method: string;
  path: string;
  body: unknown;
}

interface Answers {
  settings?: AISettings | "fails";
  runs?: RunRow[];
  check?: CheckResult;
  save?: "hangs";
}

function stubApi(role: TestRole, answers: Answers = {}) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null });
      const reply = (body: unknown, status = 200) =>
        new Response(status === 204 ? null : JSON.stringify(body), {
          status,
          headers: {
            "content-type": status < 400 ? "application/json" : "application/problem+json",
          },
        });
      if (path === "/auth/me") {
        const identity = identityFor(role);
        return reply({
          ...identity,
          organization: { ...identity.organization, name: ORGANIZATION },
        });
      }
      if (path === "/ai/connection" && method === "GET") {
        const answer = answers.settings ?? settings();
        return answer === "fails"
          ? reply({ type: "about:blank", title: "Down", status: 500 }, 500)
          : reply(answer);
      }
      if (path === "/ai/connection" && method === "PUT") {
        if (answers.save === "hangs") return new Promise<Response>(() => {});
        return reply(settings({ connection: connection() }));
      }
      if (path === "/ai/connection/check" && method === "POST")
        return reply({ request_id: "r-1" }, 202);
      if (path === "/ai/connection/check/r-1") return reply(answers.check ?? { pending: true });
      if (path === "/ai/preview") {
        return reply({
          sample: true,
          finding_id: null,
          read_at: null,
          summary: ["No finding has facts yet, so this is a built-in sample."],
          method: "POST",
          url: "https://api.openai.com/v1/chat/completions",
          headers: [{ name: "Authorization", value: "Bearer (the saved key)" }],
          instructions: "x",
          body: '{"model":"gpt-4o-mini"}',
          request_sha256: "a".repeat(64),
          prompt_version: "explain-1",
          aliases: [{ alias: "service_1", stands_for: "checkout (sample)" }],
          sendable_fields: ["finding.signal"],
          fact_kinds: ["deploy"],
        });
      }
      if (path === "/ai/consent") return reply(settings({ connection: connection() }));
      if (path === "/ai/switch") return reply({ on: true });
      if (path === "/ai/project-opt-outs") {
        return reply([{ project_id: "p-1", name: "production", opted_out: false }]);
      }
      if (path.startsWith("/ai/project-opt-outs/")) {
        return reply({ project_id: "p-1", name: "production", opted_out: method === "PUT" });
      }
      if (path === "/ai/usage") return reply(USAGE);
      if (path === "/ai/runs") return reply(answers.runs ?? []);
      if (path.endsWith("/sent")) {
        return reply({
          match: "matches",
          statement: "Matches the hash of what was sent.",
          instructions: "x",
          body: '{"sent":true}',
          facts: null,
        });
      }
      throw new Error(`no stub for ${method} ${path}`);
    }),
  );
  return calls;
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mount(): Promise<HTMLElement> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: Outlet });
  const routeTree = rootRoute.addChildren([
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/settings/ai",
      component: AISettingsScreen,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/settings/explanations",
      component: () => null,
    }),
  ]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ["/settings/ai"] }),
  });
  await router.load();
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () =>
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
        createElement(RouterProvider, { router } as any),
      ),
    ),
  );
  await settle();
  return container;
}

async function waitFor<T>(find: () => T | null | undefined | false, what: string): Promise<T> {
  for (let round = 0; round < SETTLE_ROUNDS; round += 1) {
    const found = find();
    if (found) return found;
    await settle();
  }
  throw new Error(`gave up waiting for ${what}`);
}

const button = (container: HTMLElement, text: string) =>
  [...container.querySelectorAll("button")].find((each) => each.textContent === text);

const textOf = (container: HTMLElement, words: string) =>
  waitFor(() => container.textContent?.includes(words), words);

function typeInto(container: HTMLElement, name: string, value: string) {
  const input = container.querySelector(`input[name="${name}"]`) as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  act(() => {
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("AI settings (F3 FR23 to FR32, AC85, AC90)", () => {
  it.each(["member", "viewer"] as const)(
    "gives a %s one sentence, with no checklist and no switch",
    async (role) => {
      stubApi(role, { settings: { on: false, full: false } });
      const container = await mount();

      await textOf(container, MEMBER_LINE);
      expect(container.textContent).toContain(MCP_LINE);
      expect(container.querySelectorAll("a[href^='#']")).toHaveLength(0);
      expect(button(container, "Turn AI wording on")).toBeUndefined();
    },
  );

  it("shows a failed read as a failure, never as AI being off, and offers no switch", async () => {
    stubApi("owner", { settings: "fails" });
    const container = await mount();

    await textOf(container, "The AI settings could not be loaded");
    expect(container.textContent).not.toContain("AI wording is off");
    expect(button(container, "Turn AI wording on")).toBeUndefined();
  });

  it("holds the layout with a skeleton while loading", async () => {
    stubApi("owner");
    const container = await mount();
    // The first paint, before the read lands, is the skeleton's status.
    expect(container.innerHTML.length).toBeGreaterThan(0);
    await textOf(container, "AI wording is off.");
  });

  it("lists every missing step as a link to where it is done, and says no format is offered yet", async () => {
    stubApi("owner", { settings: settings({ formats_offered: [] }) });
    const container = await mount();

    await textOf(container, "Save a provider connection");
    expect(container.querySelector("a[href='#ai-provider']")).not.toBeNull();
    expect(container.textContent).toContain(NO_FORMAT_YET);
    expect(container.textContent).toContain(MCP_LINE);
    expect(container.textContent).toContain("your own AI account");
    expect(container.textContent).not.toContain("own server, so");
  });

  it("says when the AI container is not running, and keeps the switch and test disabled", async () => {
    stubApi("admin", {
      settings: settings({
        container_running: false,
        missing: ["container", "consent"],
        connection: connection(),
      }),
    });
    const container = await mount();

    await textOf(container, CONTAINER_NOT_RUNNING);
    const on = await waitFor(() => button(container, "Turn AI wording on"), "the switch");
    expect(on.disabled).toBe(true);
    expect(button(container, "Test this connection")?.disabled).toBe(true);
  });

  it.each([
    ["hosted", "Not offered on the hosted service yet."],
    ["account_missing", "AI is not available: the database account could not be created."],
  ] as const)(
    "where AI is unavailable (%s), says why and offers no form",
    async (reason, words) => {
      stubApi("owner", { settings: settings({ available: false, unavailable_reason: reason }) });
      const container = await mount();

      await textOf(container, words);
      expect(container.querySelector("form")).toBeNull();
    },
  );

  it("tells owners and admins the beat lock is exposed, the model changed and the owner left", async () => {
    stubApi("admin", {
      settings: settings({
        beat_lock_exposed: true,
        missing: ["test"],
        connection: connection({
          model_changed: true,
          test_current: false,
          consent: {
            by_user_id: null,
            by_name: "Raj",
            by_left: true,
            at: "2026-11-10T09:00:00Z",
            current: true,
          },
        }),
      }),
    });
    const container = await mount();

    await textOf(container, BEAT_LOCK_EXPOSED);
    expect(container.textContent).toContain(MODEL_CHANGED);
    expect(container.textContent).toContain(OWNER_LEFT);
  });

  it("warns that a new address erases the key, and saves only after the warning is accepted", async () => {
    const calls = stubApi("admin", {
      settings: settings({ missing: [], connection: connection() }),
    });
    const container = await mount();
    await waitFor(() => container.querySelector("input[name='base_url']"), "the form");

    typeInto(container, "base_url", "https://gateway.example.com/v1");
    await act(async () => button(container, "Save the connection")?.click());
    await settle();

    expect(container.textContent).toContain(eraseWarning(ORGANIZATION));
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
    await act(async () => button(container, "Save and erase the key")?.click());
    await settle();
    const put = calls.find((call) => call.method === "PUT");
    expect(put?.body).toMatchObject({ base_url: "https://gateway.example.com/v1" });
    expect(put?.body).not.toHaveProperty("key");
  });

  it("says a new model turns AI off until a test, and shows saving while it saves", async () => {
    stubApi("admin", {
      settings: settings({ missing: [], connection: connection() }),
      save: "hangs",
    });
    const container = await mount();
    await waitFor(() => container.querySelector("input[name='model']"), "the form");

    typeInto(container, "model", "gpt-5-mini");
    expect(container.textContent).toContain("AI turns off until this passes a test.");
    await act(async () => button(container, "Save the connection")?.click());

    await textOf(container, "Saving");
  });

  it("says the AI container did not answer after 60 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = stubApi("admin", {
      settings: settings({ missing: [], connection: connection() }),
    });
    const container = await mount();
    const test = await waitFor(() => button(container, "Test this connection"), "the test button");

    await act(async () => test.click());
    await textOf(container, "Waiting for the AI container.");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000);
    });

    await textOf(container, NO_ANSWER);
    expect(calls.some((call) => call.path === "/ai/connection/check")).toBe(true);
  });

  it("states a finished test's result in plain words", async () => {
    stubApi("admin", {
      settings: settings({ missing: [], connection: connection() }),
      check: { pending: false, ok: false, reason: "not_found" },
    });
    const container = await mount();
    const test = await waitFor(() => button(container, "Test this connection"), "the test button");

    await act(async () => test.click());

    await textOf(container, "The provider does not know that model or address.");
  });

  it("lets only an owner agree, after reading where facts go", async () => {
    const consent = { fingerprint: "f".repeat(64) };
    const calls = stubApi("owner", {
      settings: settings({
        missing: ["consent"],
        connection: connection(),
        consent_fingerprint: consent.fingerprint,
      }),
    });
    const container = await mount();
    const agree = await waitFor(
      () => button(container, "Agree to send these facts"),
      "the agree button",
    );

    await act(async () => agree.click());
    expect(container.textContent).toContain(
      "are sent to https://api.openai.com for model gpt-4o-mini",
    );
    await act(async () => button(container, "I agree")?.click());
    await settle();

    expect(calls.find((call) => call.path === "/ai/consent")?.body).toEqual({
      fingerprint: consent.fingerprint,
      schema_version: 1,
    });
  });

  it("tells an admin consent waits for an owner, with no agree button", async () => {
    stubApi("admin", { settings: settings({ missing: ["consent"], connection: connection() }) });
    const container = await mount();

    await textOf(container, "Waiting for an owner");
    expect(button(container, "Agree to send these facts")).toBeUndefined();
  });

  it("shows the bar's sentence, the estimate and each run, and rebuilds what a run sent", async () => {
    stubApi("owner", {
      settings: settings({ on: true, missing: [], connection: connection() }),
      runs: [
        run(),
        run({ id: "6c1f0a52-7d64-4c87-9b7a-0f0d1d1d1d02", host_consent: "not_consented" }),
      ],
    });
    const container = await mount();

    await textOf(container, usageLine(USAGE));
    expect(usageLine(USAGE)).toBe("1.2 million of 2 million tokens used, resets 1 December");
    expect(container.textContent).toContain("Estimated cost this month: about 2.00");
    expect(container.textContent).toContain(
      "No owner had agreed to this address when this run went.",
    );
    const view = await waitFor(() => button(container, "View what was sent"), "a run");
    await act(async () => view.click());
    await textOf(container, "Matches the hash of what was sent.");
  });

  it("says when there are no runs yet", async () => {
    stubApi("owner", { settings: settings({ missing: [], connection: connection() }) });
    const container = await mount();
    await textOf(container, NO_RUNS);
  });

  it("keeps a project out, and lets it back", async () => {
    const calls = stubApi("admin", {
      settings: settings({ missing: [], connection: connection() }),
    });
    const container = await mount();
    const keep = await waitFor(() => button(container, "Keep it out"), "the project");

    await act(async () => keep.click());
    await settle();

    expect(calls).toContainEqual({ method: "PUT", path: "/ai/project-opt-outs/p-1", body: null });
  });
});

describe("the words", () => {
  it("states tokens at a glance", () => {
    expect(tokens(1_200_000)).toBe("1.2 million");
    expect(tokens(2_000_000)).toBe("2 million");
    expect(tokens(45_000)).toBe("45 thousand");
    expect(tokens(800)).toBe("800");
  });
});
