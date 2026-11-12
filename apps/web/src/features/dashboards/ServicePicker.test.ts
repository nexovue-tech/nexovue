// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WindowChoice } from "@/shared/selection";
import { windowCacheKey } from "@/shared/selection";
import { PICKER_DEBOUNCE_MS, ServicePicker } from "./ServicePicker";

/**
 * The dashboard's service picker (F20, T7.3; FR48, FR53, FR61, AC44, AC140): a native select
 * of the project's services, each through the escapes while holding the raw name; a choice
 * sent once after a pause, not once per keystroke; a link's unknown service shown quoted and
 * marked; a typed field if the list cannot be read. Timers and the clock are faked.
 */

const PROJECT = "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f";
const CHOICE: WindowChoice = { kind: "relative", rangeId: "1h" };
const NOW = Date.parse("2026-11-12T10:00:00Z");

const mounted: Root[] = [];

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function mount(
  options: {
    services?: string[] | "failed";
    value?: string;
    fromLink?: boolean;
    onPick?: (service: string | undefined) => void;
    focus?: boolean;
  } = {},
) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  const key = ["dashboard-service-choices", PROJECT, windowCacheKey(CHOICE)];
  if (options.services === "failed") {
    // The services route is away: the hook's own read fails.
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("network down")));
  } else {
    client.setQueryData(key, {
      services: (options.services ?? ["checkout", "payment"]).map((service_name) => ({
        service_name,
      })),
    });
  }
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  const keeper = { current: options.focus ?? false };
  act(() => {
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(ServicePicker, {
          projectId: PROJECT,
          choice: CHOICE,
          value: options.value,
          fromLink: options.fromLink ?? false,
          onPick: options.onPick ?? (() => {}),
          focusKeeper: keeper,
        }),
      ),
    );
  });
  return { container, keeper };
}

function choose(select: HTMLSelectElement, value: string) {
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

const select = (container: Element) => container.querySelector("select") as HTMLSelectElement;
const texts = (container: Element) =>
  [...container.querySelectorAll("option")].map((o) => o.textContent);

describe("the choices", () => {
  it("is a native select labelled Service, with a first option that chooses nothing", () => {
    const { container } = mount();

    expect(container.querySelector("label")?.textContent).toBe("Service");
    expect(container.querySelector("label")?.getAttribute("for")).toBe(select(container).id);
    expect(texts(container)).toEqual(["Choose a service", "checkout", "payment"]);
    expect(select(container).value).toBe("");
  });

  it("shows hidden characters as escapes while the option holds the raw name (FR61)", () => {
    const hostile = "check​out";
    const { container } = mount({ services: [hostile], value: hostile });

    expect(texts(container)).toContain("check[U+200B]out");
    expect([...container.querySelectorAll("option")].map((o) => o.value)).toContain(hostile);
    expect(container.textContent).toContain("hidden characters");
  });

  it("shows the address's service as chosen", () => {
    const { container } = mount({ value: "payment" });
    expect(select(container).value).toBe("payment");
  });

  it("shows a name no list holds quoted, cut short and marked as the link's (FR53)", () => {
    const long = `ghost-${"x".repeat(60)}`;
    const { container } = mount({ value: long, fromLink: true });

    const shown = texts(container).find((each) => each?.includes("ghost"));
    expect(shown?.startsWith("“ghost-")).toBe(true);
    expect(shown?.endsWith("…” (from the link)")).toBe(true);
    expect(shown?.length).toBeLessThan(long.length);
    expect(select(container).value).toBe(long);
  });

  it("does not call a name the person picked from the link", () => {
    const { container } = mount({ value: "ghost", fromLink: false });
    expect(texts(container)).toContain("“ghost”");
  });
});

describe("debounce (AC44)", () => {
  it("sends one choice, the last, once the person has stopped for the pause", () => {
    const onPick = vi.fn();
    const { container } = mount({ onPick });

    choose(select(container), "checkout");
    act(() => vi.advanceTimersByTime(PICKER_DEBOUNCE_MS - 1));
    choose(select(container), "payment");
    act(() => vi.advanceTimersByTime(PICKER_DEBOUNCE_MS - 1));
    expect(onPick).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));

    expect(onPick).toHaveBeenCalledExactlyOnceWith("payment");
  });

  it("shows the choice at once while the address catches up", () => {
    const { container } = mount();
    choose(select(container), "checkout");
    expect(select(container).value).toBe("checkout");
  });

  it("clears the service when the first option is chosen again", () => {
    const onPick = vi.fn();
    const { container } = mount({ onPick, value: "checkout" });

    choose(select(container), "");
    act(() => vi.advanceTimersByTime(PICKER_DEBOUNCE_MS));

    expect(onPick).toHaveBeenCalledExactlyOnceWith(undefined);
  });

  it("sends nothing for a choice put back to what the address already holds", () => {
    const onPick = vi.fn();
    const { container } = mount({ onPick, value: "checkout" });

    choose(select(container), "payment");
    choose(select(container), "checkout");
    act(() => vi.advanceTimersByTime(PICKER_DEBOUNCE_MS));

    expect(onPick).not.toHaveBeenCalled();
  });

  it("sends nothing once the picker has gone", () => {
    const onPick = vi.fn();
    const { container } = mount({ onPick });
    choose(select(container), "checkout");

    act(() => {
      for (const root of mounted.splice(0)) root.unmount();
    });
    act(() => vi.advanceTimersByTime(PICKER_DEBOUNCE_MS * 2));

    expect(onPick).not.toHaveBeenCalled();
  });
});

describe("focus and failure", () => {
  it("offers a typed field, saying so, when the list cannot be read, and still debounces", async () => {
    const onPick = vi.fn();
    const { container } = mount({ services: "failed", onPick });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const field = container.querySelector<HTMLInputElement>('input[type="text"]');
    expect(field?.placeholder).toBe("Type a name");
    expect(container.textContent).toContain("Could not list services. Type a name instead.");
    expect(container.querySelector("select")).toBeNull();

    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(field, "checkout");
      field?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() => vi.advanceTimersByTime(PICKER_DEBOUNCE_MS));
    expect(onPick).toHaveBeenCalledExactlyOnceWith("checkout");
  });

  it("takes the focus a picker it replaces held", () => {
    const { container } = mount({ focus: true });
    expect(document.activeElement).toBe(select(container));
  });

  it("takes no focus otherwise", () => {
    mount({ focus: false });
    expect(document.activeElement).toBe(document.body);
  });

  it("remembers whether it holds focus, for the picker that replaces it", () => {
    const { container, keeper } = mount();
    act(() => select(container).focus());
    expect(keeper.current).toBe(true);
    act(() => select(container).blur());
    expect(keeper.current).toBe(false);
  });
});
