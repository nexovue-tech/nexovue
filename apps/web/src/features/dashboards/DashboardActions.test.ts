// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { formatRelativeTime } from "@/shared/format/time";
import type { TestRole } from "@/shared/identity.test-util";
import { identityFor } from "@/shared/identity.test-util";
import type { Dashboard } from "./api";
import { DashboardActions } from "./DashboardActions";
import { blankDocument } from "./draft";

/**
 * A dashboard's actions (F20, T3.6): delete through the shared inline `ConfirmPanel`,
 * saying what goes in real numbers and that it can be restored for 30 days (AC136), with
 * export offered first. A component of its own, so the read view can mount it too (TF.1).
 */

const API_PREFIX = "/api/v1";
const DASHBOARD_ID = "0190e3f4-0000-7000-8000-000000000001";
const HOUR_MS = 3_600_000;
const SETTLE_ROUNDS = 50;
// Pinned: "last opened 2 hours ago" counts from this, never from the day the test runs.
const NOW = Date.parse("2026-11-12T10:00:00Z");

function dashboard(lastViewedAt: string | null): Dashboard {
  const blank = blankDocument();
  return {
    id: DASHBOARD_ID,
    title: "Checkout on-call",
    description: "",
    definition: {
      ...blank.definition,
      panels: Array.from({ length: 12 }, (_, index) => ({
        id: `p_${index + 1}`,
        kind: "project_trend",
        title: null,
        width: 4,
        height: "medium" as const,
        settings: { signal: "rate" },
      })),
    },
    version: 3,
    owner: "Asha",
    updated_by: "Asha",
    last_viewed_at: lastViewedAt,
    created_at: new Date(NOW).toISOString(),
    updated_at: new Date(NOW).toISOString(),
  };
}

const mounted: Root[] = [];

type Handler = (request: { method: string; path: string; body: unknown }) => Response | undefined;

function summaryTitled(title: string) {
  return {
    id: title,
    title,
    description: "",
    owner: null,
    panel_count: 1,
    uses_picker: false,
    panels_not_reading: 0,
    last_viewed_at: null,
    created_at: new Date(NOW).toISOString(),
    updated_at: new Date(NOW).toISOString(),
    deleted_at: null,
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

async function mount(
  subject: Dashboard,
  onDeleted = vi.fn(),
  options: {
    role?: TestRole;
    titles?: string[];
    handlers?: Handler;
    onDuplicated?: (copy: Dashboard) => void;
    onPurged?: () => void;
  } = {},
) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const calls: Array<{ method: string; path: string; body?: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      // Who is signed in, and the lists read for display, are not what a person did here.
      if (path === "/auth/me") return json(identityFor(options.role ?? "member"));
      if (method === "GET" && path === "/dashboards") {
        return json({ dashboards: (options.titles ?? []).map(summaryTitled), more: false });
      }
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ method, path, body });
      return options.handlers?.({ method, path, body }) ?? new Response(null, { status: 204 });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(DashboardActions, {
          dashboard: subject,
          onDeleted,
          onDuplicated: options.onDuplicated,
          onPurged: options.onPurged,
          now: NOW,
        }),
      ),
    );
  });
  // The session answers, and the buttons this role may use appear.
  const mayEdit = (options.role ?? "member") !== "viewer";
  for (let round = 0; round < SETTLE_ROUNDS; round++) {
    const arrived = [...container.querySelectorAll("button")].some(
      (each) => each.textContent === "Delete this dashboard",
    );
    if (arrived === mayEdit && (mayEdit || round > 3)) break;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return { container, calls, onDeleted };
}

const text = (node: Element | null) => (node?.textContent ?? "").replace(/\s+/g, " ").trim();
const button = (container: Element, name: string) =>
  [...container.querySelectorAll("button")].find((each) => text(each) === name);

describe("delete (AC136)", () => {
  it("states the title, panel count, last opened and the 30 days before anything is sent", async () => {
    const twoHoursAgo = new Date(NOW - 2 * HOUR_MS).toISOString();
    const { container, calls } = await mount(dashboard(twoHoursAgo));

    act(() => button(container, "Delete this dashboard")?.click());

    const confirm = container.querySelector("fieldset");
    expect(confirm?.getAttribute("aria-label")).toBe("Delete Checkout on-call");
    expect(document.activeElement).toBe(confirm);
    // The console's own relative words ("2 hr. ago"), as the list uses.
    expect(text(confirm)).toContain(
      `Checkout on-call, 12 panels, last opened ${formatRelativeTime(twoHoursAgo, NOW)}. It can be restored for 30 days.`,
    );
    expect(calls).toEqual([]);
  });

  it("says never opened when nobody has opened it", async () => {
    const { container } = await mount(dashboard(null));
    act(() => button(container, "Delete this dashboard")?.click());

    expect(text(container.querySelector("fieldset"))).toContain("12 panels, never opened.");
  });

  it("deletes once on confirming, and tells the page", async () => {
    const { container, calls, onDeleted } = await mount(dashboard(null));
    act(() => button(container, "Delete this dashboard")?.click());
    await act(async () => button(container, "Delete")?.click());

    expect(calls).toEqual([{ method: "DELETE", path: `/dashboards/${DASHBOARD_ID}` }]);
    expect(onDeleted).toHaveBeenCalledOnce();
  });

  it("keeps it, and gives focus back, when the person changes their mind", async () => {
    const { container, calls } = await mount(dashboard(null));
    act(() => button(container, "Delete this dashboard")?.click());
    act(() => button(container, "Keep it")?.click());

    expect(container.querySelector("fieldset")).toBeNull();
    expect(document.activeElement).toBe(button(container, "Delete this dashboard"));
    expect(calls).toEqual([]);
  });

  it("offers export first: the document alone, with no ids, owner or version (FR42, FR62)", async () => {
    const saved: Blob[] = [];
    const { container } = await mount(dashboard(null));
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: (blob: Blob) => {
        saved.push(blob);
        return "blob:dashboard";
      },
      revokeObjectURL: () => {},
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    act(() => button(container, "Delete this dashboard")?.click());

    expect(text(container.querySelector("fieldset"))).toContain(
      "The file includes notes, links, service names and host names.",
    );
    await act(async () => button(container, "Export it first")?.click());

    expect(click).toHaveBeenCalledOnce();
    const file = JSON.parse(await saved[0].text());
    expect(Object.keys(file).sort()).toEqual(["definition", "description", "title"]);
    expect(file.definition.panels).toHaveLength(12);
  });
});

const typeInto = (input: HTMLInputElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
};
const settle = async (rounds = 5) => {
  for (let round = 0; round < rounds; round++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
};

describe("duplicate (AC78)", () => {
  it("asks for a new title, proposes one, and lists the titles in use before anything is sent", async () => {
    const { container, calls } = await mount(dashboard(null), vi.fn(), {
      titles: ["Checkout on-call", "Billing"],
    });

    act(() => button(container, "Duplicate this dashboard")?.click());
    await settle();

    const field = container.querySelector<HTMLInputElement>('input[name="copy-title"]');
    expect(field?.value).toBe("Checkout on-call (copy)");
    expect(text(container)).toContain("Titles in use (2)");
    expect(text(container.querySelector("ul"))).toBe("Checkout on-callBilling");
    expect(calls).toEqual([]);
  });

  it("refuses a title in use, ignoring case, in words and sends nothing", async () => {
    const { container, calls } = await mount(dashboard(null), vi.fn(), { titles: ["Billing"] });
    act(() => button(container, "Duplicate this dashboard")?.click());
    await settle();

    act(() =>
      typeInto(container.querySelector('input[name="copy-title"]') as HTMLInputElement, "billing"),
    );
    await act(async () => button(container, "Make a copy")?.click());

    expect(text(container)).toContain("A dashboard named Billing exists. Choose another title.");
    expect(calls).toEqual([]);
  });

  it("posts the saved dashboard's text under the new title and hands the copy to the page", async () => {
    const onDuplicated = vi.fn();
    const copy = { ...dashboard(null), id: "copy-id", title: "Checkout on-call (copy)" };
    const { container, calls } = await mount(dashboard(null), vi.fn(), {
      onDuplicated,
      handlers: () => json(copy, 201),
    });
    act(() => button(container, "Duplicate this dashboard")?.click());
    await settle();

    await act(async () => button(container, "Make a copy")?.click());
    await settle();

    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ method: "POST", path: "/dashboards" });
    expect(calls[0].body).toMatchObject({
      title: "Checkout on-call (copy)",
      definition: { panels: expect.any(Array) },
    });
    expect(onDuplicated).toHaveBeenCalledOnce();
  });

  it("shows the API's own words when it refuses the title after all", async () => {
    const refusal = new Response(
      JSON.stringify({
        type: "https://nexovue.com/problems/conflict",
        title: "Conflict",
        status: 409,
        detail: "A dashboard named 'Checkout on-call (copy)' exists. Choose another title.",
      }),
      { status: 409, headers: { "content-type": "application/problem+json" } },
    );
    const { container } = await mount(dashboard(null), vi.fn(), { handlers: () => refusal });
    act(() => button(container, "Duplicate this dashboard")?.click());
    await settle();

    await act(async () => button(container, "Make a copy")?.click());
    await settle();

    expect(text(container)).toContain("exists. Choose another title.");
  });

  it("is not offered to a viewer", async () => {
    const { container } = await mount(dashboard(null), vi.fn(), { role: "viewer" });
    expect(button(container, "Duplicate this dashboard")).toBeUndefined();
    expect(button(container, "Delete this dashboard")).toBeUndefined();
  });
});

describe("removing at once (AC120)", () => {
  it("is offered to an admin, who sees what goes and what backups keep, and not to a member", async () => {
    const admin = await mount(dashboard(null), vi.fn(), { role: "admin" });
    act(() => button(admin.container, "Remove at once")?.click());
    expect(text(admin.container.querySelector("fieldset"))).toContain("Backups are outside this");
    expect(admin.calls).toEqual([]);

    const member = await mount(dashboard(null), vi.fn(), { role: "member" });
    expect(button(member.container, "Remove at once")).toBeUndefined();
  });

  it("posts one purge and tells the page the dashboard is gone", async () => {
    const onPurged = vi.fn();
    const { container, calls } = await mount(dashboard(null), vi.fn(), { role: "owner", onPurged });
    act(() => button(container, "Remove at once")?.click());
    await act(async () => {
      [...container.querySelectorAll("fieldset button")]
        .find((each) => each.textContent === "Remove at once")
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await settle();

    expect(calls).toEqual([
      { method: "POST", path: `/dashboards/${DASHBOARD_ID}/purge`, body: {} },
    ]);
    expect(onPurged).toHaveBeenCalledOnce();
  });
});
