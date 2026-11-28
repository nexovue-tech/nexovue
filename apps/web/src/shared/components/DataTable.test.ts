// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Column, TableSort } from "./DataTable";
import { DataTable } from "./DataTable";

/**
 * The table's header buttons (F20, T7.2, AC46). Sorting is the caller's: the table says which
 * column the rows are in the order of, with `aria-sort` on every sortable header, and asks.
 */

interface Row {
  name: string;
  opened: number;
}

const ROWS: Row[] = [
  { name: "Checkout", opened: 3 },
  { name: "Billing", opened: 9 },
];

const COLUMNS: Column<Row>[] = [
  { key: "name", header: "Name", sortable: true, cell: (row) => row.name },
  { key: "opened", header: "Opened", numeric: true, sortable: true, cell: (row) => row.opened },
  { key: "kind", header: "Kind", cell: () => "dashboard" },
  { key: "extra", header: "Extra", wideOnly: true, cell: () => "more" },
];

const mounted: Root[] = [];

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

function mount(sort: TableSort | undefined, rows: Row[] = ROWS): HTMLElement {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  act(() => {
    root.render(
      createElement(DataTable<Row>, {
        columns: COLUMNS,
        rows,
        rowKey: (row: Row) => row.name,
        caption: "Things",
        empty: "Nothing here.",
        sort,
      }),
    );
  });
  return container;
}

const header = (container: HTMLElement, name: string) =>
  [...container.querySelectorAll("th")].find((th) => th.textContent?.includes(name));

describe("sort headers (AC46)", () => {
  it("gives every sortable header a real button and an aria-sort, and no other header either", () => {
    const container = mount({ key: "name", direction: "ascending", onSort: () => {} });

    expect(header(container, "Name")?.getAttribute("aria-sort")).toBe("ascending");
    // A sortable header that is not the sorted one says "none", so each carries the attribute.
    expect(header(container, "Opened")?.getAttribute("aria-sort")).toBe("none");
    expect(header(container, "Name")?.querySelector("button")?.tagName).toBe("BUTTON");
    expect(header(container, "Opened")?.querySelector("button")).not.toBeNull();
    expect(header(container, "Kind")?.hasAttribute("aria-sort")).toBe(false);
    expect(header(container, "Kind")?.querySelector("button")).toBeNull();
  });

  it("says descending on the column the rows are in the reverse order of", () => {
    const container = mount({ key: "opened", direction: "descending", onSort: () => {} });

    expect(header(container, "Opened")?.getAttribute("aria-sort")).toBe("descending");
    expect(header(container, "Name")?.getAttribute("aria-sort")).toBe("none");
  });

  it("asks for a column by its key when its header is pressed, and orders nothing itself", () => {
    const onSort = vi.fn();
    const container = mount({ key: "name", direction: "ascending", onSort });

    act(() => header(container, "Opened")?.querySelector("button")?.click());

    expect(onSort).toHaveBeenCalledExactlyOnceWith("opened");
    const names = [...container.querySelectorAll("tbody tr td:first-child")].map(
      (cell) => cell.textContent,
    );
    expect(names).toEqual(["Checkout", "Billing"]);
  });

  it("is a plain table, as before, with no sort given", () => {
    const container = mount(undefined);

    expect(container.querySelectorAll("th button")).toHaveLength(0);
    expect(container.querySelectorAll("th[aria-sort]")).toHaveLength(0);
    expect(header(container, "Name")?.textContent).toBe("Name");
  });
});

describe("the table's other states", () => {
  it("says what is empty in a sentence instead of an empty table", () => {
    const container = mount(undefined, []);

    expect(container.querySelector("table")).toBeNull();
    expect(container.textContent).toBe("Nothing here.");
  });

  it("leaves a wide-only column out below md, header and cells alike", () => {
    const container = mount(undefined);

    expect(header(container, "Extra")?.className).toContain("hidden md:table-cell");
    expect(container.querySelector("tbody tr td:last-child")?.className).toContain(
      "hidden md:table-cell",
    );
    expect(header(container, "Name")?.className).not.toContain("hidden");
  });
});

describe("the gap between columns", () => {
  const gapRight = (element: Element | null | undefined) =>
    (element?.className ?? "").split(" ").includes("pr-4");

  it("follows a right-aligned column too, so its digits never touch the next column's words", () => {
    const container = mount(undefined);

    // "Opened" is right-aligned and the "Kind" column starts at its right edge.
    expect(gapRight(header(container, "Opened"))).toBe(true);
    expect(gapRight(container.querySelectorAll("tbody tr:first-child td")[1])).toBe(true);
  });

  it("leaves none after the last column, which ends at the table's edge", () => {
    const container = mount(undefined);
    const lastHeader = [...container.querySelectorAll("th")].at(-1);
    const lastCell = [...container.querySelectorAll("tbody tr:first-child td")].at(-1);

    expect(gapRight(lastHeader)).toBe(false);
    expect(gapRight(lastCell)).toBe(false);
  });
});
