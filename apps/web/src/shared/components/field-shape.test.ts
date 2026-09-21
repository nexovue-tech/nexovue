/**
 * A form row lines up because its controls are the same shape, not because
 * somebody nudged one of them.
 *
 * `TextField` and `NumberField` put the label *above* a 34px box. `SelectField`
 * has two shapes: the toolbar one puts the label *beside* a 26px box, and
 * `stacked` matches the text fields. Mixing them inside a form is a defect with
 * a look — `items-end` flushes the bottoms, the select still reads as sitting
 * low, and the fix somebody reaches for is a `pb-1` on whatever is next to it.
 *
 * That shim was on four forms here and was removed twice, because removing it
 * treats the symptom: the mismatch is what put it there. So this reads `src/`
 * and fails on the shape of a change, the way `status-interpretation.test.ts`
 * does — the next select dropped into a form is named here rather than found by
 * the founder on a screenshot (`docs/changes/console-review-fixes/`).
 *
 * Outside a form a select is free: the project selector, the range picker and
 * the role changer on a member row are toolbar controls, and the inline shape is
 * the right one for them.
 */

import { describe, expect, it } from "vitest";

const SOURCES: Record<string, string> = import.meta.glob("/src/**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
});

const within = (path: string) => path.replace(/^\/src\//, "");

/**
 * Every `<form …>…</form>` in a file, as text.
 *
 * Forms do not nest — HTML forbids it — so a non-greedy match from each `<form`
 * to the next `</form>` takes exactly one form each time.
 */
function forms(source: string): string[] {
  return source.match(/<form[\s\S]*?<\/form>/g) ?? [];
}

/** Each `<SelectField … />` element inside one block of source. */
function selects(block: string): string[] {
  return block.match(/<SelectField[\s\S]*?\/>/g) ?? [];
}

/** The `stacked` prop, written bare or as `stacked={true}`. */
const isStacked = (element: string) => /\bstacked\b/.test(element);

describe("a select inside a form", () => {
  it("is the stacked shape, so it is the height of the fields beside it", () => {
    const wrong: string[] = [];

    for (const [path, source] of Object.entries(SOURCES)) {
      if (path.endsWith(".test.tsx")) continue;
      for (const form of forms(source)) {
        for (const element of selects(form)) {
          if (isStacked(element)) continue;
          const label = element.match(/label="([^"]*)"/)?.[1] ?? "(unlabelled)";
          wrong.push(`${within(path)} — <SelectField label="${label}">`);
        }
      }
    }

    expect(
      wrong,
      `These selects sit in a form wearing the toolbar shape, so they are 26px ` +
        `tall against the 34px fields beside them and the row cannot line up. ` +
        `Add \`stacked\`; do not add padding to their neighbours.`,
    ).toEqual([]);
  });
});
