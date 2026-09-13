/**
 * What narrows the web-vitals readings.
 *
 * Both are columns of the rollup, so they narrow what is *merged*: the p75 of
 * "mobile only" is the p75 of the mobile readings, not the site's p75 with the
 * desktop rows hidden beside it. A percentile cannot be recovered by filtering a
 * finished number, which is the whole reason these have to reach the store.
 *
 * **There is deliberately no rating filter**, though the audit expected one. A
 * rating is a property of a value and the p75 is a new value — `vitals.ts` says
 * so — which is why the rating a reader sees is computed here from the merged
 * p75 against the Core Web Vitals thresholds. The rollup carries no rating of
 * its own. The raw `web_vitals` table has a per-reading one, and filtering on
 * that would have excluded pages this screen still labels poor: the control and
 * the column disagreeing about the same page.
 */

import type { FilterDefinition, FilterOption } from "@/shared/components/FilterBar";

import type { Breakdowns } from "./RumScreen";

/** What the SDK classifies a device as. Three values, from `device_class`. */
const DEVICE_CLASSES: readonly FilterOption[] = [
  { value: "mobile", label: "Mobile" },
  { value: "tablet", label: "Tablet" },
  { value: "desktop", label: "Desktop" },
];

/**
 * The filters this screen offers, given the pages it has seen.
 *
 * The page list comes from the breakdown that is already loaded, so it names the
 * pages a reader can see rather than every path the site has ever served.
 */
export function rumFilters(breakdowns: Breakdowns): FilterDefinition[] {
  return [
    {
      key: "device_class",
      label: "Device",
      control: { kind: "select", options: DEVICE_CLASSES },
    },
    {
      key: "route",
      label: "Page",
      control: { kind: "select", options: pageOptions(breakdowns) },
    },
  ];
}

function pageOptions(breakdowns: Breakdowns): FilterOption[] {
  const named = (breakdowns.page?.groups ?? []).map((group) => group.key).filter(Boolean);
  return [...new Set(named)].sort().map((page) => ({ value: page, label: page }));
}
