/**
 * A version as one line: up to `VERSION_SHOWN` characters, the middle elided so its
 * start and its end both stay (F28 design, *Long versions*). Monospace is the caller's.
 * A version is telemetry text, so it is only ever shown as text.
 */

/** About 24 characters fit a table cell and a phone line (a guess, design). */
export const VERSION_SHOWN = 24;
const ELLIPSIS = "…";

export function shortVersion(version: string, shown = VERSION_SHOWN): string {
  const characters = Array.from(version);
  if (characters.length <= shown) return version;
  const kept = shown - ELLIPSIS.length;
  const head = Math.ceil(kept / 2);
  return `${characters.slice(0, head).join("")}${ELLIPSIS}${characters.slice(characters.length - (kept - head)).join("")}`;
}
