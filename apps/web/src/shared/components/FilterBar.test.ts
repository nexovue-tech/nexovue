// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import type { FilterDefinition, FilterValues } from "./FilterBar";
import { appliedFilters, FilterBar } from "./FilterBar";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

const KIND: FilterDefinition = {
  key: "kind",
  label: "Kind",
  control: {
    kind: "select",
    options: [
      { value: "host", label: "Hosts" },
      { value: "container", label: "Containers" },
    ],
  },
};

const STALE: FilterDefinition = {
  key: "stale_only",
  label: "Only stale",
  control: { kind: "toggle", on: "true" },
};

const DEFINITIONS = [KIND, STALE];

interface Change {
  key: string;
  value: string | undefined;
}

function render(values: FilterValues): { container: HTMLElement; changes: Change[] } {
  const changes: Change[] = [];
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  act(() =>
    root.render(
      createElement(FilterBar, {
        definitions: DEFINITIONS,
        values,
        onChange: (key, value) => changes.push({ key, value }),
        onClearAll: () => changes.push({ key: "*", value: undefined }),
      }),
    ),
  );
  return { container, changes };
}

function select(container: HTMLElement): HTMLSelectElement {
  const found = container.querySelector("select");
  if (!found) throw new Error("no select rendered");
  return found;
}

function optionValues(container: HTMLElement): string[] {
  return [...select(container).options].map((option) => option.value);
}

describe("appliedFilters", () => {
  it("reports nothing when no key carries a value", () => {
    expect(appliedFilters(DEFINITIONS, {})).toEqual([]);
  });

  it("treats a blank string as not filtering", () => {
    // `?kind=` reads as a filter in the address bar and is not one. Letting it
    // through would show a chip the reader cannot act on, naming no value.
    expect(appliedFilters(DEFINITIONS, { kind: "" })).toEqual([]);
  });

  it("describes a selected value by its label, not its stored spelling", () => {
    expect(appliedFilters(DEFINITIONS, { kind: "container" })).toEqual([
      { key: "kind", label: "Kind", value: "Containers" },
    ]);
  });

  it("falls back to the raw value when the option is not among those declared", () => {
    // A shared link naming a host that has since gone quiet.
    expect(appliedFilters(DEFINITIONS, { kind: "web-07" })).toEqual([
      { key: "kind", label: "Kind", value: "web-07" },
    ]);
  });

  it("uses the screen's own wording when it supplies one", () => {
    const slower: FilterDefinition = {
      key: "min_mean_ms",
      label: "Slower than",
      control: { kind: "select", options: [{ value: "50", label: "50 ms" }] },
      describe: (value) => `${value} ms`,
    };

    expect(appliedFilters([slower], { min_mean_ms: "250" })).toEqual([
      { key: "min_mean_ms", label: "Slower than", value: "250 ms" },
    ]);
  });
});

describe("FilterBar", () => {
  it("offers an all option ahead of the declared ones", () => {
    expect(optionValues(render({}).container)).toEqual(["", "host", "container"]);
  });

  it("keeps a selected value the screen no longer offers", () => {
    // Without this the native select shows "All" while the filter is still
    // applied — the control saying one thing and the results another.
    const { container } = render({ kind: "web-07" });

    expect(optionValues(container)).toEqual(["", "web-07", "host", "container"]);
    expect(select(container).value).toBe("web-07");
  });

  it("clears the key rather than sending a blank when all is chosen", () => {
    const { container, changes } = render({ kind: "host" });

    act(() => {
      select(container).value = "";
      select(container).dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(changes).toEqual([{ key: "kind", value: undefined }]);
  });

  it("writes the declared value when a toggle is checked, and clears it when not", () => {
    const off = render({});
    const checkbox = off.container.querySelector<HTMLInputElement>("input[type=checkbox]");
    if (!checkbox) throw new Error("no toggle rendered");
    expect(checkbox.checked).toBe(false);

    // `click()` rather than a dispatched event: it flips `checked` and fires the
    // click React maps to `onChange` for a checkbox, which a bare change event
    // does not.
    act(() => checkbox.click());
    expect(off.changes).toEqual([{ key: "stale_only", value: "true" }]);

    const on = render({ stale_only: "true" });
    const checked = on.container.querySelector<HTMLInputElement>("input[type=checkbox]");
    if (!checked) throw new Error("no toggle rendered");
    expect(checked.checked).toBe(true);

    act(() => checked.click());
    expect(on.changes).toEqual([{ key: "stale_only", value: undefined }]);
  });

  it("shows no chips and no way to clear them when nothing is applied", () => {
    const { container } = render({});

    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("names each applied filter and removes only that one", () => {
    const { container, changes } = render({ kind: "host", stale_only: "true" });
    const removals = [...container.querySelectorAll("button")].filter((button) =>
      (button.getAttribute("aria-label") ?? "").startsWith("Remove"),
    );

    expect(removals.map((button) => button.getAttribute("aria-label"))).toEqual([
      "Remove the kind filter",
      "Remove the only stale filter",
    ]);

    act(() => removals[0].dispatchEvent(new Event("click", { bubbles: true })));
    expect(changes).toEqual([{ key: "kind", value: undefined }]);
  });

  it("offers one way out of every filter at once", () => {
    const { container, changes } = render({ kind: "host", stale_only: "true" });
    const clearAll = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Clear all",
    );
    if (!clearAll) throw new Error("no clear-all rendered");

    act(() => clearAll.dispatchEvent(new Event("click", { bubbles: true })));
    expect(changes).toEqual([{ key: "*", value: undefined }]);
  });
});
