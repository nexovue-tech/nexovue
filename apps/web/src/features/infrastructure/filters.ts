/**
 * What narrows the node list.
 *
 * All three go to the server. `kind` and `host` are predicates on the series
 * read; `stale_only` is applied after the nodes are assembled, because whether
 * a node has stopped reporting is measured from the interval its own series
 * turned out to have and that does not exist until they are grouped. What keeps
 * that honest is that it runs over every series the window holds rather than
 * over a page of them, and the truncation notice still says when the window
 * itself was cut.
 */

import type { FilterDefinition, FilterOption } from "@/shared/components/FilterBar";

import type { NodeSummary } from "./api";

/* Plural, because these narrow a list rather than describing one row. */
const KIND_LABELS: Record<NodeSummary["kind"], string> = {
  host: "Hosts",
  container: "Containers",
  reporter: "Reporters",
};

const KIND_ORDER: readonly NodeSummary["kind"][] = ["host", "container", "reporter"];

/**
 * The filters this screen offers, given what is on it.
 *
 * The host list is built from the nodes in hand. A host that has gone quiet
 * since a link was shared is not in it, and `FilterBar` keeps the selected value
 * visible anyway, so the control and the results cannot disagree.
 */
export function infrastructureFilters(nodes: readonly NodeSummary[]): FilterDefinition[] {
  return [
    {
      key: "kind",
      label: "Kind",
      control: {
        kind: "select",
        options: KIND_ORDER.map((kind) => ({ value: kind, label: KIND_LABELS[kind] })),
      },
    },
    {
      key: "host",
      label: "Machine",
      control: { kind: "select", options: hostOptions(nodes) },
    },
    {
      key: "stale_only",
      label: "Only stale",
      control: { kind: "toggle", on: "true" },
    },
  ];
}

function hostOptions(nodes: readonly NodeSummary[]): FilterOption[] {
  const named = [...new Set(nodes.map((node) => node.host).filter(Boolean))].sort();
  return named.map((host) => ({ value: host, label: host }));
}
