import { describe, expect, it } from "vitest";

import { heldOrder, unseenCount } from "./live-updates";

interface Row {
  name: string;
  errors: number;
}

const row = (name: string, errors = 0): Row => ({ name, errors });
const nameOf = (item: Row) => item.name;
const names = (items: Row[]) => items.map(nameOf);

describe("holding a list still while somebody reads it", () => {
  it("keeps the order already on screen when the ranking moves", () => {
    // The harm this exists for: a customer reaching for the third row finds a
    // different service under the cursor because a refresh landed between
    // deciding and clicking.
    const shown = ["checkout", "cart", "payment"];

    const held = heldOrder(shown, [row("payment"), row("checkout"), row("cart")], nameOf);

    expect(names(held.items)).toEqual(shown);
    expect(held.reorderPending).toBe(true);
  });

  it("still hands over the newest readings", () => {
    // Position is held; values are not. The values are the reading.
    const held = heldOrder(["checkout"], [row("checkout", 41)], nameOf);

    expect(held.items[0].errors).toBe(41);
  });

  it("says nothing is pending when the ranking has not moved", () => {
    const held = heldOrder(["checkout", "cart"], [row("checkout"), row("cart")], nameOf);

    expect(held.reorderPending).toBe(false);
  });

  it("puts a row that was not there at the end rather than into its rank", () => {
    // A new row appearing mid-list pushes everything below it down, which is the
    // same harm by another route.
    const held = heldOrder(
      ["checkout", "cart"],
      [row("arrived"), row("checkout"), row("cart")],
      nameOf,
    );

    expect(names(held.items)).toEqual(["checkout", "cart", "arrived"]);
    expect(held.reorderPending).toBe(true);
  });

  it("drops a row that went away without disturbing the rest", () => {
    const held = heldOrder(
      ["checkout", "cart", "payment"],
      [row("checkout"), row("payment")],
      nameOf,
    );

    expect(names(held.items)).toEqual(["checkout", "payment"]);
  });

  it("takes the incoming order on a first render, when nothing is on screen yet", () => {
    // There is no reader to disturb before anything has been drawn, and holding a
    // list that has never been shown would hold it in no order at all.
    const held = heldOrder([], [row("payment"), row("checkout")], nameOf);

    expect(names(held.items)).toEqual(["payment", "checkout"]);
    expect(held.reorderPending).toBe(false);
  });

  it("does not mutate the list it was handed", () => {
    const incoming = [row("payment"), row("checkout")];

    heldOrder(["checkout", "payment"], incoming, nameOf);

    expect(names(incoming)).toEqual(["payment", "checkout"]);
  });
});

describe("counting what a reader has not been shown", () => {
  it("counts only what is genuinely new", () => {
    expect(unseenCount(["a", "b"], ["c", "d", "a", "b"])).toBe(2);
  });

  it("counts nothing when nothing arrived", () => {
    expect(unseenCount(["a", "b"], ["a", "b"])).toBe(0);
  });

  it("counts nothing on a first load, where everything is new", () => {
    // Opening a feed to "412 new" would be counting the feed itself.
    expect(unseenCount([], ["a", "b", "c"])).toBe(0);
  });
});
