import { describe, expect, it } from "vitest";

import { plural } from "./plural";

describe("plural", () => {
  it("adds an s to everything but one", () => {
    expect(plural(1, "service")).toBe("service");
    expect(plural(0, "service")).toBe("services");
    expect(plural(2, "service")).toBe("services");
  });

  it("takes the irregular form when there is one", () => {
    expect(plural(1, "node has", "nodes have")).toBe("node has");
    expect(plural(3, "node has", "nodes have")).toBe("nodes have");
  });
});
