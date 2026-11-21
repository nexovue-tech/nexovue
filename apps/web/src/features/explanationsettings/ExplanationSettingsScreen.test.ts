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

import type { RuleCounts } from "./api";
import { ExplanationSettingsScreen } from "./ExplanationSettingsScreen";
import { copyableSummary, nothingYet, RULE_WORDS, STOP_WORDS } from "./explanation-settings";

/**
 * `/settings/explanations` (F3 T2.12; FR54, AC99): counts per rule, a summary holding
 * counts only, and a rule stopped behind a confirmation. Mounted in a router with only
 * the network replaced.
 */

const API_PREFIX = "/api/v1";
const SETTLE_ROUNDS = 60;
const PLANTED_NAME = "planted-service-name-7f3";
const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

function counts(overrides: Partial<RuleCounts> = {}): RuleCounts {
  return {
    rules: (Object.keys(RULE_WORDS) as (keyof typeof RULE_WORDS)[]).map((rule) => ({
      rule,
      named: rule === "signed_deploy" ? 4 : 0,
      useful: rule === "signed_deploy" ? 2 : 0,
      wrong_cause: rule === "signed_deploy" ? 1 : 0,
      not_useful: 0,
      bad_wording: 0,
      stopped: false,
    })),
    without_cause_useful: 1,
    without_cause_not_useful: 0,
    ...overrides,
  };
}

interface Call {
  method: string;
  path: string;
}

function stubApi(role: TestRole, answer: RuleCounts | "fails" = counts()) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      calls.push({ method, path });
      const reply = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), {
          status,
          headers: {
            "content-type": status < 400 ? "application/json" : "application/problem+json",
          },
        });
      if (path === "/auth/me") return reply(identityFor(role));
      if (path === "/explanations/rule-counts") {
        return answer === "fails"
          ? reply({ type: "about:blank", title: "Down", status: 500 }, 500)
          : reply(answer);
      }
      if (path.startsWith("/explanations/stopped-rules/")) {
        return reply({ rule: path.split("/").at(-1), stopped: method === "PUT" });
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
      path: "/settings/explanations",
      component: ExplanationSettingsScreen,
    }),
  ]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ["/settings/explanations"] }),
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

describe("explanation settings (F3 FR54, AC99)", () => {
  it("states each rule's findings and ratings as counts", async () => {
    stubApi("admin");
    const container = await mount();

    const text = await waitFor(
      () => container.textContent?.includes(RULE_WORDS.signed_deploy) && container.textContent,
      "the rules",
    );
    expect(text).toContain(
      "Named a likely cause on 4 findings. Rated useful 2; the cause was wrong 1",
    );
    expect(text).toContain(RULE_WORDS.project_shared_dependency);
  });

  it("stops a rule only after saying its facts stay", async () => {
    const calls = stubApi("admin");
    const container = await mount();
    const stop = await waitFor(() => button(container, STOP_WORDS.stop), "the stop button");

    await act(async () => stop.click());
    expect(container.textContent).toContain("Its facts stay on every explanation");
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
    const confirm = container.querySelector("fieldset[aria-label] button") as HTMLButtonElement;
    await act(async () => confirm.click());
    await settle();

    expect(calls).toContainEqual({
      method: "PUT",
      path: "/explanations/stopped-rules/signed_deploy",
    });
  });

  it("lets a stopped rule name causes again", async () => {
    const stopped = counts();
    stopped.rules[0] = { ...stopped.rules[0], stopped: true };
    const calls = stubApi("admin", stopped);
    const container = await mount();

    expect(
      await waitFor(() => container.textContent?.includes(STOP_WORDS.stopped), "the stopped rule"),
    ).toBe(true);
    await act(async () => button(container, STOP_WORDS.resume)?.click());
    await settle();

    expect(calls).toContainEqual({
      method: "DELETE",
      path: "/explanations/stopped-rules/signed_deploy",
    });
  });

  it("reads nothing for a member, and says who can", async () => {
    const calls = stubApi("member");
    const container = await mount();
    await waitFor(() => container.textContent?.includes("An owner or an admin"), "the refusal");

    expect(calls.some((call) => call.path === "/explanations/rule-counts")).toBe(false);
  });

  it("says it could not load rather than showing zeroes", async () => {
    stubApi("admin", "fails");
    const container = await mount();

    expect(
      await waitFor(
        () => container.textContent?.includes("The rule counts could not be loaded"),
        "the failure",
      ),
    ).toBe(true);
    expect(container.textContent).not.toContain("Named a likely cause on 0");
  });

  it("says no findings yet when nothing was counted", () => {
    const empty = counts({ without_cause_useful: 0 });
    empty.rules = empty.rules.map((rule) => ({ ...rule, named: 0, useful: 0, wrong_cause: 0 }));

    expect(nothingYet(empty)).toBe(true);
    expect(nothingYet(counts())).toBe(false);
  });
});

describe("the summary a customer may send (AC99)", () => {
  it("holds counts and rule names only, never a planted name", () => {
    const planted = counts();
    // A name could only arrive through a field the API never sends; even then it is
    // not in the summary, which is built from counts alone.
    (planted.rules[0] as unknown as Record<string, unknown>).service = PLANTED_NAME;

    const summary = copyableSummary(planted);

    expect(summary).not.toContain(PLANTED_NAME);
    expect(summary.split("\n")[0]).toBe(
      "signed_deploy: named=4 useful=2 wrong_cause=1 not_useful=0 bad_wording=0",
    );
    expect(summary.split("\n").at(-1)).toBe("no_cause: useful=1 not_useful=0");
  });
});
