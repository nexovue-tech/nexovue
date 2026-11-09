// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Definition, PanelResult, StoredPanel } from "./api";
import { blankDocument } from "./draft";
import { holdsHiddenCharacters, PanelPicker, previewFit } from "./PanelPicker";
import { SHEET_WIDTH_CLASS, SideSheet } from "./SideSheet";

/**
 * The panel picker in its side sheet (F20, T3.3): real values behind escaped labels, the
 * typed field when the list failed, and its six states (AC134); Apply reading that one
 * panel once, and typing reading nothing (AC135); a sheet that scales with the window and
 * a preview that says when it is narrower than the page (AC112). Only `fetch` is a table.
 */

const API_PREFIX = "/api/v1";
const PROJECT_ID = "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f";
const RTL_OVERRIDE = "‮";
const HOSTILE = `pay${RTL_OVERRIDE}ment`;
const SETTLE_ROUNDS = 60;

type Handler = (body: unknown) => Response;
const json =
  (body: unknown, status = 200): Handler =>
  () =>
    new Response(JSON.stringify(body), {
      status,
      headers: {
        "content-type": status >= 400 ? "application/problem+json" : "application/json",
      },
    });

function stubApi(handlers: Record<string, Handler>) {
  const calls: Array<{ method: string; path: string; body: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ method, path, body });
      const handler = handlers[`${method} ${path}`];
      if (!handler) throw new Error(`no stub for ${method} ${path}`);
      return handler(body);
    }),
  );
  return calls;
}

const okReading = (): PanelResult => ({
  state: "ok",
  kind: "service_trend",
  read_at: new Date().toISOString(),
  bucket_seconds: 60,
  data_starts_at: null,
  data_ends_at: null,
  series: [{ label: "requests_per_minute", points: [[new Date().toISOString(), 12]] }],
});

const mounted: Root[] = [];

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

async function mount(
  options: {
    initial?: StoredPanel;
    definition?: Definition;
    onKeep?: (panel: StoredPanel, reading: PanelResult | undefined) => void;
  } = {},
) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
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
        createElement(PanelPicker, {
          projectId: PROJECT_ID,
          projectName: "production",
          zone: "UTC",
          choice: { kind: "relative", rangeId: "1h" },
          definition: options.definition ?? blankDocument().definition,
          panelId: "p_1",
          initial: options.initial,
          realWidth: 0,
          onKeep: options.onKeep ?? (() => {}),
        }),
      ),
    );
  });
  return container;
}

async function waitFor(check: () => unknown, what: string): Promise<void> {
  for (let round = 0; round < SETTLE_ROUNDS; round += 1) {
    if (check()) return;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
  }
  throw new Error(`never saw ${what}`);
}

const text = (node: Element | null) => (node?.textContent ?? "").replace(/\s+/g, " ").trim();
const button = (container: Element, name: string) =>
  [...container.querySelectorAll("button")].find((each) => text(each) === name);

function labelled<T extends HTMLElement>(container: Element, label: string): T {
  const found = [...container.querySelectorAll("label")].find((each) => text(each) === label);
  const id = found?.getAttribute("for");
  const control = id ? container.querySelector<T>(`#${CSS.escape(id)}`) : null;
  if (!control) throw new Error(`no control labelled ${label}`);
  return control;
}

/** React reads a native setter's value, so a change must go through it. */
function typeInto(control: HTMLInputElement | HTMLSelectElement, value: string) {
  const prototype =
    control instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(control, value);
  control.dispatchEvent(
    new Event(control instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }),
  );
}

const services = (...names: string[]) =>
  json({ services: names.map((service_name) => ({ service_name })) });

describe("the panel picker's real values (AC134)", () => {
  it("shows each service through its escapes and keeps the raw name as the value", async () => {
    stubApi({ "GET /services": services("checkout", HOSTILE) });
    const container = await mount();
    await waitFor(() => container.querySelector("select option[value='checkout']"), "the list");

    const select = labelled<HTMLSelectElement>(container, "Service");
    const hostile = [...select.options].find((option) => option.value === HOSTILE);
    expect(hostile?.textContent).toBe("pay[U+202E]ment");
    expect(text(container)).not.toContain(RTL_OVERRIDE);
  });

  it("warns when the chosen name holds characters that do not show", async () => {
    stubApi({ "GET /services": services("checkout", HOSTILE) });
    const container = await mount();
    await waitFor(() => container.querySelector("select option[value='checkout']"), "the list");

    act(() => typeInto(labelled(container, "Service"), "checkout"));
    expect(container.querySelector('[role="alert"]')).toBeNull();

    act(() => typeInto(labelled(container, "Service"), HOSTILE));
    expect(text(container.querySelector('[role="alert"]'))).toContain(
      "holds characters that do not show on screen: pay[U+202E]ment",
    );
    expect(holdsHiddenCharacters("checkout")).toBe(false);
  });

  it("says a failed list failed, and the typed field still makes a panel", async () => {
    stubApi({ "GET /services": json({ type: "about:blank", title: "x", status: 500 }, 500) });
    const kept: StoredPanel[] = [];
    const container = await mount({ onKeep: (panel) => void kept.push(panel) });
    await waitFor(() => text(container).includes("Could not list services"), "the failure");

    expect(text(container)).toContain("Could not list services. Type a name instead.");
    act(() => typeInto(labelled(container, "Type a name"), " not-yet-reporting "));
    expect(text(container)).toContain("Reads as: not-yet-reporting");
    act(() => button(container, "Add to dashboard")?.click());

    expect(kept).toHaveLength(1);
    expect(kept[0].settings).toEqual({ service: "not-yet-reporting", signal: "rate" });
  });

  it("says when no service has reported, and offers the typed field", async () => {
    stubApi({ "GET /services": services() });
    const container = await mount();
    await waitFor(() => text(container).includes("No services have reported yet"), "the words");

    expect(labelled(container, "Type a name")).toBeTruthy();
  });

  it("shows skeleton rows while the list loads", async () => {
    stubApi({ "GET /services": () => new Promise<Response>(() => {}) as unknown as Response });
    const container = await mount();

    expect(container.querySelector('[aria-label="Listing services"]')).not.toBeNull();
  });

  it("refuses to add a service panel naming no service, and says why", async () => {
    stubApi({ "GET /services": services("checkout") });
    const kept: StoredPanel[] = [];
    const container = await mount({ onKeep: (panel) => void kept.push(panel) });
    await waitFor(() => container.querySelector("select option[value='checkout']"), "the list");

    act(() => button(container, "Add to dashboard")?.click());
    expect(kept).toEqual([]);
    expect(text(container.querySelector('[role="alert"]'))).toBe(
      "Choose a service, or type its name.",
    );
  });
});

describe("Apply (AC135)", () => {
  it("reads that one panel once per Apply, and typing reads nothing", async () => {
    const calls = stubApi({
      "GET /services": services("checkout"),
      "POST /dashboard-panels/preview": json({
        correlation_id: "corr-7",
        window_start: new Date().toISOString(),
        window_end: new Date().toISOString(),
        panels: { p_1: okReading() },
      }),
    });
    const container = await mount();
    await waitFor(() => container.querySelector("select option[value='checkout']"), "the list");
    const previews = () => calls.filter((call) => call.path === "/dashboard-panels/preview");

    act(() => typeInto(labelled(container, "Service"), "checkout"));
    act(() => typeInto(labelled(container, "Title (optional)"), "Checkout rate"));
    expect(previews()).toHaveLength(0);

    act(() => button(container, "Apply")?.click());
    await waitFor(() => text(container).includes("Applied"), "the preview");
    expect(previews()).toHaveLength(1);
    expect(previews()[0].body).toMatchObject({
      project_id: PROJECT_ID,
      range: "1h",
      panels: ["p_1"],
    });
    const [panel] = (previews()[0].body as { definition: Definition }).definition.panels;
    expect(panel).toMatchObject({ id: "p_1", title: "Checkout rate" });

    act(() => typeInto(labelled(container, "Title (optional)"), "Checkout requests"));
    expect(previews()).toHaveLength(1);
    act(() => button(container, "Apply")?.click());
    await waitFor(() => previews().length === 2, "a second read");
  });

  it("reads again for a different draft of the same panel, never a kept answer", async () => {
    const calls = stubApi({
      "GET /services": services("checkout", "payment"),
      "POST /dashboard-panels/preview": json({
        correlation_id: "corr-7",
        window_start: new Date().toISOString(),
        window_end: new Date().toISOString(),
        panels: { p_1: okReading() },
      }),
    });
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    // One client for both sheets, as the builder has: two sessions of the sheet each
    // start counting their Applies at one.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    mounted.push(root);
    const sheet = (initial: StoredPanel, session: number) =>
      createElement(
        QueryClientProvider,
        { client },
        createElement(PanelPicker, {
          key: session,
          projectId: PROJECT_ID,
          projectName: "production",
          zone: "UTC",
          choice: { kind: "relative", rangeId: "1h" },
          definition: blankDocument().definition,
          panelId: "p_1",
          initial,
          realWidth: 0,
          onKeep: () => {},
        }),
      );
    const panelFor = (service: string): StoredPanel => ({
      id: "p_1",
      kind: "service_trend",
      title: null,
      width: 4,
      height: "medium",
      settings: { service, signal: "rate" },
    });
    const previews = () => calls.filter((call) => call.path === "/dashboard-panels/preview");

    await act(async () => root.render(sheet(panelFor("checkout"), 1)));
    act(() => button(container, "Apply")?.click());
    await waitFor(() => previews().length === 1, "the first read");
    // Let the first answer settle into the cache, as it would before a second sheet opens.
    await act(async () => new Promise((resolve) => setTimeout(resolve, 50)));
    await act(async () => root.render(sheet(panelFor("payment"), 2)));
    act(() => button(container, "Apply")?.click());

    await waitFor(() => previews().length === 2, "a read of the second draft");
    const [, second] = previews();
    const [panel] = (second.body as { definition: Definition }).definition.panels;
    expect(panel.settings).toEqual({ service: "payment", signal: "rate" });
  });

  it("keeps the applied reading with the panel it was read for", async () => {
    stubApi({
      "GET /services": services("checkout"),
      "POST /dashboard-panels/preview": json({
        correlation_id: "corr-7",
        window_start: new Date().toISOString(),
        window_end: new Date().toISOString(),
        panels: { p_1: okReading() },
      }),
    });
    const kept: Array<[StoredPanel, PanelResult | undefined]> = [];
    const container = await mount({
      onKeep: (panel, reading) => void kept.push([panel, reading]),
    });
    await waitFor(() => container.querySelector("select option[value='checkout']"), "the list");
    act(() => typeInto(labelled(container, "Service"), "checkout"));
    act(() => button(container, "Apply")?.click());
    await waitFor(() => text(container).includes("Applied"), "the preview");

    act(() => button(container, "Add to dashboard")?.click());
    expect(kept[0][1]?.state).toBe("ok");
  });

  it("says a refused preview in the panel's words, and Apply stays available", async () => {
    stubApi({
      "GET /services": services("checkout"),
      "POST /dashboard-panels/preview": json({
        correlation_id: "corr-8",
        window_start: new Date().toISOString(),
        window_end: new Date().toISOString(),
        panels: { p_1: { state: "refused", kind: "service_trend", retry_after_seconds: 20 } },
      }),
    });
    const container = await mount();
    await waitFor(() => container.querySelector("select option[value='checkout']"), "the list");
    act(() => typeInto(labelled(container, "Service"), "checkout"));
    act(() => button(container, "Apply")?.click());
    await waitFor(() => text(container).includes("Too many reads just now."), "the words");

    expect(button(container, "Apply")).toBeDefined();
  });

  it("says why the API refused the draft, in its own words", async () => {
    stubApi({
      "GET /services": services("checkout"),
      "POST /dashboard-panels/preview": json(
        {
          type: "about:blank",
          title: "Validation failed",
          status: 422,
          detail: "This dashboard would need 44 reads, and one page may make 40.",
        },
        422,
      ),
    });
    const container = await mount();
    await waitFor(() => container.querySelector("select option[value='checkout']"), "the list");
    act(() => typeInto(labelled(container, "Service"), "checkout"));
    act(() => button(container, "Apply")?.click());

    await waitFor(() => text(container).includes("Could not preview"), "the refusal");
    expect(text(container.querySelector('[aria-label="Preview"]'))).toContain(
      "Could not preview: This dashboard would need 44 reads, and one page may make 40.",
    );
  });

  it("draws a note from its own text, with no read", async () => {
    const calls = stubApi({ "GET /services": services("checkout") });
    const container = await mount();
    const note = [...container.querySelectorAll<HTMLInputElement>("input[type=radio]")].find(
      (radio) => radio.value === "note",
    );
    act(() => note?.click());
    const area = container.querySelector("textarea");
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(
        area,
        "Check the queue",
      );
      area?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() => button(container, "Apply")?.click());

    expect(text(container.querySelector('[aria-label="Preview"]'))).toContain("Check the queue");
    expect(calls.filter((call) => call.path === "/dashboard-panels/preview")).toEqual([]);
  });
});

describe("the sheet and the preview's width (AC112)", () => {
  it("scales with the window, and fills the screen below lg", async () => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    mounted.push(root);
    const closed = vi.fn();
    await act(async () => {
      root.render(createElement(SideSheet, { title: "Add a panel", onClose: closed }, "Body"));
    });

    const dialog = container.querySelector("dialog");
    expect(SHEET_WIDTH_CLASS).toBe("lg:w-[clamp(28rem,40vw,60rem)]");
    expect(dialog?.className).toContain(SHEET_WIDTH_CLASS);
    expect(dialog?.className).toContain("w-full");
    expect(dialog?.getAttribute("aria-labelledby")).toBe(dialog?.querySelector("h2")?.id);

    act(() => dialog?.dispatchEvent(new Event("cancel")));
    expect(closed).toHaveBeenCalledOnce();
  });

  it("draws at the page's width when it fits, and says when it is narrower", () => {
    expect(previewFit(400, 600)).toEqual({ width: 400, narrower: false });
    expect(previewFit(900, 600)).toEqual({ narrower: true });
    expect(previewFit(0, 600)).toEqual({ narrower: false });
  });
});
