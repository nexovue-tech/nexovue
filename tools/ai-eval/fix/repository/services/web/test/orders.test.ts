import { expect, test } from "vitest";
import { submitOrder } from "../src/api/orders";

test("a total with cents is accepted", () => {
  expect(submitOrder({ total: "12.50", totalCents: 1250 })).toBe(1250);
});
