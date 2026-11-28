// What a route reads from its URL, apart from the screen it renders, so the router
// can import it without pulling the screen into the first download (group 9, D3).

/** A service named in the URL, or nothing. A blank string is not a filter. */
export function scopedService(raw: unknown): string | undefined {
  const named = typeof raw === "string" ? raw.trim() : "";
  return named === "" ? undefined : named;
}

/** The longest phrase a link may carry, as chat's tools may search (`MAX_SEARCH_TEXT`). */
export const MAX_SEARCH_PHRASE = 200;

/**
 * A search phrase named in the URL (F4 T3.3: a chat step's `q`), or nothing. Read into the
 * search box on arrival; typing never writes it back, since a history entry per keystroke
 * is not a history.
 */
export function searchPhrase(raw: unknown): string | undefined {
  const phrase = typeof raw === "string" ? raw.trim() : "";
  return phrase === "" || phrase.length > MAX_SEARCH_PHRASE ? undefined : phrase;
}
