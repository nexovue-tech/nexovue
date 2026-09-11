/**
 * A service's peers: what an edge of the dependency graph is, how a peer that never
 * reported for itself is named, and how the ones named by a bare address collapse.
 *
 * Shared rather than owned by a screen because two screens draw the same edges. The
 * map draws them as a graph and the service detail panel as two lists, and when only
 * one of them collapsed addresses the same pair of peers was one dependency on one
 * screen and two on the other.
 */

/**
 * The two kinds of dependency the pipeline records, spelled as the API spells them.
 * A queue hop can be retried for hours; a direct call fails the caller now, so they
 * are never drawn as the same arrow.
 */
export const EDGE_CALL = "call";
export const EDGE_QUEUE = "queue";

/** One edge of `GET /service-map`. Field names are the API's, unchanged. */
export interface ServiceEdge {
  caller: string;
  callee: string;
  /** `call` or `queue`; anything else is read as a call. */
  edge_kind: string;
  /**
   * The technology the caller says it reached this callee over — `redis`,
   * `postgresql`, `kafka`, `grpc`. Empty whenever the callee reported for itself.
   */
  callee_kind: string;
  call_count: number;
  error_count: number;
  calls_per_minute: number;
  /** A fraction, not a percentage: 0.05 is one call in twenty. */
  error_rate: number;
  /**
   * The caller's own span. On a `call` that is the request it waited on, network
   * included; on a `queue` it is the publish, not the delivery lag — the producer
   * and the consumer keep different clocks.
   */
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

/**
 * A service we can only see from the outside: something calls it, it sends us nothing.
 *
 * The test is the parentheses, not a fixed list. A callee that never reported for
 * itself is named from what the caller's own span said about its peer —
 * `(valkey-cart)`, `(postgresql)`, `(kafka)` — so the set of these names is open, and
 * a real `service.name` is a resource attribute that cannot contain parentheses.
 */
export function isPlaceholderService(name: string): boolean {
  return name.startsWith("(") && name.endsWith(")");
}

/** Four dot-separated decimal octets, nothing else. */
const IPV4_OCTETS = 4;
const IPV4_MAX_OCTET = 255;

/**
 * A `host:port` carries one colon; every IPv6 spelling carries at least two. A DNS
 * hostname can carry none at all, which is what makes the count decisive.
 */
const MIN_IPV6_COLONS = 2;

/** Hex groups, the `::` elision, and the dotted tail of an IPv4-mapped address. */
const IPV6_CHARACTERS = /^[0-9A-Fa-f:.]+$/;

/**
 * Whether a peer name is a bare network address rather than a name.
 *
 * Deliberately strict, because the cost of a false positive is collapsing a real named
 * dependency into the host group. The rules are the two that a DNS hostname provably
 * cannot satisfy:
 *
 *   - IPv4: exactly four all-digit labels, each 0-255. A hostname's rightmost label may
 *     not be all-numeric (RFC 1123 §2.1), so a dotted quad is never a hostname. This
 *     rejects `1.2.3`, `1.2.3.4.5`, `999.1.1.1` and `10.0.0.1.internal`, and it leaves
 *     `db2.example.com` alone.
 *   - IPv6: a colon cannot appear in a hostname at all, so two or more of them settle it.
 *
 * The WHATWG URL parser was measured as the alternative and rejected: it reads
 * `http://1` as `0.0.0.1` and `http://010.1.1.1` as `8.1.1.1` — it would call a short
 * numeric hostname an address — and it throws on the unbracketed IPv6 that
 * `server.address` actually carries.
 */
export function isAddressLiteral(name: string): boolean {
  const host = withoutBrackets(name.trim());
  return isIpv6(host) || isIpv4(withoutPort(host));
}

/** `[2001:db8::1]` and `[::1]:8080` both name the address inside the brackets. */
function withoutBrackets(host: string): string {
  if (!host.startsWith("[")) return host;
  const close = host.indexOf("]");
  return close < 0 ? host : host.slice(1, close);
}

/** A single trailing `:1234`. Only ever stripped from an IPv4 candidate. */
function withoutPort(host: string): string {
  const colon = host.lastIndexOf(":");
  if (colon < 0) return host;
  const port = host.slice(colon + 1);
  return isDigits(port) ? host.slice(0, colon) : host;
}

function isIpv4(host: string): boolean {
  const octets = host.split(".");
  if (octets.length !== IPV4_OCTETS) return false;
  return octets.every(
    (octet) => isDigits(octet) && octet.length <= 3 && Number(octet) <= IPV4_MAX_OCTET,
  );
}

function isIpv6(host: string): boolean {
  const colons = host.split(":").length - 1;
  return colons >= MIN_IPV6_COLONS && IPV6_CHARACTERS.test(host);
}

function isDigits(value: string): boolean {
  return value.length > 0 && /^[0-9]+$/.test(value);
}

/**
 * What one group of collapsed addresses is called.
 *
 * Parentheses, because that is already how this codebase marks a name we were told
 * rather than one that reported for itself — `isPlaceholderService` reads them, so the
 * group keeps its dashed, italic, unlinked styling without knowing about this rule.
 *
 * "hosts" rather than the count, because the name is also the map node's identity: a
 * name carrying "3" would become a different node the moment a fourth instance
 * appeared. The count is data beside the name instead, which is where a label belongs.
 */
export function hostGroupName(peerKind: string): string {
  return peerKind ? `(${peerKind} hosts)` : "(unnamed hosts)";
}

/** One collapsed group: the addresses it stands for. */
export interface HostGroup {
  /** Sorted, so the hover text does not reshuffle while the window advances. */
  addresses: string[];
}

export interface CollapsedEdges {
  edges: ServiceEdge[];
  /** Keyed by the group's name. Empty when no peer was named by an address. */
  groups: Map<string, HostGroup>;
}

/**
 * The edge list with address-named peers folded into one peer per technology.
 *
 * A callee that never reported for itself is named from what the caller's own span
 * asserted, and sometimes that assertion is an IP. Measured live against the
 * OpenTelemetry Demo, `checkout` reached `(172.19.0.11)` and `(172.19.0.19)` — one
 * dependency wearing two names, and two more after the next restart.
 *
 * The stored row keeps the address, because storage should hold the most specific truth
 * and presentation can always aggregate upward while it can never recover detail it
 * discarded. So the collapse lives here, in the drawing, and not in the API: an API
 * that merged them would throw the addresses away for every consumer, and it would also
 * have to invent a latency, because two merged `quantiles` states cannot be re-merged
 * from the percentiles they were already reduced to.
 *
 * The group is per *technology*, so two datastores reached over different protocols
 * stay two peers; only addresses of the same kind — which is the case that was one
 * dependency all along — become one.
 *
 * Edges that now share a caller, a callee and a kind are merged, because two React Flow
 * edges sharing an id silently become one — the counts sum, and the latency reported is
 * the worst of them. That is the one honest reduction available: percentiles that have
 * already been merged cannot be merged again, so the alternative would be to average
 * them, which would state a number no instance ever measured.
 */
export function collapseAddressPeers(edges: readonly ServiceEdge[]): CollapsedEdges {
  const groups = new Map<string, HostGroup>();
  const merged = new Map<string, ServiceEdge>();

  for (const edge of edges) {
    const callee = groupFor(edge, groups);
    const key = `${edge.caller} ${callee} ${edge.edge_kind}`;
    const held = merged.get(key);
    merged.set(key, held ? mergeEdges(held, edge) : { ...edge, callee });
  }

  for (const group of groups.values()) group.addresses.sort();
  return { edges: [...merged.values()], groups };
}

/** The peer this edge's callee belongs to, recording the address it stood for. */
function groupFor(edge: ServiceEdge, groups: Map<string, HostGroup>): string {
  const address = addressOf(edge.callee);
  if (address === undefined) return edge.callee;

  const name = hostGroupName(edge.callee_kind);
  const group = groups.get(name) ?? { addresses: [] };
  if (!group.addresses.includes(address)) group.addresses.push(address);
  groups.set(name, group);
  return name;
}

/** The address inside a reserved peer name, when that is what the name holds. */
function addressOf(callee: string): string | undefined {
  if (!isPlaceholderService(callee)) return undefined;
  const inner = callee.slice(1, -1);
  return isAddressLiteral(inner) ? inner : undefined;
}

function mergeEdges(held: ServiceEdge, next: ServiceEdge): ServiceEdge {
  const callCount = held.call_count + next.call_count;
  const errorCount = held.error_count + next.error_count;

  return {
    ...held,
    // Empty only if both were, so the group keeps the technology that named it.
    callee_kind: held.callee_kind || next.callee_kind,
    call_count: callCount,
    error_count: errorCount,
    calls_per_minute: held.calls_per_minute + next.calls_per_minute,
    error_rate: callCount > 0 ? errorCount / callCount : 0,
    // The worst of the group. True of at least one instance, which an average is not.
    p50_ms: Math.max(held.p50_ms, next.p50_ms),
    p95_ms: Math.max(held.p95_ms, next.p95_ms),
    p99_ms: Math.max(held.p99_ms, next.p99_ms),
  };
}
