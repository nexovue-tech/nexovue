import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/**
 * The billing endpoints. Field names are the API's.
 *
 * Every figure about the month comes from the API already computed — this file
 * derives none of them. `usage.billing` in the API and `allowance` in the
 * receiver are the same arithmetic written twice already; a third copy here
 * would be the one that shows a customer a limit nobody enforces.
 */

export interface PlanSummary {
  slug: string;
  name: string;
  raw_retention_days: number;
  derived_retention_days: number;
}

export interface PlanPriceSummary {
  effective_from: string;
  currency: string;
  price_per_service_minor_units: number;
  /** Null on a tier that bills every service it sees. */
  max_billed_services: number | null;
  included_bytes_per_service: number | null;
  browser_share_percent: number | null;
}

export interface SubscriptionSummary {
  plan_slug: string;
  /** `pending`, `active` or `cancelled` — rendered as written. */
  status: string;
  /** ADR 0107's ceiling: the count the customer chose, which sized the mandate. */
  services_committed: number;
  activated_at: string | null;
  cancelled_at: string | null;
}

export interface BillingPeriodUsage {
  first_day: string;
  last_day: string;
  settled: boolean;
  services: number;
  billed_services: number;
  bytes_ingested: number;
  browser_bytes_ingested: number;
  service_cost_minor_units: number | null;
  currency: string | null;
  /** Null when nothing sizes the allowance. Not zero — that would read as none. */
  included_bytes: number | null;
  browser_included_bytes: number | null;
  /** Whether going past the allowance stops ingestion. ADR 0107's sentence. */
  allowance_enforced: boolean;
}

/**
 * What the upgrade form must collect, or null when no processor is configured.
 *
 * Null is a real state rather than an error: the plan and the month stay
 * readable, and the screen says upgrading is unavailable instead of drawing a
 * form whose submit would fail.
 */
export interface CheckoutRequirements {
  /** `name`, `email`, `phone` — whichever this processor declared. */
  required_contact_fields: string[];
}

/** A tier that can be moved to, priced in the currency the overview is quoted in. */
export interface PlanOption {
  slug: string;
  name: string;
  currency: string;
  /** Per service, per month, in minor units. The total is this times the count. */
  price_per_service_minor_units: number;
  included_bytes_per_service: number | null;
  max_billed_services: number | null;
  raw_retention_days: number;
  derived_retention_days: number;
}

export interface BillingOverview {
  plan: PlanSummary;
  price: PlanPriceSummary | null;
  subscription: SubscriptionSummary | null;
  period: BillingPeriodUsage;
  checkout: CheckoutRequirements | null;
  /** What every figure here is stated in. */
  currency: string;
  /** Every currency this tier is priced in, so the screen can offer a switch. */
  available_currencies: string[];
  /** Excludes the current tier and any with no price in `currency`. */
  plans: PlanOption[];
}

/**
 * Keyed by currency, so switching refetches rather than showing the previous
 * currency's figures under a new symbol.
 */
export const billingQueryKey = (currency?: string) => ["billing", currency ?? null] as const;

export function useBillingOverview(currency?: string) {
  return useQuery({
    queryKey: billingQueryKey(currency),
    queryFn: ({ signal }) =>
      apiClient.get<BillingOverview>("billing", {
        searchParams: currency ? { currency } : undefined,
        signal,
      }),
  });
}

export interface StartCheckoutInput {
  planSlug: string;
  /**
   * What the customer is charged in. Chosen by them, never guessed from where
   * they connected — tax and settlement follow the payment instrument, not a
   * network address.
   */
  currency: string;
  /** At least one. The count the customer commits to, which is also the bill. */
  servicesCommitted: number;
  /** Only when the processor declared it needs one. Most collect it themselves. */
  contactEmail?: string;
  contactName?: string;
  contactPhone?: string;
}

interface CheckoutHandoff {
  url: string;
}

/**
 * Answers with somewhere to authorise a mandate and changes no plan.
 *
 * **The URL is a bearer credential** — whoever holds it can authorise the
 * mandate — so it is navigated to and never stored, logged or put in a query
 * cache. The plan moves when the processor says the mandate became active, not
 * when this returns.
 */
export function useStartCheckout() {
  return useMutation({
    mutationKey: ["billing", "checkout"],
    mutationFn: (input: StartCheckoutInput) =>
      apiClient.post<CheckoutHandoff>("billing/checkout", {
        plan_slug: input.planSlug,
        currency: input.currency,
        services_committed: input.servicesCommitted,
        ...(input.contactEmail ? { contact_email: input.contactEmail } : {}),
        ...(input.contactName ? { contact_name: input.contactName } : {}),
        ...(input.contactPhone ? { contact_phone: input.contactPhone } : {}),
      }),
  });
}

/**
 * 202, not 204: the arrangement ends when the processor says it has, at its own
 * period boundary. The overview is invalidated so the row's new status is read
 * back rather than assumed.
 */
export function useCancelSubscription() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["billing", "cancel"],
    mutationFn: () => apiClient.delete<void>("billing/subscription"),
    // Every currency's copy: the arrangement changed, not one view of it.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["billing"] }),
  });
}
