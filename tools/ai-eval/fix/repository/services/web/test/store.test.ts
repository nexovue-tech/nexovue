import { expect, test } from "vitest";
import { itemCount } from "../src/cart/store";

test("an empty cart counts zero items", () => {
  expect(itemCount({ id: "c1" })).toBe(0);
});
