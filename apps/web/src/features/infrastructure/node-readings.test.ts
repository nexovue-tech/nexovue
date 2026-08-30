import { describe, expect, it } from "vitest";

import type { NodeSummary } from "./api";
import {
  CPU_METRICS,
  describeMemory,
  explainMissing,
  formatByteRate,
  formatBytes,
  formatCores,
  formatCpu,
  formatPercent,
  isUnnamed,
  MEMORY_METRICS,
  nodeName,
} from "./node-readings";

function node(overrides: Partial<NodeSummary> = {}): NodeSummary {
  return {
    kind: "host",
    host: "web-01",
    container: "",
    cpu_utilization: null,
    cpu_cores_used: null,
    memory_used_bytes: null,
    memory_limit_bytes: null,
    memory_utilization: null,
    filesystem_used_bytes: null,
    filesystem_limit_bytes: null,
    filesystem_utilization: null,
    disk_bytes_per_second: null,
    network_bytes_per_second: null,
    first_seen: "2026-08-30T09:29:55.196Z",
    last_seen: "2026-08-30T09:42:56.449Z",
    signals: [],
    ...overrides,
  };
}

describe("an absent measurement is never a zero", () => {
  it("answers a null byte count with a rule", () => {
    // "0 B" would report an unmonitored host as one using no memory.
    expect(formatBytes(null)).toBe("—");
  });

  it("answers a null rate with a rule", () => {
    expect(formatByteRate(null)).toBe("—");
  });

  it("answers a null fraction with a rule", () => {
    expect(formatPercent(null)).toBe("—");
  });

  it("answers a null core count with a rule", () => {
    expect(formatCores(null)).toBe("—");
  });

  it("still shows a genuine zero as zero", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatPercent(0)).toBe("0.0%");
  });
});

describe("formatBytes", () => {
  it("uses binary prefixes, the way every host tool quotes memory", () => {
    expect(formatBytes(8_600_219_648)).toBe("8.0 GiB");
    expect(formatBytes(5_943_672_832)).toBe("5.5 GiB");
  });

  it("leaves plain bytes unscaled", () => {
    expect(formatBytes(512)).toBe("512 B");
  });

  it("drops the decimal once three digits are already there", () => {
    expect(formatBytes(512 * 1024)).toBe("512 KiB");
  });
});

describe("formatByteRate", () => {
  it("reads as a quantity per second", () => {
    expect(formatByteRate(2048)).toBe("2.0 KiB/s");
  });

  it("marks a floor rather than reporting a trickle as silence", () => {
    expect(formatByteRate(0.3)).toBe("<1 B/s");
  });

  it("shows a real zero as zero", () => {
    expect(formatByteRate(0)).toBe("0 B/s");
  });
});

describe("formatPercent", () => {
  it("turns the wire's fraction into a percentage", () => {
    expect(formatPercent(0.23575)).toBe("23.6%");
  });

  it("marks a floor rather than reporting a busy host as 0.0%", () => {
    expect(formatPercent(0.0002)).toBe("<0.1%");
  });

  it("does not clamp an impossible figure — hiding it is how a wrong number survives", () => {
    expect(formatPercent(11.247)).toBe("1124.7%");
  });
});

describe("formatCpu", () => {
  it("reports a host as a fraction of its CPUs", () => {
    expect(formatCpu(node({ kind: "host", cpu_utilization: 0.236 }))).toBe("23.6%");
  });

  it("reports a container as cores consumed, which is a different quantity", () => {
    expect(formatCpu(node({ kind: "container", container: "checkout", cpu_cores_used: 1.5 }))).toBe(
      "1.50 cores",
    );
  });

  it("never borrows the other kind's number to fill its own column", () => {
    // A container carries no utilization fraction and a host carries no core count.
    expect(formatCpu(node({ kind: "container", container: "c", cpu_utilization: 0.9 }))).toBe("—");
    expect(formatCpu(node({ kind: "host", cpu_cores_used: 2 }))).toBe("—");
  });
});

describe("nodeName", () => {
  it("prefers the container name", () => {
    expect(nodeName(node({ container: "checkout", host: "web-01" }))).toBe("checkout");
  });

  it("falls back to the host name", () => {
    expect(nodeName(node({ host: "web-01" }))).toBe("web-01");
  });

  it("names the unnamed rather than rendering a blank row", () => {
    // Real state, not hypothetical: the host metrics in our own store arrived with
    // no host.name at all, and a blank row would look like a rendering bug.
    const unnamed = node({ host: "", container: "" });

    expect(nodeName(unnamed)).toBe("unnamed node");
    expect(isUnnamed(unnamed)).toBe(true);
  });
});

describe("explainMissing", () => {
  it("says the scraper is off when the node sent none of the metrics", () => {
    const said = explainMissing(node({ signals: ["system.memory.usage"] }), CPU_METRICS);

    expect(said).toContain("not enabled");
    expect(said).toContain("system.cpu.utilization");
  });

  it("says the reading was uninterpretable when the metric did arrive", () => {
    // A different fix entirely: the metric is being sent with attributes we do not
    // recognise, so pointing someone at their scraper config would be wrong.
    const said = explainMissing(node({ signals: ["system.cpu.utilization"] }), CPU_METRICS);

    expect(said).toContain("attributes we do not recognise");
    expect(said).not.toContain("not enabled");
  });
});

describe("describeMemory", () => {
  it("reads as used over total", () => {
    const said = describeMemory(
      node({ memory_used_bytes: 5_943_672_832, memory_limit_bytes: 8_600_219_648 }),
    );

    expect(said).toBe("5.5 GiB used of 8.0 GiB.");
  });

  it("explains the gap when nothing about memory arrived", () => {
    expect(describeMemory(node({ signals: [] }))).toContain("not enabled");
  });

  it("keeps the half it has when only one of the two was reported", () => {
    const said = describeMemory(node({ memory_used_bytes: 1024, memory_limit_bytes: null }));

    expect(said).toBe("1.0 KiB used of —.");
  });
});

describe("MEMORY_METRICS", () => {
  it("names the metrics the memory column is built from", () => {
    expect(MEMORY_METRICS).toContain("system.memory.usage");
  });
});
