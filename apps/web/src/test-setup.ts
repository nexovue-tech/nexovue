/**
 * What jsdom is missing and a charting library assumes.
 *
 * uPlot reads `matchMedia` at module load to pick a device pixel ratio, so a
 * test that imports any chart fails while *collecting* — before a single test
 * runs, and with a stack pointing into `node_modules` rather than at anything
 * a reader wrote.
 *
 * Defined only when absent, so a test that wants its own answer can still give
 * one. `matches: false` is a real answer rather than a stub that pretends the
 * question was not asked: it resolves to the dark palette, which is what a
 * browser with no light-scheme preference reports.
 */

if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

/**
 * jsdom has no `ResizeObserver`, and every chart here watches its host so the
 * canvas follows the column it sits in.
 *
 * It never fires: nothing in jsdom lays anything out, so a resize that reported
 * a size would be reporting a measurement that does not exist. What this buys
 * is that constructing one does not throw — the component under test renders,
 * and its *behaviour* is what the test asserts on rather than its geometry.
 * Anything about real widths has to be measured in a browser, which is where
 * the trend column's own width bug was found.
 */
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
