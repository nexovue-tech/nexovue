// @vitest-environment jsdom

import { act, createElement, useState } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { type TabItem, Tabs } from "./Tabs";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

const REQUEST_TABS: readonly TabItem[] = [
  { id: "params", label: "Params" },
  { id: "headers", label: "Headers", count: 3 },
  { id: "body", label: "Body", invalid: true },
  { id: "settings", label: "Settings" },
];

/** Selection held the way a screen holds it, so a key press re-renders like the real thing. */
function Harness({ initial }: { initial: string }) {
  const [selected, setSelected] = useState(initial);
  // Tabs requires its panel, which createElement's positional children cannot satisfy in the types.
  const props: Parameters<typeof Tabs>[0] = {
    label: "Request",
    items: REQUEST_TABS,
    selected,
    onSelect: setSelected,
    children: createElement("p", null, `panel for ${selected}`),
  };
  return createElement(Tabs, props);
}

function render(initial = "params") {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  act(() => root.render(createElement(Harness, { initial })));

  const tabs = () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
  const tab = (label: string) => {
    const found = tabs().find((candidate) => candidate.textContent?.startsWith(label));
    if (!found) throw new Error(`no tab labelled ${label}`);
    return found;
  };
  const panel = () => {
    const found = container.querySelector<HTMLElement>('[role="tabpanel"]');
    if (!found) throw new Error("no tab panel");
    return found;
  };

  return {
    container,
    tabs,
    tab,
    panel,
    selectedLabel: () =>
      tabs()
        .find((candidate) => candidate.getAttribute("aria-selected") === "true")
        ?.textContent?.split(" ")[0],
    press(key: string) {
      const target = document.activeElement;
      if (!(target instanceof HTMLElement)) throw new Error("nothing has focus");
      act(() => {
        target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
      });
    },
  };
}

describe("Tabs", () => {
  it("wires the tab list, tabs and panel together", () => {
    const view = render("headers");
    const list = view.container.querySelector('[role="tablist"]');

    expect(list?.getAttribute("aria-label")).toBe("Request");
    for (const tab of view.tabs()) {
      expect(tab.getAttribute("aria-controls")).toBe(view.panel().id);
      expect(tab.id).not.toBe("");
    }
    expect(view.panel().getAttribute("aria-labelledby")).toBe(view.tab("Headers").id);
    expect(view.panel().tabIndex).toBe(0);
    expect(view.panel().textContent).toBe("panel for headers");
  });

  it("gives the list one tab stop, on the selected tab", () => {
    const view = render("body");

    expect(view.tabs().map((tab) => tab.tabIndex)).toEqual([-1, -1, 0, -1]);
    expect(view.tab("Body").getAttribute("aria-selected")).toBe("true");
    expect(view.tab("Params").getAttribute("aria-selected")).toBe("false");
  });

  it("moves focus and selection together with the arrow keys", () => {
    const view = render("params");
    view.tab("Params").focus();

    view.press("ArrowRight");

    expect(document.activeElement).toBe(view.tab("Headers"));
    expect(view.selectedLabel()).toBe("Headers");
    expect(view.panel().textContent).toBe("panel for headers");
    expect(view.tab("Headers").tabIndex).toBe(0);
    expect(view.tab("Params").tabIndex).toBe(-1);

    view.press("ArrowLeft");

    expect(document.activeElement).toBe(view.tab("Params"));
    expect(view.selectedLabel()).toBe("Params");
  });

  it("wraps past either end", () => {
    const view = render("params");
    view.tab("Params").focus();

    view.press("ArrowLeft");
    expect(document.activeElement).toBe(view.tab("Settings"));
    expect(view.selectedLabel()).toBe("Settings");

    view.press("ArrowRight");
    expect(document.activeElement).toBe(view.tab("Params"));
    expect(view.selectedLabel()).toBe("Params");
  });

  it("jumps to the first and last tab with Home and End", () => {
    const view = render("headers");
    view.tab("Headers").focus();

    view.press("End");
    expect(document.activeElement).toBe(view.tab("Settings"));
    expect(view.selectedLabel()).toBe("Settings");

    view.press("Home");
    expect(document.activeElement).toBe(view.tab("Params"));
    expect(view.selectedLabel()).toBe("Params");
  });

  it("leaves other keys alone", () => {
    const view = render("headers");
    view.tab("Headers").focus();

    view.press("ArrowDown");

    expect(document.activeElement).toBe(view.tab("Headers"));
    expect(view.selectedLabel()).toBe("Headers");
  });

  it("selects a tab on click", () => {
    const view = render("params");

    act(() => view.tab("Settings").click());

    expect(view.selectedLabel()).toBe("Settings");
    expect(view.panel().getAttribute("aria-labelledby")).toBe(view.tab("Settings").id);
  });

  it("shows a count after the label, and nothing when there is none", () => {
    const view = render();

    expect(view.tab("Headers").textContent).toBe("Headers (3)");
    expect(view.tab("Params").textContent).toBe("Params");
  });

  it("names an invalid tab's problem in text, not only with an icon", () => {
    const view = render();

    expect(view.tab("Body").textContent).toBe("Body, has a problem");
    expect(view.tab("Settings").textContent).toBe("Settings");
  });

  it("keeps a tab stop when the selection names no tab", () => {
    const view = render("missing");

    expect(view.tabs().map((tab) => tab.tabIndex)).toEqual([0, -1, -1, -1]);
    expect(view.panel().hasAttribute("aria-labelledby")).toBe(false);
  });
});
