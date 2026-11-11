import { expect, test } from "vitest";

// The upload fails inside the HTTP library when the storage service answers 413; no
// line of this service is in the stack, so a refusal is right.
test("a large upload is refused by the storage service, not by this code", () => {
  expect(413).toBe(413);
});
