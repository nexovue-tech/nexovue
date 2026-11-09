// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatRelativeTime } from "@/shared/format/time";
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
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

const mounted: Root[] = [];

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function mount(subject: Dashboard, onDeleted = vi.fn()) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const calls: Array<{ method: string; path: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      calls.push({ method: init?.method ?? "GET", path: url.pathname.slice(API_PREFIX.length) });
      return new Response(null, { status: 204 });
    }),
  );
  const client = new QueryClient();
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(DashboardActions, { dashboard: subject, onDeleted }),
      ),
    );
  });
  return { container, calls, onDeleted };
}

const text = (node: Element | null) => (node?.textContent ?? "").replace(/\s+/g, " ").trim();
const button = (container: Element, name: string) =>
  [...container.querySelectorAll("button")].find((each) => text(each) === name);

describe("delete (AC136)", () => {
  it("states the title, panel count, last opened and the 30 days before anything is sent", async () => {
    const twoHoursAgo = new Date(Date.now() - 2 * HOUR_MS).toISOString();
    const { container, calls } = await mount(dashboard(twoHoursAgo));

    act(() => button(container, "Delete this dashboard")?.click());

    const confirm = container.querySelector("fieldset");
    expect(confirm?.getAttribute("aria-label")).toBe("Delete Checkout on-call");
    expect(document.activeElement).toBe(confirm);
    // The console's own relative words ("2 hr. ago"), as the list uses.
    expect(text(confirm)).toContain(
      `Checkout on-call, 12 panels, last opened ${formatRelativeTime(twoHoursAgo)}. It can be restored for 30 days.`,
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
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: (blob: Blob) => {
        saved.push(blob);
        return "blob:dashboard";
      },
      revokeObjectURL: () => {},
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const { container } = await mount(dashboard(null));
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
