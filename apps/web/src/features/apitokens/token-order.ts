import type { ApiTokenSummary } from "./api";

/**
 * Live tokens first, then the dead ones, newest first within each.
 *
 * The API serves them newest first, which put four revoked tokens above the one
 * token that still works (`docs/quality/console-review-2026-09-20.md`). A
 * revoked token is kept on the screen on purpose — it is the record that the
 * credential existed and was withdrawn — but it is history, and history does not
 * lead.
 */
export function orderedTokens(tokens: readonly ApiTokenSummary[]): ApiTokenSummary[] {
  const live = tokens.filter((token) => token.status === "live");
  const rest = tokens.filter((token) => token.status !== "live");
  return [...live, ...rest];
}
