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
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { identityFor, type TestRole } from "@/shared/identity.test-util";
import { setSelectedProjectId } from "@/shared/selection";

import type { RunbookList, RunbookView, UploadResult } from "./api";
import { RunbookScreen } from "./RunbookScreen";
import { RunbooksScreen } from "./RunbooksScreen";
import {
  ADD,
  capReached,
  DELETE,
  DELETE_CONFIRM,
  EDIT,
  EMPTY,
  FORMER_MEMBER,
  SAVE,
  SAVE_ANYWAY,
  SUPPORT_NO_BODY,
  sentTo,
  TOO_LARGE,
  UPLOAD,
} from "./runbook-words";
import { credentialBatches } from "./UploadForm";

/**
 * `/settings/runbooks` (F4 T5.3; FR32, AC69, AC90, AC92): the list with author and age, its
 * states, who may write, the credential second click, the size refusal, a support view's
 * missing body, delete's sentence, and an upload's per-file errors. Mounted in a router with
 * only the network replaced; the clock pinned.
 */

const API_PREFIX = "/api/v1";
const SETTLE_ROUNDS = 60;
const NOW = Date.parse("2026-11-21T10:00:00Z");
const PROJECT = "0196a000-0000-7000-8000-00000000000a";
const RUNBOOK = "0196a000-0000-7000-8000-0000000000aa";
const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  setSelectedProjectId(PROJECT);
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function runbook(overrides: Partial<RunbookView> = {}): RunbookView {
  return {
    id: RUNBOOK,
    project_id: PROJECT,
    title: "Checkout out of connections",
    service_name: "checkout",
    author: "Priya",
    created_at: "2026-11-09T10:00:00Z",
    updated_at: "2026-11-09T10:00:00Z",
    content: "## Pool\nrestart pgbouncer",
    ...overrides,
  };
}

interface Call {
  method: string;
  path: string;
  body: unknown;
}

interface Stubs {
  role?: TestRole;
  list?: RunbookList | "fails";
  one?: RunbookView;
  save?: (body: Record<string, unknown>) => { status: number; body: unknown };
  upload?: UploadResult;
  chat?: { state: string; provider_label: string | null };
}

function stubApi(stubs: Stubs = {}): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ method, path, body });
      const reply = (answer: unknown, status = 200) =>
        new Response(status === 204 ? null : JSON.stringify(answer), {
          status,
          headers: {
            "content-type": status < 400 ? "application/json" : "application/problem+json",
          },
        });
      if (path === "/auth/me") return reply(identityFor(stubs.role ?? "member"));
      if (path === "/system/info") return reply({ deployment: "self-hosted" });
      if (path === "/projects") {
        return reply({
          projects: [
            { id: PROJECT, slug: "production", name: "production", first_reported_on: null },
          ],
        });
      }
      if (path === "/assistant/availability") {
        return reply(stubs.chat ?? { state: "chat_off", provider_label: null });
      }
      if (path === "/runbooks" && method === "GET") {
        const list = stubs.list ?? { runbooks: [runbook()], limit: 200 };
        return list === "fails"
          ? reply({ type: "about:blank", title: "Down", status: 500 }, 500)
          : reply(list);
      }
      if (path === "/runbooks" && method === "POST") {
        const answer = stubs.save?.(body) ?? { status: 201, body: runbook() };
        return reply(answer.body, answer.status);
      }
      if (path === "/runbooks/upload") return reply(stubs.upload ?? { created: [], refused: [] });
      if (path === `/runbooks/${RUNBOOK}` && method === "GET") return reply(stubs.one ?? runbook());
      if (path === `/runbooks/${RUNBOOK}` && method === "DELETE") return reply(null, 204);
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

async function mount(at: string): Promise<HTMLElement> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: Outlet });
  const one = createRoute({
    getParentRoute: () => rootRoute,
    path: "/settings/runbooks/$runbookId",
    component: () =>
      createElement(RunbookScreen, {
        runbookId: (one.useParams() as { runbookId: string }).runbookId,
      }),
  });
  const routeTree = rootRoute.addChildren([
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/settings/runbooks",
      component: RunbooksScreen,
    }),
    one,
  ]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [at] }),
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

function type(field: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype = Object.getPrototypeOf(field);
  Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(field, value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
}

async function fill(container: HTMLElement, title: string, content: string) {
  const add = await waitFor(() => button(container, ADD), "the add button");
  await act(async () => add.click());
  const [titleField] = [...container.querySelectorAll<HTMLInputElement>('input[name="title"]')];
  const text = container.querySelector("textarea");
  if (!titleField || !text) throw new Error("the form did not open");
  await act(async () => {
    type(titleField, title);
    type(text, content);
  });
}

describe("the runbooks list (FR32)", () => {
  it("shows each runbook's service, author and age, warning past 180 days", async () => {
    stubApi({
      list: {
        runbooks: [
          runbook(),
          runbook({
            id: "0196a000-0000-7000-8000-0000000000ab",
            title: "Old one",
            author: null,
            updated_at: "2026-04-01T10:00:00Z",
          }),
        ],
        limit: 200,
      },
    });
    const container = await mount("/settings/runbooks");
    await waitFor(() => container.textContent?.includes("Old one"), "the list");

    expect(container.textContent).toContain("checkout · Priya · 12 days old");
    expect(container.textContent).toContain(`${FORMER_MEMBER} · `);
    const stale = [...container.querySelectorAll("span")].find(
      (span) => span.textContent === "234 days old",
    );
    expect(stale?.className).toContain("text-warning-text");
  });

  it("says when there are none, and offers writing only to who may", async () => {
    stubApi({ role: "viewer", list: { runbooks: [], limit: 200 } });
    const viewer = await mount("/settings/runbooks");
    await waitFor(() => viewer.textContent?.includes(EMPTY), "the empty sentence");
    expect(button(viewer, ADD)).toBeUndefined();
    expect(button(viewer, UPLOAD)).toBeUndefined();

    stubApi({ role: "member", list: { runbooks: [], limit: 200 } });
    const member = await mount("/settings/runbooks");
    await waitFor(() => button(member, ADD), "the add button");
    expect(button(member, UPLOAD)).toBeDefined();
  });

  it("says the cap is reached instead of offering another", async () => {
    stubApi({ list: { runbooks: [runbook()], limit: 1 } });
    const container = await mount("/settings/runbooks");
    await waitFor(() => container.textContent?.includes(capReached(1)), "the cap sentence");
    expect(button(container, ADD)).toBeUndefined();
  });

  it("says a failed read in its own words", async () => {
    stubApi({ list: "fails" });
    const container = await mount("/settings/runbooks");
    await waitFor(
      () => container.textContent?.includes("The runbooks could not be loaded"),
      "the failure",
    );
  });
});

describe("adding a runbook (AC69, AC90)", () => {
  it("says who the text may go to with chat on, and asks a second click for a credential", async () => {
    const calls = stubApi({
      chat: { state: "ready", provider_label: "OpenAI-compatible" },
      save: (body) =>
        body.confirm_credentials
          ? { status: 201, body: runbook() }
          : {
              status: 422,
              body: {
                type: "about:blank",
                title: "Invalid",
                status: 422,
                detail: "This text looks like it holds a password, token or key.",
                code: "looks_like_credential",
              },
            },
    });
    const container = await mount("/settings/runbooks");
    await fill(container, "Pool", "password: hunter2");
    expect(container.textContent).toContain(sentTo("OpenAI-compatible"));

    await act(async () => button(container, SAVE)?.click());
    const anyway = await waitFor(() => button(container, SAVE_ANYWAY), "the second click");
    expect(container.textContent).toContain("looks like it holds a password");
    await act(async () => anyway.click());
    await settle();

    const saves = calls.filter((call) => call.method === "POST" && call.path === "/runbooks");
    expect(
      saves.map((call) => (call.body as { confirm_credentials: boolean }).confirm_credentials),
    ).toEqual([false, true]);
  });

  it("refuses more than 32 KiB in the API's own sentence and sends nothing", async () => {
    const calls = stubApi();
    const container = await mount("/settings/runbooks");
    await fill(container, "Large", "x".repeat(32 * 1024 + 1));
    await act(async () => button(container, SAVE)?.click());

    expect(container.textContent).toContain(TOO_LARGE);
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });
});

describe("one runbook (AC92)", () => {
  it("shows a support view no body and no way to change it", async () => {
    stubApi({ role: "owner", one: runbook({ content: null }) });
    const container = await mount(`/settings/runbooks/${RUNBOOK}`);
    await waitFor(() => container.textContent?.includes(SUPPORT_NO_BODY), "the sentence");
    expect(button(container, EDIT)).toBeUndefined();
    expect(button(container, DELETE)).toBeUndefined();
  });

  it("says what chat loses before deleting", async () => {
    const calls = stubApi({ role: "member" });
    const container = await mount(`/settings/runbooks/${RUNBOOK}`);
    const remove = await waitFor(() => button(container, DELETE), "the delete button");
    await act(async () => remove.click());
    expect(container.textContent).toContain(DELETE_CONFIRM);
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);

    const confirm = [...container.querySelectorAll("fieldset button")].find(
      (each) => each.textContent === DELETE,
    ) as HTMLButtonElement | undefined;
    await act(async () => confirm?.click());
    await settle();
    expect(
      calls.some((call) => call.method === "DELETE" && call.path === `/runbooks/${RUNBOOK}`),
    ).toBe(true);
  });
});

describe("an upload (AC92)", () => {
  it("lists each file it could not add, in the API's words", async () => {
    stubApi({
      upload: {
        created: [runbook()],
        refused: [
          { name: "big.md", project_id: PROJECT, code: "runbook_too_large", detail: TOO_LARGE },
        ],
      },
    });
    const container = await mount("/settings/runbooks");
    const open = await waitFor(() => button(container, UPLOAD), "the upload button");
    await act(async () => open.click());
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    if (!input) throw new Error("no file input");
    Object.defineProperty(input, "files", {
      value: [new File(["# One\nrestart"], "one.md"), new File(["x"], "big.md")],
    });
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await settle();
    const send = [...container.querySelectorAll("button")].filter(
      (each) => each.textContent === UPLOAD,
    );
    await act(async () => send.at(-1)?.click());

    const refused = await waitFor(
      () => container.querySelector('[aria-label="Files not added"]'),
      "the refusals",
    );
    expect(refused.textContent).toContain(`big.md: ${TOO_LARGE}`);
    expect(container.textContent).toContain("1 runbook added.");
  });

  it("sends again only the files refused for a credential, to the projects that refused them", () => {
    const files = [
      { name: "a.md", content: "a" },
      { name: "b.md", content: "b" },
    ];
    expect(
      credentialBatches(
        [
          { name: "b.md", project_id: "p1", code: "looks_like_credential", detail: "" },
          { name: "a.md", project_id: "p2", code: "looks_like_credential", detail: "" },
        ],
        files,
      ),
    ).toEqual([
      { projectIds: ["p1"], files: [{ name: "b.md", content: "b" }] },
      { projectIds: ["p2"], files: [{ name: "a.md", content: "a" }] },
    ]);
  });
});
