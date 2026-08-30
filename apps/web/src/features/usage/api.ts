import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

import type { DayRange } from "./usage-range";

/**
 * The usage endpoint, unchanged. Field names are the API's.
 *
 * Read for the whole organization and never narrowed to the selected project: the
 * billing unit is distinct services per project (ADR 0056), so one project's slice of
 * it is not a smaller bill — it is a different number, and a customer reading this
 * screen is reading a bill.
 */

export interface UsageCounters {
  spans: number;
  logs: number;
  metric_points: number;
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
  price_per_service_cents: number;
  /** Null is uncapped, which is not the same statement as a cap of zero. */
  max_billed_services: number | null;
}

export interface UsageResponse {
  plan: PlanSummary;
  /** Null until a price row exists for the tier. There is no zero here. */
  price: PlanPrice | null;
  from_day: string;
  to_day: string;
  /** False while any part of the range could still be counted, listed days or not. */
  settled: boolean;
  totals: UsageTotals;
  /** A day with no ingestion is absent rather than zero-filled. */
  days: ProjectDayUsage[];
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
