/**
 * One `<main>` per page, and the shell owns it.
 *
 * `SettingsLayout` rendered a second `<main>` for its content column, inside the
 * shell's own. Two are invalid HTML and hand a screen reader's landmark list a
 * duplicate with no way to tell which is the page — the kind of defect that never
 * shows up in a screenshot and never fails a DOM assertion about what a screen says.
 *
 * Read as text, like `status-interpretation.test.ts`: the question is whether
 * tomorrow's component can reintroduce it, not whether today's pages are fine.
 */

import { describe, expect, it } from "vitest";

const SOURCES: Record<string, string> = import.meta.glob("/src/**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
});

const within = (path: string) => path.replace(/^\/src\//, "");

/**
 * Where a `<main>` belongs.
 *
 * `app/layout/AppShell.tsx` is the signed-in shell: every screen renders inside it,
 * so the shell's is the page's one landmark.
 *
 * `features/docs/DocsScreen.tsx` is the exception and stays one: the setup pages are
 * routed off the root rather than through the shell — unauthenticated, so somebody
 * can read them before signing up — and are therefore the whole page themselves.
 *
 * `admin/AdminApp.tsx` is the staff console's frame (F31): a separate page,
 * `admin.html`, with no shell around it.
 */
const MAY_RENDER_A_MAIN: readonly string[] = [
  "app/layout/AppShell.tsx",
  "features/docs/DocsScreen.tsx",
  "admin/AdminApp.tsx",
];

const RENDERS_A_MAIN = /<main[\s>]|role="main"/;

function offenders(): string[] {
  return Object.keys(SOURCES)
    .map(within)
    .filter((path) => !/\.test\.tsx?$/.test(path))
    .filter((path) => !MAY_RENDER_A_MAIN.includes(path))
    .filter((path) => RENDERS_A_MAIN.test(SOURCES[`/src/${path}`]))
    .sort();
}

describe("the main landmark", () => {
  it("is rendered by the shell and the unauthenticated docs, and nowhere else", () => {
    expect(offenders()).toEqual([]);
  });

  it("is rendered by both of those, so this suite cannot pass by naming nothing", () => {
    for (const path of MAY_RENDER_A_MAIN) {
      expect(SOURCES[`/src/${path}`], path).toMatch(RENDERS_A_MAIN);
    }
  });
});
