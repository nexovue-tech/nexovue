import { describe, expect, it } from "vitest";

/**
 * The staff console's boundary (F31, ADR-016), read from source like the other
 * structural tests: it imports only `shared/`, never a feature or the customer app, and
 * it talks only to `/admin/v1`.
 */

const SOURCES: Record<string, string> = import.meta.glob("/src/admin/**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
});

describe("the admin console's boundary", () => {
  it("has sources to judge", () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(2);
  });

  it.each(Object.entries(SOURCES))("%s imports nothing from a feature or the app", (_, text) => {
    expect(text).not.toMatch(/from "@\/(features|app)\//);
    expect(text).not.toMatch(/from "\.\.\/(features|app)\//);
  });

  it("never calls the customer API", () => {
    for (const text of Object.values(SOURCES)) expect(text).not.toContain("/api/v1");
  });
});
