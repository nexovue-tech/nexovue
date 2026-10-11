// What a route reads from its URL, apart from the screen it renders, so the router
// can import it without pulling the screen into the first download (group 9, D3).

/** A service named in the URL, or nothing. A blank string is not a filter. */
export function scopedService(raw: unknown): string | undefined {
  const named = typeof raw === "string" ? raw.trim() : "";
  return named === "" ? undefined : named;
}
