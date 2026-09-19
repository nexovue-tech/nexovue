import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

import type { DayRange } from "./usage-range";

/**
 * The usage endpoint, unchanged. Field names are the API's.
 *
 * Read for the whole organization and never narrowed to the selected project: the
 * billing unit is distinct services per project, so one project's slice of
 * it is not a smaller bill — it is a different number, and a customer reading this
 * screen is reading a bill.
 */

export interface UsageCounters {
  spans: number;
  logs: number;
  metric_points: number;
  /** Counted and shown; not billed while profiling is in preview (F17, D1). */
  profile_samples: number;
  bytes_ingested: number;
}

export interface ProjectDayUsage extends UsageCounters {
  project_id: string;
  /** `YYYY-MM-DD`, in UTC — the day the meter counts in. */
  day: string;
  /** Distinct services this project sent something for on this day. */
  services: number;
  /** False while the meter has not finished this day, so the row can still grow. */
  settled: boolean;
}

export interface UsageTotals extends UsageCounters {
  /** Distinct services across the whole range: a union, never a sum of the days. */
  services: number;
}

export interface PlanSummary {
  slug: string;
  name: string;
  raw_retention_days: number;
  derived_retention_days: number;
}

export interface PlanPrice {
  effective_from: string;
  /** ISO 4217. */
  currency: string;
  /** Minor units of that currency — whole, because money in binary floats rounds. */
  price_per_service_minor_units: number;
  /** Null is uncapped, which is not the same statement as a cap of zero. */
  max_billed_services: number | null;
  /** Bytes each billed service brings per month. Null is "none recorded". */
  included_bytes_per_service: number | null;
}

/** The calendar month a bill covers — not the range of days being browsed. */
export interface BillingPeriod {
  first_day: string;
  last_day: string;
  /** False while the meter has not counted past the end of the month. */
  settled: boolean;
  /** Distinct services over the month: a union, never a sum of the days. */
  services: number;
  /** The same after the plan's ceiling. Equal to `services` when uncapped. */
  billed_services: number;
  bytes_ingested: number;
  /** Minor units of `currency`. Null when no price is recorded — never a zero. */
  service_cost_minor_units: number | null;
  currency: string | null;
  /** The month's pooled allowance in bytes. Null when the rate records none. */
  included_bytes: number | null;
  /**
   * True when going past `included_bytes` refuses further ingest until the 1st.
   * The API decides it — which tiers are capped is policy, and working it out
   * from the price row here would be a second place for it to be wrong.
   */
  allowance_enforced: boolean;
}

export interface ServiceNameFamily {
  /** The name these would share if the identifier came off the end. */
  stem: string;
  /** `uuid`, `hex`, `kubernetes-pod` or `ordinal`. */
  kind: string;
  members: number;
  examples: string[];
}

export interface CardinalityWarning {
  project_id: string;
  services_now: number;
  services_before: number;
  window_days: number;
  /** Empty when the count moved for a reason the API could not name. */
  families: ServiceNameFamily[];
}

export interface UsageResponse {
  plan: PlanSummary;
  /** Null until a price row exists for the tier. There is no zero here. */
  price: PlanPrice | null;
  period: BillingPeriod;
  from_day: string;
  to_day: string;
  /** False while any part of the range could still be counted, listed days or not. */
  settled: boolean;
  totals: UsageTotals;
  /** A day with no ingestion is absent rather than zero-filled. */
  days: ProjectDayUsage[];
  /** Empty for almost every organization, which is the intended state. */
  cardinality: CardinalityWarning[];
}

export const usageQueryKey = (range: DayRange) => ["usage", range.from, range.to] as const;

export function useUsage(range: DayRange) {
  return useQuery({
    queryKey: usageQueryKey(range),
    queryFn: ({ signal }) =>
      apiClient.get<UsageResponse>("usage", {
        searchParams: { from: range.from, to: range.to },
        signal,
      }),
  });
}
