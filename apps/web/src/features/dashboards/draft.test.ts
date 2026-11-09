import { describe, expect, it } from "vitest";

import type { DashboardDocument, StoredPanel } from "./api";
import type { Draft } from "./draft";
import {
  announce,
  blankDocument,
  canChangeHeight,
  canMove,
  canResize,
  draftOf,
  isDirty,
  MAX_PANELS,
  newPanelId,
  reduceDraft,
  worstCaseReads,
} from "./draft";

/**
 * The builder's draft as a pure reducer (F20, T3.2): add, move, resize, change height,
 * duplicate, remove and undo, each one a value in and a value out, so the keyboard
 * screen's behaviour rests on something tested without a browser. AC133: dirty means a
 * value differs from what is stored, so a change undone by hand does not warn.
 */

const panel = (id: string, overrides: Partial<StoredPanel> = {}): StoredPanel => ({
  id,
  kind: "service_trend",
  title: null,
  width: 4,
  height: "medium",
  settings: { service: "checkout", signal: "rate" },
  ...overrides,
});

function documentWith(...panels: StoredPanel[]): DashboardDocument {
  const blank = blankDocument();
  return {
    ...blank,
    title: "Checkout on-call",
    definition: { ...blank.definition, panels },
  };
}

const RATE = panel("p_1");
const ERRORS = panel("p_2", { settings: { service: "checkout", signal: "errors" } });
const NOTE = panel("p_3", { kind: "note", height: "small", settings: { text: "Read me" } });

const ids = (draft: Draft) => draft.document.definition.panels.map((each) => each.id);
const start = () => draftOf(documentWith(RATE, ERRORS, NOTE));

describe("the draft reducer", () => {
  it("moves a panel earlier and later, and does nothing past either end", () => {
    const later = reduceDraft(start(), { type: "move", id: "p_1", by: 1 });
    expect(ids(later)).toEqual(["p_2", "p_1", "p_3"]);

    const earlier = reduceDraft(later, { type: "move", id: "p_1", by: -1 });
    expect(ids(earlier)).toEqual(["p_1", "p_2", "p_3"]);

    const first = start();
    expect(reduceDraft(first, { type: "move", id: "p_1", by: -1 })).toBe(first);
    expect(reduceDraft(first, { type: "move", id: "p_3", by: 1 })).toBe(first);
    expect(canMove(first.document.definition.panels, "p_1", -1)).toBe(false);
    expect(canMove(first.document.definition.panels, "p_1", 1)).toBe(true);
  });

  it("steps width through the allowed shares, 3, 4, 6, 8 and 12, and stops at each end", () => {
    let draft = start();
    const widths: number[] = [];
    for (let step = 0; step < 5; step += 1) {
      draft = reduceDraft(draft, { type: "resize", id: "p_1", by: 1 });
      widths.push(draft.document.definition.panels[0].width);
    }
    expect(widths).toEqual([6, 8, 12, 12, 12]);
    expect(canResize(draft.document.definition.panels[0], 1)).toBe(false);

    const narrow = reduceDraft(reduceDraft(start(), { type: "resize", id: "p_1", by: -1 }), {
      type: "resize",
      id: "p_1",
      by: -1,
    });
    expect(narrow.document.definition.panels[0].width).toBe(3);
    expect(canResize(narrow.document.definition.panels[0], -1)).toBe(false);
  });

  it("never makes a chart small, and lets a note be small (FR5)", () => {
    const shorter = reduceDraft(start(), { type: "height", id: "p_1", by: -1 });
    expect(shorter.document.definition.panels[0].height).toBe("medium");
    expect(canChangeHeight(RATE, -1)).toBe(false);

    const taller = reduceDraft(start(), { type: "height", id: "p_1", by: 1 });
    expect(taller.document.definition.panels[0].height).toBe("large");
    expect(canChangeHeight(taller.document.definition.panels[0], 1)).toBe(false);

    expect(canChangeHeight(NOTE, -1)).toBe(false);
    const noteTaller = reduceDraft(start(), { type: "height", id: "p_3", by: 1 });
    expect(noteTaller.document.definition.panels[2].height).toBe("medium");
  });

  it("adds at the end with an id of its own, and refuses past 30 panels", () => {
    const added = reduceDraft(start(), {
      type: "add",
      panel: panel(newPanelId(start().document.definition.panels)),
    });
    expect(ids(added)).toEqual(["p_1", "p_2", "p_3", "p_4"]);

    const full = draftOf(
      documentWith(...Array.from({ length: MAX_PANELS }, (_, index) => panel(`p_${index + 1}`))),
    );
    expect(reduceDraft(full, { type: "add", panel: panel("p_99") })).toBe(full);
    expect(reduceDraft(full, { type: "duplicate", id: "p_1" })).toBe(full);
  });

  it("duplicates beside the original, with a fresh id and the same settings", () => {
    const copied = reduceDraft(start(), { type: "duplicate", id: "p_1" });
    expect(ids(copied)).toEqual(["p_1", "p_4", "p_2", "p_3"]);
    const [, copy] = copied.document.definition.panels;
    expect({ ...copy, id: "p_1" }).toEqual(RATE);
  });

  it("removes a panel, and undo puts it back where it was (FR21)", () => {
    const removed = reduceDraft(start(), { type: "remove", id: "p_2" });
    expect(ids(removed)).toEqual(["p_1", "p_3"]);
    expect(removed.removed?.panel).toEqual(ERRORS);

    const restored = reduceDraft(removed, { type: "undo-remove" });
    expect(ids(restored)).toEqual(["p_1", "p_2", "p_3"]);
    expect(restored.removed).toBeUndefined();
  });

  it("forgets the undo once anything else changes, so it cannot restore into a moved list", () => {
    const removed = reduceDraft(start(), { type: "remove", id: "p_2" });
    const moved = reduceDraft(removed, { type: "move", id: "p_3", by: -1 });
    expect(moved.removed).toBeUndefined();
    expect(reduceDraft(moved, { type: "undo-remove" })).toBe(moved);
  });

  it("replaces a panel's settings in place, keeping its id and size", () => {
    const edited = reduceDraft(start(), {
      type: "replace",
      panel: { ...RATE, settings: { service: "payment", signal: "latency" }, width: 12 },
    });
    expect(edited.document.definition.panels[0].settings).toEqual({
      service: "payment",
      signal: "latency",
    });
    expect(ids(edited)).toEqual(["p_1", "p_2", "p_3"]);
  });

  it("sets the title, description and dashboard-wide settings", () => {
    const named = reduceDraft(start(), { type: "set", changes: { title: "Payments" } });
    const tuned = reduceDraft(named, {
      type: "settings",
      changes: { refresh_seconds: 300, service_picker: true },
    });
    expect(tuned.document.title).toBe("Payments");
    expect(tuned.document.definition.refresh_seconds).toBe(300);
    expect(tuned.document.definition.service_picker).toBe(true);
  });
});

describe("dirty means a value differs from what is stored (AC133)", () => {
  it("is clean when opened, dirty after a change, and clean again when undone by hand", () => {
    const stored = documentWith(RATE, ERRORS, NOTE);
    const opened = draftOf(stored);
    expect(isDirty(opened.document, stored)).toBe(false);

    const moved = reduceDraft(opened, { type: "move", id: "p_1", by: 1 });
    expect(isDirty(moved.document, stored)).toBe(true);

    const back = reduceDraft(moved, { type: "move", id: "p_1", by: -1 });
    expect(isDirty(back.document, stored)).toBe(false);

    const renamed = reduceDraft(opened, { type: "set", changes: { title: "Other" } });
    const renamedBack = reduceDraft(renamed, { type: "set", changes: { title: stored.title } });
    expect(isDirty(renamedBack.document, stored)).toBe(false);
  });

  it("ignores the order of keys, which a server's JSON need not keep", () => {
    const stored = documentWith({ ...RATE, settings: { signal: "rate", service: "checkout" } });
    expect(isDirty(draftOf(documentWith(RATE)).document, stored)).toBe(false);
  });
});

describe("what a definition costs at worst (FR7)", () => {
  it("counts equal reads once and the picker as one service apart from named ones", () => {
    const definition = documentWith(
      RATE,
      ERRORS,
      panel("p_4", { kind: "project_trend", settings: { signal: "rate" } }),
      panel("p_5", { kind: "project_trend", settings: { signal: "errors" } }),
      panel("p_6", { settings: { service: { picker: true }, signal: "rate" } }),
      panel("p_7", { settings: { service: "payment", signal: "rate" } }),
      NOTE,
    ).definition;
    // checkout, the project, the picked service and payment.
    expect(worstCaseReads(definition)).toBe(4);
  });
});

describe("what the live region says (AC37)", () => {
  it("names the panel and where it went", () => {
    const before = start();
    const moved = reduceDraft(before, { type: "move", id: "p_1", by: 1 });
    expect(announce({ type: "move", id: "p_1", by: 1 }, before, moved)).toBe(
      "checkout · Rate moved to position 2 of 3",
    );

    const wider = reduceDraft(before, { type: "resize", id: "p_1", by: 1 });
    expect(announce({ type: "resize", id: "p_1", by: 1 }, before, wider)).toBe(
      "checkout · Rate is now 6 wide",
    );

    const removed = reduceDraft(before, { type: "remove", id: "p_2" });
    expect(announce({ type: "remove", id: "p_2" }, before, removed)).toBe(
      "checkout · Errors removed. Undo available",
    );
    expect(
      announce({ type: "undo-remove" }, removed, reduceDraft(removed, { type: "undo-remove" })),
    ).toBe("checkout · Errors restored at position 2 of 3");
  });

  it("says nothing changed at an end, rather than staying silent", () => {
    const before = start();
    expect(announce({ type: "move", id: "p_1", by: -1 }, before, before)).toBe(
      "checkout · Rate is already first",
    );
    expect(announce({ type: "move", id: "p_3", by: 1 }, before, before)).toBe(
      "Note is already last",
    );
  });
});
