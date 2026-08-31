import { describe, expect, it } from "vitest";

import type { ServiceEdge } from "./api";
import { collapseAddressPeers, hostGroupName, isAddressLiteral } from "./peer-hosts";

function edge(overrides: Partial<ServiceEdge> = {}): ServiceEdge {
  return {
    caller: "checkout",
    callee: "(172.19.0.11)",
    edge_kind: "call",
    callee_kind: "grpc",
    call_count: 3,
    error_count: 0,
    calls_per_minute: 0.3,
    error_rate: 0,
    p50_ms: 1,
    p95_ms: 2,
    p99_ms: 3,
    ...overrides,
  };
}

describe("isAddressLiteral", () => {
  it("recognises a dotted quad, which a hostname can never be", () => {
    // A DNS name's rightmost label may not be all-numeric (RFC 1123 §2.1).
    expect(isAddressLiteral("172.19.0.11")).toBe(true);
    expect(isAddressLiteral("10.0.0.1")).toBe(true);
    expect(isAddressLiteral("0.0.0.0")).toBe(true);
    expect(isAddressLiteral("255.255.255.255")).toBe(true);
  });

  it("recognises IPv6 in every spelling server.address arrives in", () => {
    expect(isAddressLiteral("::1")).toBe(true);
    expect(isAddressLiteral("fe80::1")).toBe(true);
    expect(isAddressLiteral("2001:db8::8a2e:370:7334")).toBe(true);
    expect(isAddressLiteral("[::1]")).toBe(true);
    expect(isAddressLiteral("[2001:db8::1]:5432")).toBe(true);
    expect(isAddressLiteral("::ffff:10.0.0.1")).toBe(true);
  });

  it("takes an address with a port, because server.address sometimes carries one", () => {
    expect(isAddressLiteral("10.0.0.5:5432")).toBe(true);
  });

  it("leaves alone a hostname that merely looks numeric", () => {
    // Each of these is the reason the rule is four labels rather than "has digits".
    expect(isAddressLiteral("db2.example.com")).toBe(false);
    expect(isAddressLiteral("10.0.0.1.internal")).toBe(false);
    expect(isAddressLiteral("1.2.3")).toBe(false);
    expect(isAddressLiteral("1.2.3.4.5")).toBe(false);
    expect(isAddressLiteral("999.1.1.1")).toBe(false);
    expect(isAddressLiteral("valkey-cart")).toBe(false);
    expect(isAddressLiteral("kafka:9092")).toBe(false);
    expect(isAddressLiteral("")).toBe(false);
  });

  it("does not read a short number as an address, unlike the URL parser", () => {
    // Measured: `new URL("http://1").hostname` is "0.0.0.1" and `http://1.2.3` is
    // "1.2.0.3" — the URL host parser invents octets, which is why it was rejected
    // for this job. A one-label name is a name here.
    expect(isAddressLiteral("1")).toBe(false);
  });

  it("takes a padded quad as the address it is, without rewriting it", () => {
    // Four all-numeric labels cannot be a hostname whatever the padding. We only
    // classify: the URL parser would rewrite this to 8.1.1.1, reading 010 as octal,
    // and a display layer must never restate an address as a different address.
    expect(isAddressLiteral("010.1.1.1")).toBe(true);
  });

  it("misses an octal quad wider than three digits, and says so rather than guessing", () => {
    // `0300.1.1.1` is 192.1.1.1 to an octal-aware resolver. The rule is decimal
    // octets, so this one keeps its own node — the honest status quo, not a wrong
    // merge. Deliberate: widening the rule to reach it would start accepting labels
    // that a hostname could legitimately use.
    expect(isAddressLiteral("0300.1.1.1")).toBe(false);
  });
});

describe("hostGroupName", () => {
  it("names the group by its technology, so a customer reads a dependency", () => {
    expect(hostGroupName("postgresql")).toBe("(postgresql hosts)");
    expect(hostGroupName("grpc")).toBe("(grpc hosts)");
  });

  it("still says hosts when no caller named a technology", () => {
    expect(hostGroupName("")).toBe("(unnamed hosts)");
  });

  it("keeps the parentheses that mark a name we were told", () => {
    // `isPlaceholderService` reads them, which is what keeps the node unlinked.
    expect(hostGroupName("redis").startsWith("(")).toBe(true);
    expect(hostGroupName("redis").endsWith(")")).toBe(true);
  });

  it("carries no count, because the name is also the node's identity", () => {
    expect(hostGroupName("grpc")).not.toMatch(/[0-9]/);
  });
});

describe("collapseAddressPeers", () => {
  it("draws two addresses of one technology as one dependency", () => {
    // The live defect: checkout -> (172.19.0.11) and checkout -> (172.19.0.19).
    const { edges, groups } = collapseAddressPeers([
      edge({ callee: "(172.19.0.11)", call_count: 3 }),
      edge({ callee: "(172.19.0.19)", call_count: 5 }),
    ]);

    expect(edges).toHaveLength(1);
    expect(edges[0].callee).toBe("(grpc hosts)");
    expect(groups.get("(grpc hosts)")?.addresses).toEqual(["172.19.0.11", "172.19.0.19"]);
  });

  it("sums the counts of the edges it merged, so no call is lost", () => {
    const { edges } = collapseAddressPeers([
      edge({ callee: "(172.19.0.11)", call_count: 3, error_count: 0, calls_per_minute: 0.3 }),
      edge({ callee: "(172.19.0.19)", call_count: 5, error_count: 1, calls_per_minute: 0.5 }),
    ]);

    expect(edges[0].call_count).toBe(8);
    expect(edges[0].error_count).toBe(1);
    expect(edges[0].calls_per_minute).toBeCloseTo(0.8);
    // Recomputed against the merged total, never averaged from the two rates.
    expect(edges[0].error_rate).toBe(1 / 8);
  });

  it("reports the worst latency of the group rather than an invented average", () => {
    const { edges } = collapseAddressPeers([
      edge({ callee: "(10.0.0.1)", p50_ms: 1, p95_ms: 2, p99_ms: 3 }),
      edge({ callee: "(10.0.0.2)", p50_ms: 9, p95_ms: 40, p99_ms: 50 }),
    ]);

    expect([edges[0].p50_ms, edges[0].p95_ms, edges[0].p99_ms]).toEqual([9, 40, 50]);
  });

  it("keeps two technologies apart, because they are two dependencies", () => {
    const { edges, groups } = collapseAddressPeers([
      edge({ callee: "(172.19.0.11)", callee_kind: "postgresql" }),
      edge({ callee: "(172.19.0.19)", callee_kind: "redis" }),
    ]);

    expect(edges.map((one) => one.callee).sort()).toEqual(["(postgresql hosts)", "(redis hosts)"]);
    expect(groups.size).toBe(2);
  });

  it("keeps a call and a queue to the same group as two edges", () => {
    // Two React Flow edges sharing an id silently become one, which is the bug the
    // edge id already carries a comment about.
    const { edges } = collapseAddressPeers([
      edge({ callee: "(10.0.0.1)", edge_kind: "call" }),
      edge({ callee: "(10.0.0.2)", edge_kind: "queue" }),
    ]);

    expect(edges).toHaveLength(2);
    expect(new Set(edges.map((one) => `${one.caller}${one.callee}${one.edge_kind}`)).size).toBe(2);
  });

  it("keeps two callers' groups apart, so the group is never a shared hub", () => {
    const { edges } = collapseAddressPeers([
      edge({ caller: "checkout", callee: "(10.0.0.1)" }),
      edge({ caller: "frontend", callee: "(10.0.0.2)" }),
    ]);

    expect(edges).toHaveLength(2);
  });

  it("leaves a peer that was named rather than addressed exactly as it was", () => {
    const named = [
      edge({ callee: "(valkey-cart)", callee_kind: "redis" }),
      edge({ callee: "(uninstrumented)", callee_kind: "" }),
      edge({ callee: "payments", callee_kind: "" }),
    ];

    const { edges, groups } = collapseAddressPeers(named);

    expect(edges).toEqual(named);
    expect(groups.size).toBe(0);
  });

  it("collapses a single address too, so a node does not rename itself later", () => {
    const { edges, groups } = collapseAddressPeers([edge({ callee: "(172.19.0.11)" })]);

    expect(edges[0].callee).toBe("(grpc hosts)");
    expect(groups.get("(grpc hosts)")?.addresses).toEqual(["172.19.0.11"]);
  });

  it("counts one address once however many edges named it", () => {
    const { groups } = collapseAddressPeers([
      edge({ caller: "checkout", callee: "(10.0.0.1)", edge_kind: "call" }),
      edge({ caller: "checkout", callee: "(10.0.0.1)", edge_kind: "queue" }),
    ]);

    expect(groups.get("(grpc hosts)")?.addresses).toEqual(["10.0.0.1"]);
  });
});
