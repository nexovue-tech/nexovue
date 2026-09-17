// @vitest-environment jsdom

import { act, createElement, useState } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { EditableRows } from "./EditableRows";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

interface Header {
  key: string;
  name: string;
}

let nextKey = 0;
const freshHeader = (): Header => ({ key: `fresh-${nextKey++}`, name: "" });

/** Rows held the way a screen holds them; `seen` records every list passed to onChange. */
function Harness({
  initial,
  max,
  rowErrors,
  seen,
}: {
  initial: Header[];
  max?: number;
  rowErrors?: Record<number, string>;
  seen: Header[][];
}) {
  const [rows, setRows] = useState(initial);
  return createElement(EditableRows<Header>, {
    label: "Headers",
    rows,
    onChange: (next) => {
      seen.push(next);
      setRows(next);
    },
    newRow: freshHeader,
    renderRow: (row, update, index) =>
      createElement("input", {
        "aria-label": `Header name ${index + 1}`,
        value: row.name,
        onChange: (event: { target: { value: string } }) =>
          update({ ...row, name: event.target.value }),
      }),
    addLabel: "Add header",
    emptyLabel: "No headers yet.",
    max,
    rowErrors,
  });
}

function render(props: { initial: Header[]; max?: number; rowErrors?: Record<number, string> }) {
  const seen: Header[][] = [];
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  act(() => root.render(createElement(Harness, { ...props, seen })));

  const button = (name: string) => {
    const found = [...container.querySelectorAll("button")].find(
      (candidate) => (candidate.getAttribute("aria-label") ?? candidate.textContent) === name,
    );
    if (!found) throw new Error(`no button named ${name}`);
    return found;
  };

  return {
    container,
    seen,
    button,
    inputs: () => [...container.querySelectorAll("input")],
    lastRows: () => seen.at(-1),
    click: (name: string) => act(() => button(name).click()),
    type(input: HTMLInputElement, next: string) {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      act(() => {
        setter?.call(input, next);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    },
  };
}

const THREE: Header[] = [
  { key: "a", name: "Accept" },
  { key: "b", name: "Authorization" },
  { key: "c", name: "Cookie" },
];

describe("EditableRows", () => {
  it("says the list is empty rather than rendering nothing", () => {
    const view = render({ initial: [] });

    expect(view.container.textContent).toContain("No headers yet.");
    expect(view.container.querySelector("ul")).toBeNull();
  });

  it("names the list and each row's remove button by position", () => {
    const view = render({ initial: THREE });

    expect(view.container.querySelector("ul")?.getAttribute("aria-label")).toBe("Headers");
    expect(view.button("Remove row 1")).toBeDefined();
    expect(view.button("Remove row 3")).toBeDefined();
  });

  it("adds a fresh row at the end and leaves the others untouched", () => {
    const view = render({ initial: THREE });

    view.click("Add header");

    const rows = view.lastRows();
    expect(rows?.map((row) => row.key).slice(0, 3)).toEqual(["a", "b", "c"]);
    expect(rows?.slice(0, 3)).toEqual(THREE);
    expect(rows).toHaveLength(4);
    expect(rows?.[3]?.key.startsWith("fresh-")).toBe(true);
  });

  it("removes only the row asked for", () => {
    const view = render({ initial: THREE });

    view.click("Remove row 2");

    expect(view.lastRows()).toEqual([THREE[0], THREE[2]]);
  });

  it("replaces only the row being edited", () => {
    const view = render({ initial: THREE });
    const second = view.inputs()[1];
    if (!second) throw new Error("no second input");

    view.type(second, "X-Trace");

    const rows = view.lastRows();
    expect(rows?.[0]).toBe(THREE[0]);
    expect(rows?.[1]).toEqual({ key: "b", name: "X-Trace" });
    expect(rows?.[2]).toBe(THREE[2]);
  });

  it("keeps each row's DOM with its key when an earlier row is removed", () => {
    // Keyed by index, removing row 1 would hand row 2's element to row 1's successor.
    const view = render({ initial: THREE });
    const cookieInput = view.inputs()[2];

    view.click("Remove row 1");

    expect(view.inputs()[1]).toBe(cookieInput);
    expect(view.inputs().map((input) => input.value)).toEqual(["Authorization", "Cookie"]);
  });

  it("moves focus into the row just added", () => {
    const view = render({ initial: THREE });

    view.click("Add header");

    expect(document.activeElement).toBe(view.inputs()[3]);
  });

  it("moves focus to the next row after a remove", () => {
    const view = render({ initial: THREE });

    view.click("Remove row 2");

    expect(document.activeElement).toBe(view.inputs()[1]);
    expect((document.activeElement as HTMLInputElement).value).toBe("Cookie");
  });

  it("moves focus to the add button after removing the last row", () => {
    const view = render({ initial: THREE });

    view.click("Remove row 3");

    expect(document.activeElement).toBe(view.button("Add header"));
  });

  it("disables add at the limit and says what the limit is", () => {
    const view = render({ initial: THREE.slice(0, 2), max: 3 });
    expect(view.button("Add header").disabled).toBe(false);

    view.click("Add header");

    expect(view.button("Add header").disabled).toBe(true);
    expect(view.container.textContent).toContain("Headers holds at most 3 rows.");
    const calls = view.seen.length;
    view.click("Add header");
    expect(view.seen).toHaveLength(calls);
  });

  it("describes a row by its problem", () => {
    const view = render({
      initial: THREE,
      rowErrors: { 1: "A header name cannot contain spaces." },
    });

    const groups = [...view.container.querySelectorAll("fieldset")];
    const describedBy = groups[1]?.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")?.textContent).toBe(
      "A header name cannot contain spaces.",
    );
    expect(groups[0]?.hasAttribute("aria-describedby")).toBe(false);
    expect(groups[1]?.getAttribute("aria-label")).toBe("Row 2");
  });
});
