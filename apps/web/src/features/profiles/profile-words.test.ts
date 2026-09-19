import { describe, expect, it } from "vitest";

import { formatShare, formatValue, typeLabel, typeName, typePhrase } from "./profile-words";

describe("profile types and values in people's words", () => {
  it("names the known types and shows an unknown one as sent, never guessed at", () => {
    expect(typeName("inuse_space")).toBe("Memory in use");
    expect(typeName("samples")).toBe("CPU samples");
    expect(typeName("wakeups")).toBe("wakeups");
  });

  it("says what a type is measured in beside its name", () => {
    expect(typeLabel("cpu", "nanoseconds")).toBe("CPU time (ns)");
    expect(typeLabel("alloc_space", "bytes")).toBe("Allocated memory (bytes)");
    expect(typeLabel("samples", "count")).toBe("CPU samples (count)");
    expect(typeLabel("custom", "")).toBe("custom");
  });

  it("writes a value in its own unit", () => {
    expect(formatValue(2_500_000_000, "nanoseconds")).toBe("2.5 s");
    expect(formatValue(3 * 1024 * 1024, "bytes")).toBe("3.0 MiB");
    expect(formatValue(12_345, "count")).toBe("12,345");
    expect(formatValue(7, "widgets")).toBe("7 widgets");
  });

  it("writes a share with a decimal only while it is small, and no share of nothing", () => {
    expect(formatShare(5, 100)).toBe("5.0 %");
    expect(formatShare(40, 100)).toBe("40 %");
    expect(formatShare(1, 0)).toBe("—");
  });

  it("writes a type inside a sentence without breaking its acronym", () => {
    expect(typePhrase("cpu")).toBe("CPU time");
    expect(typePhrase("inuse_space")).toBe("memory in use");
    expect(typePhrase("wakeups")).toBe("wakeups");
  });
});
