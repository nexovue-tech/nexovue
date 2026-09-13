/**
 * Carrying "which service" from one screen to the next (IA1).
 *
 * A person who has found the failing service should not have to name it again
 * on the next screen. Today they do: every screen starts unscoped, so the
 * journey from a service row to its traces, its logs and its errors is three
 * searches for a word already on the screen behind them.
 *
 * **The scope travels in the link, not in a store.** A sticky global scope
 * would be one more piece of hidden state: a reader who looked at `checkout`
 * this morning would find Logs mysteriously filtered this afternoon, with
 * nothing on the screen to say why, and no way to hand the view to a colleague.
 * A link carries it visibly, lands in the address bar, and can be shared — the
 * same rule the filters follow.
 *
 * Each screen names its own parameter, and they do not agree: traces and logs
 * call it `service`, error groups call it `service_name`. That disagreement is
 * the API's, and this is where it stops — a screen linking to another should
 * not have to know the other's spelling.
 */

/** Where a scoped link can go. Each entry is a route that filters by service. */
export type ScopedDestination = "traces" | "logs" | "errors";

const SERVICE_PARAMETER: Record<ScopedDestination, string> = {
  traces: "service",
  logs: "service",
  errors: "service_name",
};

export const SCOPED_PATHS: Record<ScopedDestination, string> = {
  traces: "/traces",
  logs: "/logs",
  errors: "/errors",
};

/**
 * The search object a destination needs to arrive already narrowed.
 *
 * An empty or missing service produces no parameters rather than an empty one:
 * `?service=` reads as a filter and is not one, and it would put the
 * destination into its filtered-empty state for a filter nobody applied.
 */
export function scopedSearch(
  destination: ScopedDestination,
  serviceName: string | undefined,
): Record<string, string> {
  const named = serviceName?.trim();
  if (!named) return {};
  return { [SERVICE_PARAMETER[destination]]: named };
}
